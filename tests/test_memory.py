import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from click.testing import CliRunner
import httpx
from langchain_core.messages import AIMessage

from ai_player import cli, play
from game import Game, GameEngine, Passage
from memory import MemoryBatch, extraction_messages, retrieve, validate_batch
from player import BudgetExceeded, ContextBudgetExceeded, Player, PlayerError, Settings, create_model, input_token_upper_bound
from run_store import RunStore, StoreError, snapshot
from test_player import api_response, engine, result


def record(source, quote, *, channel='observation', epistemic='observation', layer='entity', entities=None):
    return {'layer': layer, 'epistemic': epistemic, 'text': quote,
            'entities': entities or [], 'salience': 4,
            'evidence': [{'source_id': source['source_id'], 'channel': channel, 'quote': quote}]}


def batch_result(records):
    return {'raw': AIMessage(content=[], response_metadata={'status': 'completed', 'model_name': 'gpt-6-luna'},
                             usage_metadata={'input_tokens': 20, 'output_tokens': 10, 'total_tokens': 30}),
            'parsed': {'records': records}, 'parsing_error': None}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'runs.sqlite3'
        self.store = RunStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def player(self, game=None, **options):
        settings = Settings(memory_mode='hybrid', retries=0, **options)
        decision = Mock()
        decision.invoke.return_value = result()
        memory = Mock()
        memory.invoke.return_value = batch_result([])
        player = Player(game or engine('Jenny lives here. [[Go|End]]', 'Jenny moved away. [[Back|Start]]'),
                        settings, model=decision, memory_model=memory)
        self.store.create(player)
        return player

    def test_batches_follow_cadence_with_one_request_and_shared_accounting(self):
        player = self.player(memory_update_interval=4)
        for _ in range(3):
            player.step()
        player.memory_model.invoke.assert_not_called()
        player.step()
        player.memory_model.invoke.assert_called_once()
        sources = json.loads(player.memory_model.invoke.call_args.args[0][-1].content)
        self.assertEqual([s['turn'] for s in sources], [0, 1, 2, 3])
        events = self.store.events(player.session.run_id)
        updates = [e['payload'] for e in events if e['kind'] == 'memory_updated']
        self.assertEqual(updates[0]['source_ids'], [s['source_id'] for s in sources])
        self.assertEqual(updates[0]['record_count'], 0)
        starts = [e['payload'] for e in events if e['kind'] == 'request_started']
        self.assertEqual([e['purpose'] for e in starts], ['decision', 'decision', 'decision', 'memory', 'decision'])
        self.assertEqual(self.store.usage(player.session.run_id)['requests'], 5)

    def test_short_window_updates_before_unprocessed_sources_disappear(self):
        player = self.player(recent_turns=1, memory_update_interval=20)
        player.step()
        player.step()
        player.memory_model.invoke.assert_not_called()
        player.step()
        supplied = json.loads(player.memory_model.invoke.call_args.args[0][-1].content)
        self.assertEqual([s['turn'] for s in supplied], [0, 1, 2])
        self.assertEqual(len(player.model.invoke.call_args.args[0]), 4)  # persona + recent observation/action/observation

    def test_conflicting_observations_keep_both_records_and_sources(self):
        player = self.player()
        player.step()
        sources = self.store.sources(player.session.run_id)
        records = [record(sources[0], 'Jenny lives here.', entities=['Jenny']),
                   record(sources[1], 'Jenny moved away.', entities=['Jenny'])]
        player.session.commit_memories(sources, validate_batch({'records': records}, sources))
        stored = self.store.memories(player.session.run_id)
        selected = player.inspect_memory()['selected']
        self.assertEqual({r['text'] for r in selected}, {'Jenny lives here.', 'Jenny moved away.'})
        self.assertEqual([r['source_refs'][0]['turn'] for r in stored], [0, 1])
        self.assertEqual([r['source_refs'][0]['passage'] for r in stored], ['Start', 'End'])
        for command in ('UPDATE memories SET record="{}"', 'DELETE FROM memories'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.db:
                    self.store.db.execute(command)

    def test_citations_must_be_exact_local_and_in_the_current_batch(self):
        first, other = self.player(), self.player()
        sources = self.store.sources(first.session.run_id)
        foreign = self.store.sources(other.session.run_id)[0]
        for invalid in (record(foreign, 'Jenny lives here.'), record(sources[0], 'Invented fact'),
                        record(sources[0], 'Jenny lives here.', entities=['UNSEEN'])):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_batch({'records': [invalid]}, sources)
        first.step()
        newer = self.store.sources(first.session.run_id)[1:]
        with self.assertRaises(ValueError):
            validate_batch({'records': [record(sources[0], 'Jenny lives here.')]}, newer)

    def test_comments_and_interpretations_cannot_be_promoted_to_observations(self):
        player = self.player()
        player.step()
        sources = self.store.sources(player.session.run_id)
        comment = sources[1]['character_comment']
        invalid = record(sources[1], comment, channel='character_comment')
        with self.assertRaises(ValueError):
            validate_batch({'records': [invalid]}, sources)
        belief = record(sources[1], comment, channel='character_comment', epistemic='character_belief', layer='intention')
        inferred = record(sources[0], 'Jenny lives here.', epistemic='interpretation')
        inferred['text'] = 'Jenny kanske vill träffa mig.'
        paraphrase = {**inferred, 'epistemic': 'observation'}
        with self.assertRaises(ValueError):
            validate_batch({'records': [paraphrase]}, sources)
        batch = validate_batch({'records': [belief, inferred]}, sources)
        player.session.commit_memories(sources, batch)
        messages = str([m.content for m in player.messages()])
        self.assertIn('character_belief', messages)
        self.assertIn('interpretation', messages)

    def test_retrieval_is_relevant_deterministic_and_bounded(self):
        player = self.player()
        source = self.store.sources(player.session.run_id)[0]
        batch = validate_batch({'records': [record(source, 'Jenny lives here.', entities=['Jenny'])]}, [source])
        player.session.commit_memories([source], batch)
        records = self.store.memories(player.session.run_id)
        records.append({**records[0], 'id': 999, 'text': 'Completely unrelated', 'entities': [], 'salience': 5})
        selected = retrieve(records, 'Where is Jenny?', limit=1, budget=8000)
        self.assertEqual(selected[0]['id'], records[0]['id'])
        self.assertEqual(selected, retrieve(records, 'Where is Jenny?', limit=1, budget=8000))
        self.assertLessEqual(len(json.dumps(selected, ensure_ascii=False).encode()), 8000)
        self.assertEqual(retrieve(records, 'Jenny', limit=8, budget=1), [])

    def test_fixture_recalls_earlier_jenny_evidence_outside_recent_window(self):
        trace = json.loads((Path(__file__).parent / 'fixtures/encountered_history.json').read_text(encoding='utf-8'))[0]
        data = {}
        for index, turn in enumerate(trace['turns']):
            narrative = turn['observation'].split('\n[1.')[0]
            link = f' [[Continue|{index + 1}]]' if index + 1 < len(trace['turns']) else ''
            data[str(index)] = Passage(index + 1, str(index), narrative + link, [])
        data['UNVISITED'] = Passage(99, 'UNVISITED', 'SECRET FUTURE', [])
        game = GameEngine(Game(data, variables={'hidden_secret': 999}, seed=7))
        player = self.player(game, recent_turns=1, memory_update_interval=4)
        def extract(messages):
            sources = json.loads(messages[-1].content)
            matches = [s for s in sources if 'Jenny var alltid schysst mot dig' in s['observation']]
            return batch_result([record(matches[0], 'Jenny var alltid schysst mot dig', entities=['Jenny'])] if matches else [])
        player.memory_model.invoke.side_effect = extract
        for _ in range(5):
            player.step()
        inspected = player.inspect_memory()
        self.assertEqual(inspected['stored'][0]['source_refs'][0]['turn'], 2)
        # The oldest still-unprocessed source is bridged until the next extraction.
        self.assertEqual([s['turn'] for s in self.store.sources(player.session.run_id) if s['source_id'] in inspected['recent_source_ids']], [3, 4, 5])
        self.assertIn('Jenny var alltid schysst mot dig', str(inspected['selected']))
        for calls in (player.model.invoke.call_args_list, player.memory_model.invoke.call_args_list):
            for call in calls:
                prompt = str([m.content for m in call.args[0]])
                self.assertNotIn('SECRET FUTURE', prompt)
                self.assertNotIn('hidden_secret', prompt)
                self.assertNotIn('999', prompt)

    def test_resume_restores_records_cursor_and_selection_without_reextracting(self):
        player = self.player(memory_update_interval=1)
        source = self.store.sources(player.session.run_id)[0]
        player.memory_model.invoke.return_value = batch_result([record(source, 'Jenny lives here.', entities=['Jenny'])])
        player.step()
        run_id = player.session.run_id
        before = player.inspect_memory()
        self.store.close()
        self.store = RunStore(self.path)
        resumed = self.store.resume(run_id, model=Mock(), memory_model=Mock())
        self.assertEqual(resumed.inspect_memory(), before)
        self.assertEqual(snapshot(resumed), snapshot(player))
        supplied = self.store.pending_sources(run_id)
        self.assertEqual([s['turn'] for s in supplied], [1])
        resumed.model.invoke.return_value = result()
        resumed.memory_model.invoke.return_value = batch_result([])
        resumed.step()
        request_sources = json.loads(resumed.memory_model.invoke.call_args.args[0][-1].content)
        self.assertEqual([s['turn'] for s in request_sources], [1])

    def test_other_runs_cannot_retrieve_or_commit_this_runs_memories(self):
        first, second = self.player(), self.player()
        source = self.store.sources(first.session.run_id)[0]
        batch = validate_batch({'records': [record(source, 'Jenny lives here.')]}, [source])
        first.session.commit_memories([source], batch)
        self.assertEqual(second.inspect_memory()['stored'], [])
        self.assertEqual(second.inspect_memory()['selected'], [])
        with self.assertRaises(StoreError):
            second.session.commit_memories([source], batch)

    def test_request_budget_includes_memory_and_stops_decision_before_invocation(self):
        player = self.player(memory_update_interval=1, request_budget=1)
        with self.assertRaises(BudgetExceeded):
            player.step()
        player.memory_model.invoke.assert_called_once()
        player.model.invoke.assert_not_called()
        self.assertEqual(len(player.history), 1)
        usage = self.store.usage(player.session.run_id)
        self.assertEqual(usage['known_tokens'], 30)
        self.assertEqual(usage['actions'], 0)

    def test_bad_memory_batch_is_not_retried_or_committed_and_stops_before_action(self):
        player = self.player(memory_update_interval=1)
        source = self.store.sources(player.session.run_id)[0]
        player.memory_model.invoke.return_value = batch_result([record(source, 'UNVISITED FACT')])
        before = snapshot(player)
        with self.assertRaisesRegex(PlayerError, 'Invalid structured memory'):
            player.step()
        player.memory_model.invoke.assert_called_once()
        player.model.invoke.assert_not_called()
        self.assertEqual(snapshot(player), before)
        self.assertEqual(self.store.memories(player.session.run_id), [])
        self.assertEqual(len(self.store.pending_sources(player.session.run_id)), 1)
        report = self.store.report(player.session.run_id)
        self.assertEqual(report['attempts'][-1]['payload']['category'], 'invalid_memory')

    def test_successful_memory_update_survives_failed_decision_without_duplicate_extraction(self):
        player = self.player(memory_update_interval=1)
        source = self.store.sources(player.session.run_id)[0]
        player.memory_model.invoke.return_value = batch_result([record(source, 'Jenny lives here.')])
        player.model.invoke.return_value = result(99)
        with self.assertRaises(PlayerError):
            player.step()
        run_id = player.session.run_id
        before = snapshot(player)
        self.store.close()
        self.store = RunStore(self.path)
        model, memory_model = Mock(), Mock()
        model.invoke.return_value = result()
        memory_model.invoke.side_effect = AssertionError('Already processed')
        resumed = self.store.resume(run_id, model=model, memory_model=memory_model)
        self.assertEqual(snapshot(resumed), before)
        resumed.step()
        memory_model.invoke.assert_not_called()
        self.assertEqual(len(self.store.memories(run_id)), 1)
        self.assertEqual(self.store.usage(run_id)['requests'], 3)
        self.assertEqual(self.store.usage(run_id)['actions'], 1)

    def test_memory_refusal_stops_before_decision_and_preserves_sources(self):
        player = self.player(memory_update_interval=1)
        player.memory_model.invoke.return_value = result(refusal=True)
        with self.assertRaises(PlayerError):
            player.step()
        player.model.invoke.assert_not_called()
        player.memory_model.invoke.assert_called_once()
        self.assertEqual(len(self.store.pending_sources(player.session.run_id)), 1)
        self.assertEqual(self.store.report(player.session.run_id)['attempts'][-1]['payload']['category'], 'refusal')

    def test_memory_input_budget_never_slices_source_or_calls_model(self):
        player = self.player(memory_update_interval=1, memory_input_budget=1)
        with self.assertRaises(ContextBudgetExceeded):
            player.step()
        player.model.invoke.assert_not_called()
        player.memory_model.invoke.assert_not_called()
        self.assertEqual(self.store.usage(player.session.run_id)['requests'], 0)

    def test_batch_write_is_atomic_and_keeps_usage_when_storage_fails(self):
        player = self.player(memory_update_interval=1)
        source = self.store.sources(player.session.run_id)[0]
        player.memory_model.invoke.return_value = batch_result([record(source, 'Jenny lives here.')])
        self.store.db.execute("CREATE TRIGGER reject_memory BEFORE INSERT ON memories BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            player.step()
        self.assertEqual(self.store.memories(player.session.run_id), [])
        self.assertFalse(any(e['kind'] == 'memory_updated' for e in self.store.events(player.session.run_id)))
        self.assertEqual(len(self.store.pending_sources(player.session.run_id)), 1)
        self.assertEqual(self.store.usage(player.session.run_id)['known_tokens'], 30)
        player.model.invoke.assert_not_called()

    def test_context_log_records_selection_and_exact_input_bound(self):
        player = self.player(memory_update_interval=1)
        source = self.store.sources(player.session.run_id)[0]
        player.memory_model.invoke.return_value = batch_result([record(source, 'Jenny lives here.')])
        player.step()
        events = [e['payload'] for e in self.store.events(player.session.run_id) if e['kind'] == 'context_selected']
        self.assertEqual(events[0]['memory_ids'], [self.store.memories(player.session.run_id)[0]['id']])
        self.assertEqual(events[0]['input_estimate'], input_token_upper_bound(player.model.invoke.call_args.args[0]))
        self.assertLessEqual(events[0]['memory_bytes'], player.settings.memory_context_budget)

    def test_terminal_and_full_history_modes_make_no_memory_requests(self):
        terminal = self.player(game=engine('Finished'), memory_update_interval=1)
        self.assertIsNone(terminal.step())
        terminal.memory_model.invoke.assert_not_called()
        full = Player(engine(), model=Mock(), memory_model=Mock())
        full.model.invoke.return_value = result()
        self.store.create(full)
        full.step()
        full.memory_model.invoke.assert_not_called()
        self.assertFalse(any(e['kind'] == 'memory_updated' for e in self.store.events(full.session.run_id)))

    def test_interactive_inspection_makes_no_requests(self):
        player = self.player()
        with patch('builtins.input', side_effect=['m', 'h', 'q']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(play(player.engine, player), 0)
        self.assertIn('selected', output.getvalue())
        self.assertIn('human:', output.getvalue())
        player.model.invoke.assert_not_called()
        player.memory_model.invoke.assert_not_called()

    def test_real_memory_schema_responses_transport_and_usage(self):
        requests = []
        settings = Settings(memory_mode='hybrid', memory_update_interval=1, retries=0)
        def respond(request):
            requests.append(json.loads(request.content))
            response = api_response()
            response['output'][0]['content'][0]['text'] = json.dumps({'records': []})
            return httpx.Response(200, json=response, request=request)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            memory_model = create_model(settings.model_copy(update={'max_output_tokens': settings.memory_max_output_tokens}),
                                        schema=MemoryBatch, api_key='offline-test-key', http_client=client)
            player = Player(engine(), settings, model=Mock(), memory_model=memory_model)
            player.model.invoke.return_value = result()
            self.store.create(player)
            player.step()
        self.assertEqual(requests[0]['text']['format']['name'], 'MemoryBatch')
        self.assertTrue(requests[0]['text']['format']['strict'])
        self.assertFalse(requests[0]['store'])
        self.assertNotIn('previous_response_id', requests[0])
        self.assertEqual(requests[0]['max_output_tokens'], 4096)
        self.assertEqual(self.store.usage(player.session.run_id)['known_tokens'], 30)

    def test_version_one_database_migrates_without_changing_saved_run(self):
        full = Player(engine(), model=Mock())
        self.store.create(full)
        run_id = full.session.run_id
        before = snapshot(full)
        with self.store.db:
            self.store.db.execute('DROP TABLE memories')
            self.store.db.execute('PRAGMA user_version=1')
            row = self.store.db.execute('SELECT settings FROM runs WHERE id=?', (run_id,)).fetchone()
            old = json.loads(row['settings'])
            for field in ('memory_reasoning', 'recent_turns', 'memory_update_interval', 'memory_input_budget', 'memory_context_budget', 'memory_limit', 'memory_max_output_tokens'):
                del old[field]
            self.store.db.execute('UPDATE runs SET settings=? WHERE id=?', (json.dumps(old), run_id))
        self.store.close()
        self.store = RunStore(self.path)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)
        resumed = self.store.resume(run_id)
        self.assertEqual(snapshot(resumed), before)
        self.assertEqual(resumed.settings.memory_mode, 'full-history')

    def test_memory_model_compatibility_and_cli_options(self):
        with self.assertRaisesRegex(ValueError, 'memory_reasoning'):
            Settings(memory_mode='hybrid', memory_model='gpt-6.1-sol', memory_reasoning='none').check_compatibility()
        outcome = CliRunner().invoke(cli, ['--help'])
        self.assertEqual(outcome.exit_code, 0)
        for option in ('--memory-update-interval', '--memory-input-budget', '--recent-turns', 'hybrid'):
            self.assertIn(option, outcome.output)


if __name__ == '__main__':
    unittest.main()
