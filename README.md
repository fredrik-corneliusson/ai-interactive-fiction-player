# AI interactive fiction player

A console player for a Swedish Twine adventure. Play manually or let an AI
choose actions in character, using LangChain's OpenAI Responses integration and
locally managed full-history context.

## Setup

Requires Python 3.10 or newer. Verified on Windows with Python 3.13.2.
Run these commands from the repository directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

The local `.venv` is already created in the prepared checkout. Using its Python
executable directly avoids PowerShell activation policy issues.
Optional activation: `.\.venv\Scripts\Activate.ps1`.

On macOS/Linux, use `python3 -m venv .venv`, `.venv/bin/python` instead of the
Windows executable, and `cp .env.example .env`.

`requirements.txt` lists direct dependencies with version bounds;
`requirements-lock.txt` records the exact installed versions for repeatable setup.
To update dependencies, install from `requirements.txt`, verify the application,
then regenerate the lock with `python -m pip freeze` using the virtual environment.

## Run

Manual play requires no API key:

```powershell
.\.venv\Scripts\python.exe game.py
```

For AI play, set `OPENAI_API_KEY` in `.env`, then run:

```powershell
.\.venv\Scripts\python.exe ai_player.py
```

AI play makes paid API requests. The default is `gpt-6-luna` with low reasoning
effort and one structured decision per turn; transient failures or invalid
decisions can trigger bounded retries. There are no entity-summary calls in this
milestone. Use `--debug` for safe model, response-status, usage, and latency
summaries. `.env` and `.venv` are ignored by Git.

For a bounded, non-interactive run:

```powershell
.\.venv\Scripts\python.exe ai_player.py --story-file data/del1.html --max-steps 3 --retries 0 --debug
```

This makes at most three generation requests with retries disabled. The title
screen counts as a decision. Errors exit with a nonzero status without applying
the rejected action. Interactive errors stop autorun and let you inspect state
or quit.

## AI configuration

CLI options override process environment variables, which override `.env` values.
Unset settings use the defaults below. Your existing `.env` need only contain the
API key; see `.env.example` for optional settings.

| Option | Environment variable | Default |
| --- | --- | --- |
| `--model` | `OPENAI_MODEL` | `gpt-6-luna` |
| `--memory-model` | `OPENAI_MEMORY_MODEL` | `gpt-6-luna` (reserved for CP4; unused here) |
| `--reasoning` | `OPENAI_REASONING_EFFORT` | `low` |
| `--memory-mode` | `AI_MEMORY_MODE` | `full-history` |
| `--timeout` | `AI_TIMEOUT` | 30 seconds per request |
| `--retries` | `AI_RETRIES` | 2 additional attempts per decision |
| `--max-output-tokens` | `AI_MAX_OUTPUT_TOKENS` | 2048 (includes reasoning tokens) |
| `--context-budget` | `AI_CONTEXT_BUDGET` | 64000 conservative input token estimate |

Supported model IDs are explicitly `gpt-6-luna` and `gpt-6.1-sol`; there is no
automatic fallback. Sol requires `low` reasoning or higher. No temperature or
other sampling override is sent. Sol's account access has not been live-tested.

Full-history mode sends every encountered observation and successfully applied
decision. It does not send hidden variables or unvisited passages. Responses use
`store=False`, and no server-side response chain is used. History currently lasts
only for the running process; save/resume belongs to CP3 and hybrid memory to CP4.

The context budget uses a conservative UTF-8 byte estimate plus schema/envelope
allowance, rather than an exact provider token count. If exceeded, the player
stops before making a request and preserves all history. It never silently trims
or summarizes the full-history baseline. Increase the budget explicitly if needed.

Both modes download Part 1 from `https://fordarvet.netlify.app/del1`
on their first run and cache it in `data/twine_game.html` (ignored by Git).
An internet connection is required for the first download and for AI play.
Run from the repository directory because the story cache uses a relative path.

The prepared checkout also includes both games from
[För­därvet](https://fordarvet.netlify.app/) as `data/del1.html` and
`data/del2.html`. Open either HTML file in a browser to play the original game.
Part 1 is the active console cache; the previous download is preserved as
`data/twine_game.previous.html`. Select a story without replacing the cache:

```powershell
.\.venv\Scripts\python.exe game.py --story-file data/del2.html --seed 7
.\.venv\Scripts\python.exe ai_player.py --story-file data/del1.html --seed 7
```

The seed makes random outcomes repeatable. The AI character prompt was written
for Part 1. Supported console syntax and verification limits are documented in
[CP1_VALIDATION.md](CP1_VALIDATION.md).

## Console commands

Manual play: enter a numbered choice, `v` to inspect variables and interpreter
notices, or `q` to quit. Both modes stop at passages without story choices.

AI play:

- Enter: ask the AI to choose the next action.
- `r #`: automatically run the requested number of steps.
- `m`: show the complete encountered history and committed decisions.
- `v`: show variables and the current passage.
- `q`: quit.

## Repository notes

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the modernization design,
implementation checkpoints, acceptance criteria, and progress log.

- `game.py` downloads and parses Twine passages, handles a limited subset of
  Twine macros, and provides the manual game loop.
- `ai_player.py` provides the AI console and configuration.
- `player.py` supplies the Swedish persona, structured decision contract,
  Responses model configuration, full-history context, and retry/error handling.
- The deprecated classic chain and entity memory have been removed. Dependencies
  use `langchain-openai`, `langchain-core`, and the OpenAI SDK. See
  [CP2_VALIDATION.md](CP2_VALIDATION.md) for validation evidence and remaining scope.
- The console parser supports the audited subset of Harlowe in both downloaded
  games, including nested conditionals, arithmetic, and random outcomes. Browser
  save/load/restart controls and external score pages are unavailable; `v` shows
  notices. Other unsupported syntax raises a passage-specific error.
- Persistence and a durable event log are the next checkpoint.

## Verification

Run the offline regression suite (no API key, browser, or game download needed):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The optional whole-story audit requires the downloaded games:

```powershell
.\.venv\Scripts\python.exe scripts/audit_stories.py
```
