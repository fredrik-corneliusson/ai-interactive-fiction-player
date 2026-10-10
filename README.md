# AI interactive fiction player

A console player for a Swedish Twine adventure. Play manually or let an AI
choose actions in character, using LangChain's OpenAI Responses integration and
locally managed full-history or hybrid memory context.

## Setup

Requires Python 3.10 or newer. Verified on Windows with Python 3.13.2.
Run these commands from the repository directory in Bash (Git Bash on Windows):

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
if [ ! -f .env ]; then cp .env.example .env; fi
```

The local `.venv` is already created in the prepared checkout. The copy command
preserves an existing `.env`. All Windows examples use forward slashes and the
virtual environment's Python executable directly, so activation is optional.

To activate it in Git Bash, run `source .venv/Scripts/activate`; you can then use
`python` instead of `./.venv/Scripts/python.exe` in the commands below.

On macOS/Linux, use `python3 -m venv .venv` and replace
`./.venv/Scripts/python.exe` with `./.venv/bin/python`. Optional activation is
`source .venv/bin/activate`.

`requirements.txt` lists direct dependencies with version bounds;
`requirements-lock.txt` records the exact installed versions for repeatable setup.
To update dependencies, install from `requirements.txt`, verify the application,
then regenerate the lock with `python -m pip freeze` using the virtual environment.

## Run

Manual play requires no API key:

```bash
./.venv/Scripts/python.exe game.py
```

For AI play, set `OPENAI_API_KEY` in `.env`, then run:

```bash
./.venv/Scripts/python.exe ai_player.py
```

AI play makes paid API requests. The default is `gpt-6-luna` with low reasoning
effort and one structured decision per turn; transient failures or invalid
decisions can trigger bounded retries. Full-history mode makes no memory calls;
hybrid mode adds one batched extraction request when an update is due.
Use `--debug` for safe model, response-status, usage, and latency
summaries. `.env` and `.venv` are ignored by Git.

For a bounded, non-interactive run:

```bash
./.venv/Scripts/python.exe ai_player.py --story-file data/del1.html --max-steps 3 --retries 0 --debug
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
| `--memory-model` | `OPENAI_MEMORY_MODEL` | `gpt-6-luna` (hybrid extractor) |
| `--reasoning` | `OPENAI_REASONING_EFFORT` | `low` |
| `--memory-mode` | `AI_MEMORY_MODE` | `full-history` |
| `--memory-reasoning` | `OPENAI_MEMORY_REASONING_EFFORT` | `low` |
| `--timeout` | `AI_TIMEOUT` | 30 seconds per request |
| `--retries` | `AI_RETRIES` | 2 additional attempts per decision |
| `--max-output-tokens` | `AI_MAX_OUTPUT_TOKENS` | 2048 (includes reasoning tokens) |
| `--context-budget` | `AI_CONTEXT_BUDGET` | 64000 conservative decision input estimate |
| `--request-budget` | `AI_REQUEST_BUDGET` | 100 attempts per saved run |
| `--token-budget` | `AI_TOKEN_BUDGET` | 1000000 tokens per saved run |
| `--recent-turns` | `AI_RECENT_TURNS` | 4 complete action pairs plus current observation |
| `--memory-update-interval` | `AI_MEMORY_UPDATE_INTERVAL` | 4 encountered sources per update |
| `--memory-input-budget` | `AI_MEMORY_INPUT_BUDGET` | 32000 conservative extraction input estimate |
| `--memory-context-budget` | `AI_MEMORY_CONTEXT_BUDGET` | 8000 bytes of retrieved memory JSON |
| `--memory-limit` | `AI_MEMORY_LIMIT` | 8 recalled records per decision |
| `--memory-max-output-tokens` | `AI_MEMORY_MAX_OUTPUT_TOKENS` | 4096, including reasoning |

Supported model IDs are explicitly `gpt-6-luna` and `gpt-6.1-sol`; there is no
automatic fallback. Sol requires `low` reasoning or higher. No temperature or
other sampling override is sent. Sol's account access has not been live-tested.

Full-history mode sends every encountered observation and successfully applied
decision. It does not send hidden variables or unvisited passages. Responses use
`store=False`, and no server-side response chain is used. History and game state
are saved locally after each successful action.

The context budget uses a conservative UTF-8 byte estimate plus schema/envelope
allowance, rather than an exact provider token count. If exceeded, the player
stops before making a request and preserves all history. It never silently trims
or summarizes the full-history baseline. Increase the budget explicitly if needed.

## Hybrid memory

To try hybrid mode with a bounded run in Git Bash:

```bash
./.venv/Scripts/python.exe ai_player.py --story-file data/del1.html --memory-mode hybrid --max-steps 8 --retries 0 --request-budget 10 --debug
```

The request budget counts both decisions and memory extraction, so this run sends
at most 10 requests and may stop before eight actions. Full-history remains the
default and comparison baseline; hybrid quality has not yet been benchmarked.

Hybrid mode combines recent verbatim experience with retrieved event, entity, and
intention records. Extraction normally runs before a decision when four new
encountered sources are available, or sooner when an unprocessed source would
leave the recent window. Each source is an initial observation or a committed
action with its resulting observation and separately labeled character comment.
One request handles all entities in the oldest pending batch; there are no
per-entity calls or automatic extraction retries.

Each record has exact source quotes, event IDs, turn/passage references, salience,
and an epistemic label: quoted `observation`, expressed `character_belief`, or
generated `interpretation`. Observation records must be verbatim excerpts and
cannot cite character comments. A quote records what appeared in the narrative,
including possibly unreliable dialogue, rather than establishing world truth.
Contradictory evidence is retained as separate records. Local validation rejects
unknown sources, fabricated quotes, and entity tags absent from the evidence;
it does not prove that a model's interpretation or classification is correct.

Retrieval uses keyword overlap, matching entity tags, salience, an intention
bonus, and recency to break ties. It selects at most eight records that fit the
memory JSON byte allowance, then adds the configured recent window. Older
unprocessed sources remain verbatim until extracted. Extraction batches include
complete sources only; neither extraction nor the final decision context silently
truncates an oversized source. The overall decision input budget applies to the
assembled prompt, including persona and memory wrapper.

The full transcript remains intact in SQLite. Derived memories, batch progress,
and selected-context logs persist across resume. Failed extraction stops before
an action and preserves its source backlog; a successful extraction can remain
saved even if the following decision fails. Terminal passages make no further
requests, so a final pending batch may remain unextracted. Memory requests share
the run's request/token budgets and interrupted-request recovery policy.

Use `m` to inspect stored records, selected records, pending sources, and the last
logged context. Inspection makes no API calls; when `update_due` is true, the
selection is a preview that can change after the next extraction. Use `h` for
the complete transcript or `--run-info RUN_ID` for persisted memory and call logs.
No embeddings, automated contradiction resolution, or deliberate forgetting are
implemented. These are experiments for later evaluation, not assumptions about
human memory. See [CP4_VALIDATION.md](CP4_VALIDATION.md).

Both modes download Part 1 from `https://fordarvet.netlify.app/del1`
on their first run and cache it in `data/twine_game.html` (ignored by Git).
An internet connection is required for the first download and for AI play.
Run from the repository directory because the story cache uses a relative path.

The prepared checkout also includes both games from
[För­därvet](https://fordarvet.netlify.app/) as `data/del1.html` and
`data/del2.html`. Open either HTML file in a browser to play the original game.
Part 1 is the active console cache; the previous download is preserved as
`data/twine_game.previous.html`. Select a story without replacing the cache:

```bash
./.venv/Scripts/python.exe game.py --story-file data/del2.html --seed 7
./.venv/Scripts/python.exe ai_player.py --story-file data/del1.html --seed 7
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
- `m`: inspect hybrid memories and selection; in full-history mode, show history.
- `h`: show the complete encountered history and committed decisions in either mode.
- `v`: show variables and the current passage.
- `u`: show cumulative request, token, and latency accounting.
- `s`: explicitly save the current checkpoint (actions already save automatically).
- `q`: quit.

## Saved runs and budgets

AI play prints a run ID and stores its story snapshot, settings, persona, complete
encountered transcript, rendered passage, variables, and random state in
`data/runs.sqlite3`. This database and its journals are ignored by Git. Select
another path with `--database`; ignore any custom database you place in the repo.
No new dependency is needed: persistence uses Python's SQLite library.

```bash
./.venv/Scripts/python.exe ai_player.py --story-file data/del1.html --max-steps 3 --request-budget 10 --token-budget 100000
./.venv/Scripts/python.exe ai_player.py --list-runs
./.venv/Scripts/python.exe ai_player.py --run-info RUN_ID
./.venv/Scripts/python.exe ai_player.py --resume RUN_ID --max-steps 3
```

Replace `RUN_ID` with the printed ID. Listing and inspection require no API key or
network. Resume uses the saved configuration and embedded story, even if the
original HTML has changed or disappeared; omit story, seed, model, and budget
options. Environment defaults affect new runs only. The API key is read from the
current environment and is never saved in the database. A completed run resumes
without applying its last action again or calling the model.

Request and token budgets cover the whole saved run, including retries and
resumes. Before each attempt the player reserves the conservative input estimate
plus the maximum output allowance. Reported provider usage replaces that
reservation when available; otherwise the reservation remains charged.
`known_tokens` is reported usage, `budget_tokens` includes unknown-usage
reservations, and `unknown_usage_requests` makes the difference explicit.
Latency is measured per attempt, excluding retry backoff. No monetary estimate
or pricing assumption is used. The token budget is conservative local accounting,
not an exact provider billing cap; it can stop earlier than actual usage requires.

An action, observation, and checkpoint commit together. The append-only event log
records every attempt, including refusals, invalid decisions, and failures, with
safe categories rather than provider error bodies. Competing sessions are rejected
before sending another request or overwriting newer state.

A hard interruption can leave an attempt with unknown outcome. Resume blocks
further requests until you inspect it with `--run-info` and explicitly acknowledge
it using `--resume RUN_ID --acknowledge-pending`. Do this after the original
process has stopped. Its token reservation remains charged; no action is replayed.
Known latency totals exclude interrupted attempts whose duration is unknown.
The database contains story and transcript text and is ordinary local SQLite,
without encryption or a tamper-proof audit guarantee. Back it up after closing
the player so its SQLite journal is flushed.

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
- `run_store.py` owns SQLite checkpoints, append-only events, run isolation, and
  persisted budget accounting. See [CP3_VALIDATION.md](CP3_VALIDATION.md).
- `memory.py` defines cited memory extraction, provenance validation, and
  deterministic bounded retrieval. SQLite schema version 2 adds derived records
  while preserving existing CP3 runs.
- Comparative memory/model evaluation is the next checkpoint.

## Verification

Run the offline regression suite (no API key, browser, or game download needed):

```bash
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

The optional whole-story audit requires the downloaded games:

```bash
./.venv/Scripts/python.exe scripts/audit_stories.py
```
