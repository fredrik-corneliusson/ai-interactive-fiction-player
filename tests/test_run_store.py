import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from click.testing import CliRunner
import httpx
import openai

from ai_player import cli
from player import BudgetExceeded, Player, PlayerError, Settings, create_model
from run_store import RunStore, StoreError, snapshot
from test_player import api_response, engine, result


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'runs.sqlite3'
        self.store = RunStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def player(self, *, settings=None, game=None, responses=None):
        model = Mock()
        model.invoke.side_effect = responses or [result()]
        player = Player(game or engine(), settings, model=model, sleep=Mock())
        self.store.create(player)
        return player

    def reopen(self):
        self.store.close()
        self.store = RunStore(self.path)

    def test_restart_restores_randomness_variables_history_settings_and_persona(self):
        game = engine('(set: $count to $count +1)(set: $roll to (random: 1,100))Start [[Go|End]]',
                      '(set: $count to $count +1)(set: $roll to (random: 1,100))End [[Back|Start]]')
        player = self.player(game=game, settings=Settings(retries=0, request_budget=3))
        player.step()
        player.persona = 'Saved persona'
        # Persona is immutable run configuration; create a second run with it.
        self.store.create(player)
        run_id = player.session.run_id
        before = snapshot(player)
        self.reopen()
        resumed = self.store.resume(run_id, model=Mock())
        self.assertEqual(snapshot(resumed), before)
        self.assertEqual(resumed.persona, 'Saved persona')
        self.assertEqual(resumed.settings, player.settings)
        self.assertEqual(resumed.engine.game.vars['count'], 2)
        self.assertEqual(resumed.engine.game.rng.getstate(), player.engine.game.rng.getstate())
        # Both independent engines produce the same next random outcome.
        resumed.model.invoke.return_value = result()
        player.model.invoke.side_effect = None
        player.model.invoke.return_value = result()
        resumed.step()
        # Use an unpersisted reference to avoid stale-session mutation.
        player.session = None
        player.step()
        self.assertEqual(snapshot(resumed), snapshot(player))

    def test_terminal_resume_does_not_repeat_committed_action_or_call_model(self):
        player = self.player(game=engine(destination='(set: $count to $count +1)Finished'))
        run_id = player.session.run_id
        player.step()
        self.reopen()
        model = Mock()
        resumed = self.store.resume(run_id, model=model)
        self.assertIsNone(resumed.step())
        model.invoke.assert_not_called()
        self.assertEqual(resumed.engine.game.vars['count'], 1)
        self.assertEqual(len(resumed.history), 3)
        self.assertEqual(self.store.usage(run_id)['actions'], 1)

    def test_runs_have_isolated_state_history_and_usage(self):
        first, second = self.player(), self.player(game=engine('Different [[Go|End]]'))
        first.step()
        self.assertEqual(self.store.usage(first.session.run_id)['requests'], 1)
        self.assertEqual(self.store.usage(second.session.run_id)['requests'], 0)
        self.assertEqual(len(self.store.resume(second.session.run_id).history), 1)
        self.assertEqual(len(self.store.list_runs()), 2)

    def test_real_responses_usage_and_invalid_retry_are_both_logged(self):
        responses = iter([api_response(choice=99), api_response()])
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses), request=request))) as client:
            settings = Settings(retries=1)
            player = Player(engine(), settings, model=create_model(settings, api_key='offline-test-key', http_client=client))
            self.store.create(player)
            player.step()
        report = self.store.report(player.session.run_id)
        self.assertEqual(report['usage']['requests'], 2)
        self.assertEqual(report['usage']['known_tokens'], 60)
        self.assertEqual(report['usage']['budget_tokens'], 60)
        ends = [e['payload'] for e in report['attempts'] if e['kind'] == 'request_finished']
        self.assertEqual([e['category'] for e in ends], ['invalid_decision', 'valid_decision'])
        self.assertTrue(all(e['seconds'] >= 0 and e['model'] == 'gpt-6-luna' for e in ends))

    def test_connection_retry_unknown_usage_is_reserved_and_no_secret_is_logged(self):
        error = openai.APIConnectionError(message='secret-provider-payload', request=httpx.Request('POST', 'https://example.test'))
        player = self.player(responses=[error, result()], settings=Settings(retries=1))
        player.step()
        report = self.store.report(player.session.run_id)
        self.assertEqual(report['usage']['unknown_usage_requests'], 2)
        self.assertGreater(report['usage']['budget_tokens'], 0)
        self.assertNotIn('secret-provider-payload', json.dumps(report))
        player.sleep.assert_called_once_with(1)

    def test_request_budget_counts_retries_and_survives_resume(self):
        player = self.player(settings=Settings(retries=2, request_budget=1), responses=[result(99)])
        with self.assertRaises(BudgetExceeded):
            player.step()
        player.model.invoke.assert_called_once()
        run_id = player.session.run_id
        self.reopen()
        model = Mock()
        resumed = self.store.resume(run_id, model=model)
        with self.assertRaises(BudgetExceeded):
            resumed.step()
        model.invoke.assert_not_called()
        self.assertEqual(len(resumed.history), 1)
        self.assertEqual(self.store.usage(run_id)['requests'], 1)

    def test_token_budget_stops_before_request(self):
        player = self.player(settings=Settings(token_budget=1))
        with self.assertRaises(BudgetExceeded):
            player.step()
        player.model.invoke.assert_not_called()
        self.assertEqual(self.store.usage(player.session.run_id)['requests'], 0)

    def test_pending_request_blocks_resume_until_explicit_acknowledgment(self):
        player = self.player()
        run_id = player.session.run_id
        player.session.begin_request(player.settings, 12345, 1)
        self.reopen()
        model = Mock()
        model.invoke.return_value = result()
        resumed = self.store.resume(run_id, model=model)
        with self.assertRaisesRegex(StoreError, 'unresolved'):
            resumed.step()
        model.invoke.assert_not_called()
        self.assertEqual(self.store.usage(run_id)['pending_requests'], 1)
        resumed.session.acknowledge_pending()
        self.assertEqual(self.store.usage(run_id)['budget_tokens'], 12345)
        self.assertEqual(self.store.usage(run_id)['unknown_latency_requests'], 1)
        resumed.step()
        self.assertEqual(self.store.usage(run_id)['actions'], 1)

    def test_stale_session_cannot_send_request_or_overwrite_checkpoint(self):
        player = self.player()
        other = RunStore(self.path)
        try:
            stale = other.resume(player.session.run_id, model=Mock())
            player.session.checkpoint(player)
            with self.assertRaisesRegex(StoreError, 'another session'):
                stale.step()
            stale.model.invoke.assert_not_called()
            with self.assertRaises(StoreError):
                stale.session.checkpoint(stale)
        finally:
            other.close()

    def test_action_and_state_are_atomic_and_memory_rolls_back_on_db_failure(self):
        player = self.player()
        before = snapshot(player)
        self.store.db.execute("CREATE TRIGGER reject_action BEFORE INSERT ON events WHEN NEW.kind='action' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            player.step()
        self.assertEqual(snapshot(player), before)
        self.assertEqual(snapshot(self.store.resume(player.session.run_id)), before)
        usage = self.store.usage(player.session.run_id)
        self.assertEqual(usage['actions'], 0)
        self.assertEqual(usage['requests'], 1)
        self.assertEqual(usage['pending_requests'], 0)

    def test_failed_engine_transition_preserves_checkpoint_but_records_paid_attempt(self):
        player = self.player(game=engine(destination='(set: $cash to 9)(unsupported:)'))
        before = snapshot(player)
        with self.assertRaises(ValueError):
            player.step()
        self.assertEqual(snapshot(player), before)
        self.assertEqual(snapshot(self.store.resume(player.session.run_id)), before)
        self.assertEqual(self.store.usage(player.session.run_id)['requests'], 1)
        self.assertEqual(self.store.usage(player.session.run_id)['actions'], 0)

    def test_event_log_rejects_update_and_delete(self):
        self.player()
        for command in ('UPDATE events SET kind="changed"', 'DELETE FROM events'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.db:
                    self.store.db.execute(command)

    def test_unknown_run_and_modified_story_fail_explicitly(self):
        with self.assertRaises(StoreError):
            self.store.resume('unknown')
        player = self.player()
        with self.store.db:
            self.store.db.execute('UPDATE runs SET story=? WHERE id=?', ('{}', player.session.run_id))
        with self.assertRaisesRegex(StoreError, 'checksum'):
            self.store.resume(player.session.run_id)

    def test_refusal_incomplete_decode_and_http_errors_have_safe_categories(self):
        request = httpx.Request('POST', 'https://example.test')
        auth = openai.AuthenticationError('secret-provider-payload', response=httpx.Response(401, request=request), body=None)
        for response, category in ((result(refusal=True), 'refusal'),
                                   (result(status='incomplete'), 'incomplete'),
                                   (ValueError('secret-provider-payload'), 'decode_error'),
                                   (auth, 'http_error')):
            with self.subTest(category=category):
                player = self.player(settings=Settings(retries=0), responses=[response])
                with self.assertRaises(PlayerError) as caught:
                    player.step()
                report = self.store.report(player.session.run_id)
                self.assertEqual(report['attempts'][-1]['payload']['category'], category)
                self.assertEqual(report['usage']['actions'], 0)
                self.assertEqual(report['usage']['requests'], 1)
                self.assertNotIn('secret-provider-payload', str(caught.exception) + json.dumps(report))

    def test_resumed_prompt_excludes_unvisited_text_and_hidden_state(self):
        player = self.player(game=engine('(set: $hidden to 99)Visible [[Go|End]]', 'UNVISITED'))
        run_id = player.session.run_id
        self.reopen()
        model = Mock()
        model.invoke.return_value = result()
        resumed = self.store.resume(run_id, model=model)
        resumed.step()
        prompt = str([m.content for m in model.invoke.call_args.args[0]])
        self.assertIn('Visible', prompt)
        self.assertNotIn('UNVISITED', prompt)
        self.assertNotIn('$hidden', prompt)

    def test_cli_create_resume_inspect_and_reject_override_without_network(self):
        story = Path(self.directory.name) / 'story.html'
        story.write_text('<tw-storydata startnode="1"><tw-passagedata pid="1" name="Start">[[Go|End]]</tw-passagedata><tw-passagedata pid="2" name="End">Done</tw-passagedata></tw-storydata>', encoding='utf-8')
        model = Mock()
        model.invoke.return_value = result()
        runner = CliRunner()
        common = ['--database', str(self.path)]
        with patch('ai_player.load_dotenv'), patch.dict(os.environ, {'OPENAI_API_KEY': 'offline-test-key'}), patch('player.create_model', return_value=model):
            outcome = runner.invoke(cli, [*common, '--story-file', str(story), '--max-steps', '1'])
        self.assertEqual(outcome.exit_code, 0, outcome.output)
        run_id = self.store.list_runs()[0]['id']
        with patch('ai_player.load_dotenv'), patch.dict(os.environ, {'OPENAI_API_KEY': ''}), patch('player.create_model', side_effect=AssertionError):
            for args in (['--resume', run_id], ['--list-runs'], ['--run-info', run_id]):
                outcome = runner.invoke(cli, [*common, *args])
                self.assertEqual(outcome.exit_code, 0, outcome.output)
            outcome = runner.invoke(cli, [*common, '--resume', run_id, '--request-budget', '200'])
        self.assertNotEqual(outcome.exit_code, 0)
        self.assertIn('saved configuration', outcome.output)


if __name__ == '__main__':
    unittest.main()
