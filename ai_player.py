import json
import os
from pathlib import Path
import textwrap

import click
from dotenv import load_dotenv

from game import Game, GameEngine, load_game_data
from player import Player, PlayerError, Settings


def out(*messages):
    text = " ".join(str(message) for message in messages)
    print("\n".join(textwrap.fill(line, 100) for line in text.split("\n")))


def play(engine, player, *, max_steps=None, debug=False):
    out(player.history[-1].content)
    completed = 0
    runs = max_steps or 0
    while engine.running:
        if max_steps is not None and completed >= max_steps:
            break
        if runs > 0:
            user_input = ""
            runs -= 1
        else:
            try:
                user_input = input("> ").strip()
            except EOFError:
                break
        if user_input == "q":
            break
        if user_input == "m":
            for message in player.history:
                out(f"{message.type}: {message.content}")
            continue
        if user_input == "v":
            out(engine.game._current)
            out(json.dumps(engine.game.vars, ensure_ascii=False))
            for notice in engine.game.notices:
                out(notice)
            continue
        if user_input.startswith("r "):
            try:
                runs = int(user_input.split()[-1])
                if runs < 1:
                    raise ValueError("Autorun steps must be positive")
            except ValueError as error:
                runs = 0
                out(error)
            continue
        if user_input != "":
            out("Choose v, m, r #, q, or press Enter to continue.")
            continue
        try:
            decision = player.step()
        except (PlayerError, ValueError) as error:
            runs = 0
            if max_steps is not None:
                raise
            out(error)
            continue
        if decision is None:
            break
        completed += 1
        out(f"{decision.choice}. <{decision.character_comment}>")
        if debug:
            out(json.dumps(player.last_call, ensure_ascii=False))
        out(player.history[-1].content)
    return completed


@click.command()
@click.option('--debug/--no-debug', default=False, help='Show safe model/usage/status summaries.')
@click.option('--story-file', type=click.Path(exists=True, dir_okay=False, path_type=Path), help='Local Twine HTML file.')
@click.option('--seed', type=int, help='Seed for repeatable game randomness (not model output).')
@click.option('--model', type=click.Choice(['gpt-6-luna', 'gpt-6.1-sol']))
@click.option('--memory-model', type=click.Choice(['gpt-6-luna', 'gpt-6.1-sol']), help='Reserved for CP4; no memory API calls in full-history mode.')
@click.option('--reasoning', type=click.Choice(['none', 'low', 'medium', 'high', 'xhigh', 'max']))
@click.option('--memory-mode', type=click.Choice(['full-history']))
@click.option('--timeout', type=click.FloatRange(min=0.1, max=120))
@click.option('--retries', type=click.IntRange(min=0, max=5))
@click.option('--max-output-tokens', type=click.IntRange(min=128, max=16384))
@click.option('--context-budget', type=click.IntRange(min=1, max=900000), help='Conservative input token budget; stops without truncating history.')
@click.option('--max-steps', type=click.IntRange(min=1, max=1000), help='Run at most this many decisions without interactive prompts.')
def cli(debug, story_file, seed, max_steps, **options):
    load_dotenv(Path(__file__).with_name('.env'), override=False)
    environment = {
        'model': 'OPENAI_MODEL', 'memory_model': 'OPENAI_MEMORY_MODEL',
        'reasoning': 'OPENAI_REASONING_EFFORT', 'memory_mode': 'AI_MEMORY_MODE',
        'timeout': 'AI_TIMEOUT', 'retries': 'AI_RETRIES',
        'max_output_tokens': 'AI_MAX_OUTPUT_TOKENS', 'context_budget': 'AI_CONTEXT_BUDGET',
    }
    values = {key: value if value is not None else os.getenv(environment[key]) for key, value in options.items()}
    values = {key: value for key, value in values.items() if value is not None}
    try:
        settings = Settings(**values)
        settings.check_compatibility()
        engine = GameEngine(Game(load_game_data(game_file=story_file), seed=seed))
        if engine.running and not os.getenv('OPENAI_API_KEY', '').strip():
            raise PlayerError('OPENAI_API_KEY is missing. Add it to .env or the process environment.')
        player = Player(engine, settings)
        play(engine, player, max_steps=max_steps, debug=debug)
    except (PlayerError, ValueError, OSError) as error:
        # Pydantic errors can echo input values; summarize configuration failures.
        from pydantic import ValidationError
        if isinstance(error, ValidationError):
            fields = ', '.join(str(item['loc'][0]) for item in error.errors())
            raise click.ClickException(f'Invalid configuration fields: {fields}') from error
        raise click.ClickException(str(error)) from error


if __name__ == '__main__':
    cli()
