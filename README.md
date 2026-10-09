# AI interactive fiction player

A console player for a Swedish Twine adventure. Play manually or let an AI
choose actions in character, with entity memory managed by LangChain.

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

AI play makes paid API requests, including additional requests for entity memory.
Use `--debug` to show chain details. `.env` and `.venv` are ignored by Git.

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
- `m`: show entity memory.
- `v`: show variables and the current passage.
- `q`: quit.

## Repository notes

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the modernization design,
implementation checkpoints, acceptance criteria, and progress log.

- `game.py` downloads and parses Twine passages, handles a limited subset of
  Twine macros, and provides the manual game loop.
- `ai_player.py` supplies the character prompt and connects the game to
  `ChatOpenAI`, `ConversationChain`, and `ConversationEntityMemory`.
- The original 2023 imports have been updated to `langchain-openai`,
  `langchain-core`, and `langchain-classic`. The classic chain and memory APIs
  are deprecated upstream but preserve the existing application design.
  See the [LangChain migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1).
- The console parser supports the audited subset of Harlowe in both downloaded
  games, including nested conditionals, arithmetic, and random outcomes. Browser
  save/load/restart controls and external score pages are unavailable; `v` shows
  notices. Other unsupported syntax raises a passage-specific error.
- The AI still uses the legacy model default and entity-memory architecture;
  modernization of that integration is the next checkpoint.

## Verification

Run the offline regression suite (no API key, browser, or game download needed):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The optional whole-story audit requires the downloaded games:

```powershell
.\.venv\Scripts\python.exe scripts/audit_stories.py
```
