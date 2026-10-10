import json
import os
import sqlite3
from pathlib import Path
import textwrap

import click
from dotenv import load_dotenv

from game import Game, GameEngine, load_game_data
from player import Player, PlayerError, Settings
from run_store import RunStore, StoreError


CONSOLE_HELP = """Console commands:
  help, ?, /help, --help   Show this help.
  m, memory               Inspect memories (or transcript in full-history mode).
  h, history              Show the complete encountered transcript.
  v, vars, variables      Show the current passage, variables, and notices.
  u, usage                Show cumulative request/token/latency usage.
  s, save                 Save the current checkpoint.
  r N, run N, autorun N    Run N AI steps, then return to the prompt.
  q, quit, exit           Quit; committed actions are already saved.
  Enter                   Ask the AI to take one step.
Commands are case-insensitive; slash prefixes such as /memory also work.
--max-steps runs without interactive prompts. Omit it to use these commands.
"""

COMMAND_ALIASES = {
    '?': 'help', '--help': 'help', 'memory': 'm', 'history': 'h',
    'vars': 'v', 'variables': 'v', 'usage': 'u', 'save': 's',
    'run': 'r', 'autorun': 'r', 'quit': 'q', 'exit': 'q',
}


def out(*messages):
    text = " ".join(str(message) for message in messages)
    print("\n".join(textwrap.fill(line, 100) for line in text.split("\n")))


def play(engine, player, *, max_steps=None, debug=False):
    out(player.history[-1].content)
    if engine.running and max_steps is None:
        out('Type help for console commands, or press Enter for one AI step.')
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
        parts = user_input.split()
        command = parts[0].casefold().removeprefix('/') if parts else ''
        user_input = COMMAND_ALIASES.get(command, command)
        if len(parts) > 1 and user_input != 'r':
            out('This command takes no arguments. Type help for console commands.')
            continue
        if user_input == 'help':
            out(CONSOLE_HELP)
            continue
        if user_input == "q":
            break
        if user_input == "m":
            if player.settings.memory_mode == 'hybrid':
                memory = player.inspect_memory()
                if not memory['stored']:
                    out('No extracted memory records yet. Pending sources will be processed when an update is due.')
                out(json.dumps(memory, ensure_ascii=False, indent=2))
            else:
                out('Memory mode: full-history. Showing the complete encountered transcript.')
                for message in player.history:
                    out(f"{message.type}: {message.content}")
            continue
        if user_input == 'h':
            for message in player.history:
                out(f'{message.type}: {message.content}')
            continue
        if user_input == "v":
            out(engine.game._current)
            out(json.dumps(engine.game.vars, ensure_ascii=False))
            for notice in engine.game.notices:
                out(notice)
            continue
        if user_input == 'u':
            if player.session:
                out(json.dumps(player.session.store.usage(player.session.run_id), ensure_ascii=False))
            else:
                out('Usage reporting requires a saved run.')
            continue
        if user_input == 's':
            if player.session:
                player.session.checkpoint(player)
                out(f'Saved run {player.session.run_id}')
            else:
                out('Saving requires a persisted run session.')
            continue
        if user_input == 'r':
            try:
                if len(parts) != 2:
                    raise ValueError('Use r N or run N with one positive step count.')
                runs = int(parts[1])
                if runs < 1:
                    raise ValueError("Autorun steps must be positive")
            except ValueError as error:
                runs = 0
                out(error)
            continue
        if user_input != "":
            out('Unknown command. Type help for console commands, or press Enter for one AI step.')
            continue
        try:
            decision = player.step()
        except (PlayerError, StoreError, ValueError) as error:
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
@click.option('--memory-model', type=click.Choice(['gpt-6-luna', 'gpt-6.1-sol']), help='Batched extractor model; unused in full-history mode.')
@click.option('--reasoning', type=click.Choice(['none', 'low', 'medium', 'high', 'xhigh', 'max']))
@click.option('--memory-mode', type=click.Choice(['full-history', 'hybrid']))
@click.option('--memory-reasoning', type=click.Choice(['none', 'low', 'medium', 'high', 'xhigh', 'max']))
@click.option('--recent-turns', type=click.IntRange(min=1, max=20), help='Complete recent action pairs plus the current observation in hybrid mode.')
@click.option('--memory-update-interval', type=click.IntRange(min=1, max=20), help='New encountered sources per extraction batch; update sooner before recent context would lose an unprocessed source.')
@click.option('--memory-input-budget', type=click.IntRange(min=1, max=900000), help='Conservative extractor input allowance; complete sources only.')
@click.option('--memory-context-budget', type=click.IntRange(min=1, max=100000), help='UTF-8 byte allowance for retrieved memory JSON, before message wrapper.')
@click.option('--memory-limit', type=click.IntRange(min=1, max=32), help='Maximum recalled memory records per decision.')
@click.option('--memory-max-output-tokens', type=click.IntRange(min=128, max=16384))
@click.option('--timeout', type=click.FloatRange(min=0.1, max=120))
@click.option('--retries', type=click.IntRange(min=0, max=5))
@click.option('--max-output-tokens', type=click.IntRange(min=128, max=16384))
@click.option('--context-budget', type=click.IntRange(min=1, max=900000), help='Conservative decision input allowance; stops without silently trimming context.')
@click.option('--max-steps', type=click.IntRange(min=1, max=1000), help='Run at most this many decisions without interactive prompts.')
@click.option('--request-budget', type=click.IntRange(min=1), help='Total attempts allowed across this run, including retries and resumes.')
@click.option('--token-budget', type=click.IntRange(min=1), help='Run token allowance; reserve input estimate plus maximum output before each attempt.')
@click.option('--database', type=click.Path(dir_okay=False, path_type=Path), default=Path('data/runs.sqlite3'), show_default=True)
@click.option('--resume', metavar='RUN_ID', help='Resume a checkpoint with its saved story, persona, settings, and budgets.')
@click.option('--list-runs', is_flag=True, help='List locally saved runs without making API calls.')
@click.option('--run-info', metavar='RUN_ID', help='Show usage and per-attempt telemetry without making API calls.')
@click.option('--acknowledge-pending', is_flag=True, help='On resume, acknowledge an interrupted request; retain its unknown-usage reservation.')
def cli(debug, story_file, seed, max_steps, database, resume, list_runs, run_info, acknowledge_pending, **options):
    load_dotenv(Path(__file__).with_name('.env'), override=False)
    environment = {
        'model': 'OPENAI_MODEL', 'memory_model': 'OPENAI_MEMORY_MODEL',
        'reasoning': 'OPENAI_REASONING_EFFORT', 'memory_mode': 'AI_MEMORY_MODE',
        'timeout': 'AI_TIMEOUT', 'retries': 'AI_RETRIES',
        'max_output_tokens': 'AI_MAX_OUTPUT_TOKENS', 'context_budget': 'AI_CONTEXT_BUDGET',
        'request_budget': 'AI_REQUEST_BUDGET', 'token_budget': 'AI_TOKEN_BUDGET',
        'memory_reasoning': 'OPENAI_MEMORY_REASONING_EFFORT', 'recent_turns': 'AI_RECENT_TURNS',
        'memory_update_interval': 'AI_MEMORY_UPDATE_INTERVAL', 'memory_input_budget': 'AI_MEMORY_INPUT_BUDGET',
        'memory_context_budget': 'AI_MEMORY_CONTEXT_BUDGET', 'memory_limit': 'AI_MEMORY_LIMIT',
        'memory_max_output_tokens': 'AI_MEMORY_MAX_OUTPUT_TOKENS',
    }
    values = {key: value if value is not None else os.getenv(environment[key]) for key, value in options.items()}
    values = {key: value for key, value in values.items() if value is not None}
    store = None
    try:
        if sum(bool(mode) for mode in (resume, list_runs, run_info)) > 1:
            raise ValueError('Choose one of --resume, --list-runs, or --run-info.')
        if acknowledge_pending and not resume:
            raise ValueError('--acknowledge-pending requires --resume.')
        if (resume or list_runs or run_info) and (story_file or seed is not None or any(v is not None for v in options.values())):
            raise ValueError('Saved-run commands use saved configuration; omit story, seed, and model/budget overrides.')
        store = RunStore(database)
        if list_runs:
            out(json.dumps(store.list_runs(), ensure_ascii=False, indent=2))
            return
        if run_info:
            out(json.dumps(store.report(run_info), ensure_ascii=False, indent=2))
            return
        if resume:
            player = store.resume(resume)
            if acknowledge_pending:
                player.session.acknowledge_pending()
            engine = player.engine
        else:
            settings = Settings(**values)
            settings.check_compatibility()
            engine = GameEngine(Game(load_game_data(game_file=story_file), seed=seed))
            player = Player(engine, settings)
        if engine.running and not os.getenv('OPENAI_API_KEY', '').strip():
            raise PlayerError('OPENAI_API_KEY is missing. Add it to .env or the process environment.')
        if not resume:
            store.create(player)
        out(f'Run ID: {player.session.run_id}; database: {database}')
        play(engine, player, max_steps=max_steps, debug=debug)
        out(json.dumps(store.usage(player.session.run_id), ensure_ascii=False))
    except sqlite3.Error as error:
        raise click.ClickException('Local run database operation failed. Check its path, permissions, and format.') from error
    except (PlayerError, StoreError, ValueError, OSError) as error:
        # Pydantic errors can echo input values; summarize configuration failures.
        from pydantic import ValidationError
        if isinstance(error, ValidationError):
            fields = ', '.join(str(item['loc'][0]) for item in error.errors())
            raise click.ClickException(f'Invalid configuration fields: {fields}') from error
        raise click.ClickException(str(error)) from error
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    cli()
