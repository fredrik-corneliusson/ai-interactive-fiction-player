import contextlib
import io
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from click.testing import CliRunner

from game import Game, GameEngine, Link, Passage, cli, load_game_data
from twine import UnsupportedTwineError


FIXTURES = Path(__file__).parent / "fixtures"


def story(*texts):
    return {str(i): Passage(i, str(i), text, []) for i, text in enumerate(texts)}


class EngineTests(unittest.TestCase):
    def test_nested_inactive_branch_does_not_leak_text_links_or_assignments(self):
        data = story('(if: $outer is true)[SECRET (set: $cash to 999)(if: $inner is true)[[[Hidden|1]]](else:)[LEAK]](else:)[VISIBLE] AFTER [[Go|1]]', 'End')
        game = Game(data)
        self.assertIn('VISIBLE AFTER', str(game))
        self.assertNotIn('SECRET', str(game))
        self.assertNotIn('LEAK', str(game))
        self.assertNotIn('cash', game.vars)
        self.assertEqual(game.links, [Link('Go', '1')])

    def test_nested_selected_branches_restore_parent_condition(self):
        game = Game(story('(if: $outer is true)[A(if: $inner is true)[B](else:)[C]D](else:)[E]F'), variables={'outer': True})
        self.assertEqual(str(game), 'ACDF')

    def test_elseif_executes_only_one_branch(self):
        text = '(if: $roll is 1)[(set: $cash to $cash +100)ONE](elseif: $roll is 2)[TWO](else:)[THREE] AFTER'
        for roll, expected in ((1, 'ONE AFTER'), (2, 'TWO AFTER'), (3, 'THREE AFTER')):
            with self.subTest(roll=roll):
                game = Game(story(text), variables={'roll': roll, 'cash': 10})
                self.assertEqual(str(game), expected)
                self.assertEqual(game.vars['cash'], 110 if roll == 1 else 10)

    def test_arithmetic_interpolation_and_numeric_clamp(self):
        game = Game(story('(set: $health to $health +10)(if: $health > 100)[(set: $health to 100)](if: $cash is > 50)[YES](else:)[NO] $health'), variables={'health': 95, 'cash': 51})
        self.assertEqual(str(game), 'YES 100')

    def test_types_are_not_coerced_for_boolean_equality(self):
        game = Game(story('(if: $unset is false)[FALSE](else:)[ZERO]'))
        self.assertEqual(str(game), 'ZERO')

    def test_random_is_seeded_and_rendering_does_not_reroll(self):
        data = story('(set: $roll to (random: 1, 4))$roll [[Go|1]]', 'End')
        first = Game(data, seed=7)
        second = Game(data, seed=7)
        engine = GameEngine(first)
        state = first.rng.getstate()
        self.assertEqual(first.vars, second.vars)
        self.assertTrue(1 <= first.vars['roll'] <= 4)
        self.assertEqual(engine.next(), engine.next())
        self.assertEqual(first.rng.getstate(), state)

    def test_invalid_choices_leave_everything_unchanged(self):
        engine = GameEngine(Game(story('[[Go|1]]', '(set: $count to $count +1)End')))
        before = (engine.game._current, dict(engine.game.vars), engine.game.rng.getstate())
        for choice in (0, -1, 2, True, '1', 1.0):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                engine.next(choice)
            self.assertEqual((engine.game._current, engine.game.vars, engine.game.rng.getstate()), before)

    def test_valid_action_applies_once_and_terminal_does_not_prompt(self):
        engine = GameEngine(Game(story('[[Go|1]]', '(set: $count to $count +1)End')))
        self.assertIn('SLUTET', engine.next(1))
        self.assertEqual(engine.game.vars['count'], 1)
        self.assertFalse(engine.running)
        engine.next()
        self.assertEqual(engine.game.vars['count'], 1)
        with patch('builtins.input', side_effect=AssertionError('Must not prompt')):
            self.assertIsNone(engine.prompt())

    def test_failed_transition_rolls_back_variables_and_rng(self):
        engine = GameEngine(Game(story('[[Go|1]]', '(set: $roll to (random: 1, 4))(set: $cash to 999)(unknown:)'), seed=4))
        before = (engine.game._current, dict(engine.game.vars), engine.game.rng.getstate(), engine.next())
        with self.assertRaisesRegex(UnsupportedTwineError, 'Unsupported macro'):
            engine.next(1)
        self.assertEqual((engine.game._current, engine.game.vars, engine.game.rng.getstate(), engine.next()), before)

    def test_unavailable_and_missing_links_are_rejected(self):
        game = Game(story('[[Missing|absent]]', 'End'))
        with self.assertRaises(ValueError):
            game.follow(Link('invented', '1'))
        with self.assertRaisesRegex(ValueError, 'Unknown destination'):
            game.follow(game.links[0])
        self.assertEqual(game._current, '0')

    def test_supported_link_forms(self):
        game = Game(story('[[One|1]] [[Two->1]] [[1<-Three]] [[1]]', 'End'))
        self.assertEqual([link.text for link in game.links], ['One', 'Two', 'Three', '1'])
        self.assertTrue(all(link.destination == '1' for link in game.links))

    def test_browser_controls_are_not_executed_or_exposed_as_source(self):
        game = Game(story('(link: "Load")[(load-game: "A")]Done'))
        self.assertEqual(str(game), 'Done')
        self.assertTrue(game.notices)
        with self.assertRaisesRegex(UnsupportedTwineError, 'Interactive'):
            Game(story('(link: "Cheat")[(set: $cash to 999)]'))

    def test_startnode_and_explicit_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'story.html'
            path.write_text('<tw-storydata startnode="2"><tw-passagedata pid="1" name="Other">Wrong</tw-passagedata><tw-passagedata pid="2" name="Start">Right</tw-passagedata></tw-storydata>', encoding='utf-8')
            game = Game(load_game_data(game_file=path))
            self.assertEqual(game._current, 'Start')
            result = CliRunner().invoke(cli, ['--story-file', str(path)])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn('Right', result.output)
            self.assertIn('SLUTET', result.output)
            with patch('game.dowload_game_data') as download, self.assertRaises(FileNotFoundError):
                load_game_data(game_file=Path(directory) / 'absent.html')
            download.assert_not_called()

    def test_invalid_story_reports_a_useful_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'story.html'
            for content, error in (('<html>No story</html>', 'No Twine story'), ('<tw-storydata startnode="99"><tw-passagedata pid="1" name="Start">Text</tw-passagedata></tw-storydata>', 'startnode')):
                path.write_text(content, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, error):
                    load_game_data(game_file=path)


class AiLoopTests(unittest.TestCase):
    def test_terminal_story_makes_no_model_call(self):
        from ai_player import play
        conversation = Mock()
        with contextlib.redirect_stdout(io.StringIO()), patch('builtins.input', side_effect=AssertionError):
            play(GameEngine(Game(story('End'))), conversation)
        conversation.predict.assert_not_called()

    def test_autorun_stops_at_ending_and_invalid_response_does_not_advance(self):
        from ai_player import play
        for response, inputs, calls, destination in [('1. <Go>', ['r 10'], 1, '1'), ('99. <Wrong>', ['', 'q'], 1, '0'), ('Refusal', ['', 'q'], 1, '0')]:
            engine = GameEngine(Game(story('[[Go|1]]', '(set: $count to $count +1)End')))
            conversation = Mock()
            conversation.predict.return_value = response
            with self.subTest(response=response), contextlib.redirect_stdout(io.StringIO()), patch('builtins.input', side_effect=inputs):
                play(engine, conversation)
            self.assertEqual(conversation.predict.call_count, calls)
            self.assertEqual(engine.game._current, destination)
            self.assertEqual(engine.game.vars.get('count', 0), 1 if destination == '1' else 0)


class BrowserFixtureTests(unittest.TestCase):
    def test_encountered_history_has_valid_actions_and_stops_at_fixture_boundary(self):
        traces = json.loads((FIXTURES / 'encountered_history.json').read_text(encoding='utf-8'))
        self.assertEqual({trace['part'] for trace in traces}, {'del1', 'del2'})
        for trace in traces:
            self.assertEqual(len(trace['turns']), 12)
            for number, turn in enumerate(trace['turns']):
                self.assertEqual(turn['turn'], number)
                self.assertNotIn('variables', turn)
                self.assertNotIn('vars', turn)
                if number == len(trace['turns']) - 1:
                    self.assertIsNone(turn['selected_choice'])
                else:
                    self.assertTrue(1 <= turn['selected_choice'] <= len(turn['available_choices']))
                for label in turn['available_choices']:
                    self.assertIn(label, turn['observation'])

    def test_original_harlowe_passage_snapshots(self):
        cases = json.loads((FIXTURES / 'browser_passages.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(part=case['part'], passage=case['passage'], variables=case['variables']):
                data = {case['passage']: Passage(1, case['passage'], case['text'], [])}
                game = Game(data, variables=case['variables'])
                visible = game.re_links.sub(lambda m: m[1].split('|', 1)[0], game.rendered_text)
                self.assertEqual(re.sub(r'\s+', ' ', visible).strip(), case['expected_text'])
                self.assertEqual([{'text': link.text, 'destination': link.destination} for link in game.links], case['expected_links'])
                for variable, expected in case['expected_variables'].items():
                    self.assertEqual(game.vars[variable], expected)


if __name__ == '__main__':
    unittest.main()
