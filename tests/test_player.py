import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from click.testing import CliRunner
import httpx
from langchain_core.messages import AIMessage
import openai

from ai_player import cli, play
from game import Game, GameEngine, Passage
from player import ContextBudgetExceeded, Decision, Player, PlayerError, Refusal, Settings, create_model


def engine(text='[[Go|End]]', destination='Finished'):
    return GameEngine(Game({'Start': Passage(1, 'Start', text, []), 'End': Passage(2, 'End', destination, [])}))


def result(choice=1, *, status='completed', refusal=False, parsed=True):
    raw = AIMessage(content=[{'type': 'refusal', 'refusal': 'No'}] if refusal else [], response_metadata={'status': status, 'model_name': 'gpt-6-luna'})
    return {'raw': raw, 'parsed': {'choice': choice, 'character_comment': 'Nu går jag.'} if parsed else None, 'parsing_error': None}


def api_response(choice=1, *, status='completed', refusal=False):
    content = {'type': 'refusal', 'refusal': 'Declined'} if refusal else {'type': 'output_text', 'text': json.dumps({'choice': choice, 'character_comment': 'Nu går jag.'}), 'annotations': []}
    return {
        'id': 'resp_offline_fixture', 'object': 'response', 'created_at': 1,
        'model': 'gpt-6-luna', 'status': status, 'error': None,
        'incomplete_details': {'reason': 'max_output_tokens'} if status == 'incomplete' else None,
        'output': [{'id': 'msg_offline_fixture', 'type': 'message', 'role': 'assistant', 'status': status, 'content': [content]}],
        'parallel_tool_calls': False, 'tools': [], 'tool_choice': 'auto',
        'text': {'format': {'type': 'json_schema', 'name': 'Decision', 'strict': True, 'schema': Decision.model_json_schema()}},
        'usage': {'input_tokens': 20, 'output_tokens': 10, 'total_tokens': 30,
                  'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}},
    }


class PlayerTests(unittest.TestCase):
    def test_terminal_game_does_not_create_model_or_prompt(self):
        game = engine('End')
        with patch('player.create_model') as create, patch('builtins.input', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()):
            player = Player(game)
            self.assertIsNone(player.step())
            self.assertEqual(play(game, player), 0)
        create.assert_not_called()

    def test_history_contains_observed_text_and_only_successful_actions(self):
        model = Mock()
        model.invoke.return_value = result()
        game = engine('Visible [[Go|End]]', '(set: $hidden to 99)Finished')
        player = Player(game, model=model)
        self.assertEqual(player.step().choice, 1)
        self.assertEqual(len(player.history), 3)
        self.assertEqual(player.history[1].content, Decision(choice=1, character_comment='Nu går jag.').model_dump_json())
        self.assertIn('Finished', player.history[2].content)
        supplied = model.invoke.call_args.args[0]
        self.assertEqual(len(supplied), 2)
        self.assertNotIn('Finished', supplied[-1].content)
        self.assertNotIn('hidden', str([m.content for m in player.history]))

    def test_full_history_preserves_all_earlier_turns_without_duplicate_current_observation(self):
        game = engine('First [[Go|End]]', 'Second [[Again|Start]]')
        model = Mock()
        model.invoke.return_value = result()
        player = Player(game, model=model)
        player.step()
        player.step()
        self.assertEqual(len(model.invoke.call_args.args[0]), 4)
        self.assertEqual([m.type for m in player.history], ['human', 'ai', 'human', 'ai', 'human'])
        self.assertIn('First', player.history[0].content)
        self.assertIn('Second', player.history[2].content)

    def test_invalid_choices_retry_without_contaminating_history(self):
        for invalid in (0, 99, True, '1', 1.0):
            with self.subTest(invalid=invalid):
                game = engine()
                model = Mock()
                model.invoke.side_effect = [result(invalid), result()]
                player = Player(game, Settings(retries=1), model=model)
                player.step()
                self.assertEqual(model.invoke.call_count, 2)
                self.assertEqual(len(player.history), 3)
                self.assertEqual(model.invoke.call_args_list[0].args[0], model.invoke.call_args_list[1].args[0])
                self.assertFalse(game.running)

    def test_exhausted_invalid_response_leaves_engine_and_history_unchanged(self):
        model = Mock()
        model.invoke.return_value = result(99)
        game = engine()
        player = Player(game, Settings(retries=2), model=model)
        before = list(player.history)
        with self.assertRaisesRegex(PlayerError, 'retries exhausted'):
            player.step()
        self.assertEqual(model.invoke.call_count, 3)
        self.assertEqual(player.history, before)
        self.assertEqual(game.game._current, 'Start')

    def test_refusal_and_incomplete_responses_are_not_retried(self):
        for response, error in ((result(refusal=True), Refusal), (result(status='incomplete'), PlayerError)):
            model = Mock()
            model.invoke.return_value = response
            game = engine()
            player = Player(game, model=model)
            with self.assertRaises(error):
                player.step()
            model.invoke.assert_called_once()
            self.assertEqual(len(player.history), 1)
            self.assertEqual(game.game._current, 'Start')

    def test_malformed_response_has_a_bounded_retry(self):
        model = Mock()
        model.invoke.return_value = result(parsed=False)
        player = Player(engine(), Settings(retries=1), model=model)
        with self.assertRaises(PlayerError):
            player.step()
        self.assertEqual(model.invoke.call_count, 2)

    def test_context_budget_stops_before_request_without_truncation(self):
        model = Mock()
        player = Player(engine(), Settings(context_budget=1), model=model)
        before = list(player.history)
        with self.assertRaises(ContextBudgetExceeded):
            player.step()
        model.invoke.assert_not_called()
        self.assertEqual(player.history, before)

    def test_failed_game_transition_does_not_commit_model_output(self):
        model = Mock()
        model.invoke.return_value = result()
        player = Player(engine(destination='(set: $cash to 999)(unsupported:)'), model=model)
        with self.assertRaises(ValueError):
            player.step()
        self.assertEqual(len(player.history), 1)
        self.assertEqual(player.engine.game._current, 'Start')
        self.assertEqual(player.engine.game.vars, {})

    def test_transient_error_retries_and_authentication_does_not_expose_provider_text(self):
        request = httpx.Request('POST', 'https://api.openai.com/v1/responses')
        model = Mock()
        model.invoke.side_effect = [openai.APIConnectionError(request=request), result()]
        sleep = Mock()
        player = Player(engine(), Settings(retries=1), model=model, sleep=sleep)
        player.step()
        sleep.assert_called_once_with(1)
        response = httpx.Response(401, request=request)
        model = Mock()
        model.invoke.side_effect = openai.AuthenticationError('secret-provider-text', response=response, body=None)
        with self.assertRaises(PlayerError) as caught:
            Player(engine(), model=model).step()
        self.assertNotIn('secret-provider-text', str(caught.exception))
        model.invoke.assert_called_once()

    def test_model_parameter_compatibility_and_no_silent_substitution(self):
        with self.assertRaisesRegex(ValueError, 'does not support'):
            Settings(model='gpt-6.1-sol', reasoning='none').check_compatibility()
        with self.assertRaises(ValueError):
            Settings(model='made-up-model')


class TransportTests(unittest.TestCase):
    def test_real_langchain_serializes_responses_schema_and_preserves_raw_metadata(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json=api_response(), request=request)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            settings = Settings(retries=0, reasoning='low', max_output_tokens=512)
            model = create_model(settings, api_key='offline-test-key', http_client=client)
            player = Player(engine(), settings, model=model)
            self.assertEqual(player.step().choice, 1)
        request = requests[0]
        self.assertEqual(request.url.path, '/v1/responses')
        payload = json.loads(request.content)
        self.assertEqual(payload['model'], 'gpt-6-luna')
        self.assertEqual(payload['reasoning'], {'effort': 'low'})
        self.assertEqual(payload['max_output_tokens'], 512)
        self.assertFalse(payload['store'])
        self.assertNotIn('previous_response_id', payload)
        self.assertNotIn('temperature', payload)
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['text']['format']['type'], 'json_schema')
        self.assertTrue(payload['text']['format']['strict'])
        self.assertEqual(player.last_call['usage']['total_tokens'], 30)

    def test_real_responses_refusal_and_incomplete_output_never_advance(self):
        for status, refusal in (('completed', True), ('incomplete', False)):
            with self.subTest(status=status, refusal=refusal):
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_response(status=status, refusal=refusal), request=request))) as client:
                    settings = Settings(retries=0)
                    player = Player(engine(), settings, model=create_model(settings, api_key='offline-test-key', http_client=client))
                    with self.assertRaises(PlayerError):
                        player.step()
                    self.assertEqual(player.engine.game._current, 'Start')


class ConsoleTests(unittest.TestCase):
    def test_help_memory_and_history_aliases_do_not_invoke_model(self):
        model = Mock()
        player = Player(engine(), model=model)
        commands = ['help', '?', '/HELP', '--help', 'm', 'MEMORY', '/memory',
                    'h', 'HISTORY', '/history', 'v', 'VARS', '/variables', 'QUIT']
        before = list(player.history)
        with patch('builtins.input', side_effect=commands), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(play(player.engine, player), 0)
        model.invoke.assert_not_called()
        text = output.getvalue()
        self.assertNotIn('Unknown command', text)
        self.assertIn('Console commands:', text)
        self.assertIn('Memory mode: full-history', text)
        self.assertIn('human:', text)
        self.assertEqual(player.history, before)
        self.assertEqual(player.engine.game._current, 'Start')

    def test_run_alias_returns_to_prompt_for_help_and_inspection(self):
        model = Mock()
        model.invoke.return_value = result()
        player = Player(engine(destination='Second [[Again|Start]]'), model=model)
        with patch('builtins.input', side_effect=['/RUN\t1', 'help', 'memory', 'exit']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(play(player.engine, player), 1)
        model.invoke.assert_called_once()
        self.assertIn('Console commands:', output.getvalue())
        self.assertNotIn('Unknown command', output.getvalue())
        self.assertEqual(player.engine.game._current, 'End')

    def test_bad_command_arguments_never_advance_or_start_autorun(self):
        model = Mock()
        player = Player(engine(), model=model)
        with patch('builtins.input', side_effect=['run', 'run 0', 'run 1 extra', 'memory extra', 'nonsense', 'exit']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(play(player.engine, player), 0)
        model.invoke.assert_not_called()
        self.assertIn('Use r N or run N', output.getvalue())
        self.assertIn('This command takes no arguments', output.getvalue())
        self.assertIn('Unknown command', output.getvalue())

    def test_autorun_stops_at_ending(self):
        game = engine(destination='(set: $count to $count +1)Finished')
        model = Mock()
        model.invoke.return_value = result()
        player = Player(game, model=model)
        with patch('builtins.input', side_effect=['r 10']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(play(game, player), 1)
        model.invoke.assert_called_once()
        self.assertEqual(game.game.vars['count'], 1)

    def test_bounded_run_propagates_failure_instead_of_prompting(self):
        model = Mock()
        model.invoke.return_value = result(99)
        player = Player(engine(), Settings(retries=0), model=model)
        with patch('builtins.input', side_effect=AssertionError), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(PlayerError):
            play(player.engine, player, max_steps=2)

    def test_cli_configuration_overrides_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            story = Path(directory) / 'story.html'
            story.write_text('<tw-storydata startnode="1"><tw-passagedata pid="1" name="Start">[[Go|End]]</tw-passagedata><tw-passagedata pid="2" name="End">End</tw-passagedata></tw-storydata>', encoding='utf-8')
            model = Mock()
            model.invoke.return_value = result()
            with patch('ai_player.load_dotenv'), patch.dict(os.environ, {'OPENAI_API_KEY': 'offline-test-key', 'OPENAI_MODEL': 'gpt-6.1-sol', 'OPENAI_REASONING_EFFORT': 'none'}), patch('player.create_model', return_value=model) as create:
                outcome = CliRunner().invoke(cli, ['--story-file', str(story), '--database', str(Path(directory) / 'runs.sqlite3'), '--model', 'gpt-6-luna', '--max-steps', '1'])
            self.assertEqual(outcome.exit_code, 0, outcome.output)
            self.assertEqual(create.call_args.args[0].model, 'gpt-6-luna')
            self.assertEqual(create.call_args.args[0].reasoning, 'none')


if __name__ == '__main__':
    unittest.main()
