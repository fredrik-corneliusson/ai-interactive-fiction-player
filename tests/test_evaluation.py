import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from click.testing import CliRunner
from langchain_core.messages import AIMessage

from evaluation import CampaignBudget, EvaluationResponse, FixtureEngine, attempt_cost, campaign_usage, load_cases, run_trial, score_response, summarize
from memory import MemoryBatch
from player import BudgetExceeded, Player, Settings
from run_store import RunStore
from scripts.evaluate import cli
from test_player import engine


def response(parsed):
    return {'raw': AIMessage(content=[], response_metadata={'status': 'completed', 'model_name': 'gpt-6-luna'},
                             usage_metadata={'input_tokens': 20, 'output_tokens': 10, 'total_tokens': 30}),
            'parsed': parsed, 'parsing_error': None}


class EvaluationTests(unittest.TestCase):
    def test_fixture_choice_replay_is_fixed_but_final_decision_validation_is_independent(self):
        fixture = load_cases()[-1]
        engine = FixtureEngine(fixture)
        for turn in fixture['turns'][:-1]:
            self.assertEqual(engine.next(), turn['observation'])
            engine.next(turn['selected_choice'])
        engine.validate_choice(2)
        with self.assertRaises(ValueError):
            engine.next(2)
        self.assertEqual(engine.next(), fixture['turns'][-1]['observation'])

    def test_scoring_requires_correct_answer_and_visible_relevant_exact_citation(self):
        probe = {'id': 'name', 'answer_groups': [['Verona']], 'support': [{'turns': [0], 'terms': [['Verona']]}]}
        source = {'source_id': 1, 'turn': 0, 'observation': 'Pizzeria Verona.', 'action': '', 'character_comment': ''}
        parsed = {'choice': 1, 'character_comment': 'Jag går vidare.', 'answers': [
            {'id': 'name', 'status': 'known', 'answer': 'Verona', 'evidence': [{'source_id': 1, 'channel': 'observation', 'quote': 'Verona'}]}]}
        answer = EvaluationResponse.model_validate(parsed)
        self.assertTrue(score_response(answer, [probe], [source], {1: source})[0]['passed'])
        self.assertFalse(score_response(answer, [probe], [source], {})[0]['passed'])
        answer.answers[0].evidence[0].quote = 'Invented Verona'
        self.assertTrue(score_response(answer, [probe], [source], {1: source})[0]['unsupported_known'])
        with self.assertRaises(ValueError):
            score_response(answer, [dict(probe, id='different')], [source], {})

    def test_unknown_questions_reject_claims_even_with_real_but_irrelevant_evidence(self):
        probe = {'id': 'secret', 'answer_groups': [], 'support': []}
        parsed = {'choice': 1, 'character_comment': 'Jag går vidare.', 'answers': [
            {'id': 'secret', 'status': 'unknown', 'answer': 'Jag vet inte.', 'evidence': []}]}
        answer = EvaluationResponse.model_validate(parsed)
        self.assertTrue(score_response(answer, [probe], [], {})[0]['passed'])
        answer.answers[0].status = 'known'
        self.assertFalse(score_response(answer, [probe], [], {})[0]['passed'])

    def test_contradiction_probe_requires_both_sources(self):
        probe = {'id': 'conflict', 'answer_groups': [['här'], ['flytt']],
                 'support': [{'turns': [0], 'terms': [['här']]}, {'turns': [3], 'terms': [['flyttat']]}]}
        sources = [{'source_id': 1, 'turn': 0, 'observation': 'Jenny bor här.'},
                   {'source_id': 2, 'turn': 3, 'observation': 'Jenny har flyttat.'}]
        parsed = {'choice': 1, 'character_comment': 'Jag går vidare.', 'answers': [
            {'id': 'conflict', 'status': 'known', 'answer': 'Jenny sade här, portvakten säger flyttat.', 'evidence': [
                {'source_id': s['source_id'], 'channel': 'observation', 'quote': s['observation']} for s in sources]}]}
        answer = EvaluationResponse.model_validate(parsed)
        visible = {s['source_id']: s for s in sources}
        self.assertTrue(score_response(answer, [probe], sources, visible)[0]['passed'])
        answer.answers[0].evidence.pop()
        self.assertFalse(score_response(answer, [probe], sources, visible)[0]['passed'])

    def test_protocol_keeps_oracles_out_of_prompts_and_costs_include_extraction(self):
        calls = []
        case = load_cases()[-1]
        def factory(settings, *, schema):
            def invoke(messages):
                calls.append((schema, [m.content for m in messages]))
                parsed = {'records': []} if schema is MemoryBatch else {
                    'choice': 2, 'character_comment': 'Jag går vidare.', 'answers': [
                        {'id': p['id'], 'status': 'unknown', 'answer': 'Jag vet inte.', 'evidence': []} for p in case['probes']]}
                return response(parsed)
            return Mock(invoke=invoke)
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / 'runs.sqlite3')
            try:
                full = run_trial(store, case, Settings(retries=0), 1, model_factory=factory)
                hybrid = run_trial(store, case, Settings(memory_mode='hybrid', retries=0), 1, model_factory=factory)
                self.assertEqual(full['status'], 'completed')
                self.assertEqual(hybrid['status'], 'completed')
                self.assertEqual(full['usage']['requests'], 1)
                self.assertEqual(hybrid['usage']['requests'], 3)
                self.assertEqual(hybrid['usage']['known_tokens'], 90)
                self.assertEqual(len(hybrid['scores']), 3)
                self.assertEqual(hybrid['response']['choice'], 2)
                for schema, messages in calls:
                    text = str(messages)
                    self.assertNotIn('answer_groups', text)
                    self.assertNotIn('support_match', text)
                    if schema is MemoryBatch:
                        self.assertNotIn('Minnesfrågor', text)
                        self.assertNotIn('Vilken ny exakt adress', text)
                self.assertGreater(hybrid['estimated_usd'], full['estimated_usd'])
            finally:
                store.close()

    def test_campaign_limits_prevent_request_reservation_across_separate_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / 'runs.sqlite3', request_guard=CampaignBudget(requests=1))
            try:
                for index in range(2):
                    model = Mock()
                    model.invoke.return_value = response({'choice': 1, 'character_comment': 'Nu går jag.'})
                    player = Player(engine(), model=model)
                    store.create(player)
                    if index == 0:
                        player.step()
                    else:
                        with self.assertRaises(BudgetExceeded):
                            player.step()
                        model.invoke.assert_not_called()
                self.assertEqual(campaign_usage(store)['requests'], 1)
            finally:
                store.close()

    def test_cost_limit_and_unknown_usage_reservations_are_conservative(self):
        start = {'model': 'gpt-6-luna', 'input_estimate': 1000, 'max_output_tokens': 100, 'token_reservation': 1100}
        cost, unknown = attempt_cost(start)
        self.assertAlmostEqual(cost, 0.000175)
        self.assertTrue(unknown)
        cost, unknown = attempt_cost(start, {'usage': {'input_tokens': 100, 'output_tokens': 20}})
        self.assertAlmostEqual(cost, 0.00002)
        self.assertFalse(unknown)
        cost, unknown = attempt_cost(start, {'usage': {'input_tokens': 1000, 'output_tokens': 20,
                                                      'input_token_details': {'cache_read': 200, 'cache_creation': 300}}})
        self.assertAlmostEqual(cost, 0.0000995)
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / 'runs.sqlite3', request_guard=CampaignBudget(usd=0.000001))
            try:
                player = Player(engine(), model=Mock())
                store.create(player)
                with self.assertRaises(BudgetExceeded):
                    player.step()
                player.model.invoke.assert_not_called()
                self.assertEqual(campaign_usage(store)['requests'], 0)
            finally:
                store.close()

    def test_failures_are_counted_against_planned_probes_and_refusals_are_preserved(self):
        rows = [{'kind': 'fixture', 'model': 'gpt-6-luna', 'mode': 'hybrid', 'status': 'failed',
                 'probe_count': 3, 'scores': [], 'usage': {'requests': 1, 'known_tokens': 10}, 'estimated_usd': 0.001,
                 'attempts': [{'category': 'refusal', 'seconds': 1}]}]
        result = summarize(rows)[0]
        self.assertEqual(result['completed'], 0)
        self.assertEqual(result['planned_probes'], 3)
        self.assertEqual(result['scored_probes'], 0)
        self.assertEqual(result['refusal_attempts'], 1)

    def test_dry_run_does_not_load_dotenv_or_create_a_model(self):
        with patch('scripts.evaluate.load_dotenv', side_effect=AssertionError):
            outcome = CliRunner().invoke(cli, ['--dry-run'])
        self.assertEqual(outcome.exit_code, 0, outcome.output)
        self.assertIn('contradiction-and-belief', outcome.output)

    def test_report_only_rebuilds_without_credentials_or_new_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            store = RunStore(output / 'runs.sqlite3')
            store.close()
            (output / 'results.jsonl').write_text('', encoding='utf-8')
            with patch('scripts.evaluate.load_dotenv', side_effect=AssertionError), \
                    patch('evaluation.create_model', side_effect=AssertionError):
                outcome = CliRunner().invoke(cli, ['--report-only', '--output-dir', directory])
            self.assertEqual(outcome.exit_code, 0, outcome.output)
            summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['groups'], [])
            self.assertEqual(summary['campaign']['requests'], 0)


if __name__ == '__main__':
    unittest.main()
