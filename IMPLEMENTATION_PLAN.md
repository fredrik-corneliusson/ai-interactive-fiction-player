# AI player modernization: design and implementation plan

Created: 2026-10-10 (Europe/Stockholm)
Last updated: 2026-10-10
Status: CP1 through CP4 complete for their documented scopes.
Next checkpoint: CP5 — comparative model and memory evaluation.

## Purpose and scope

Modernize the existing console Twine player to current LangChain and OpenAI
interfaces, make gameplay reliable, and evaluate whether explicit memory improves
believable character behavior compared with a modern full-history baseline.

This document is the working reference for future sessions. Update checkpoint
status, evidence, decisions, and the next action as work proceeds. A checkpoint is
a reviewable milestone, not an automatic Git commit or a requirement to pause for
approval. Continue authorized work unless the user requests a pause or a decision
requires clarification.

## Starting baseline (before CP1)

- Python 3.13.2 virtual environment exists at `.venv` on Windows.
- Dependencies are installed, bounded in `requirements.txt`, and frozen in
  `requirements-lock.txt`. The packages were refreshed during environment setup;
  the application architecture remains legacy.
- Imports use `langchain-openai`, `langchain-core`, and `langchain-classic`.
- `ai_player.py` still uses deprecated `ConversationChain` and
  `ConversationEntityMemory`. Its implicit `ChatOpenAI()` model is
  `gpt-3.5-turbo` in the installed package.
- Entity memory places three recent turn pairs in the prompt, retrieves summaries
  for entities extracted from the current passage, and makes approximately
  `2 + extracted entity count` model calls per turn, excluding retries.
- Memory is stored in RAM and is lost when the process exits.
- The AI choice parser assumes a valid numeric response; the AI loop does not
  automatically stop at a terminal passage.
- `game.py` implements a limited, story-specific subset of Harlowe/Twine syntax.
  Part 2 includes additional macros such as `elseif` and `random`.
- Both games are downloaded: `data/del1.html` (343 passages) and
  `data/del2.html` (415 passages). Part 1 is the active `data/twine_game.html`
  cache. The previous cache is preserved in `data/twine_game.previous.html`.
- Completed checks: dependency consistency, imports, offline classic-chain
  execution, basic synthetic navigation, Part 1 parsing and first transition,
  and manual CLI startup/quit. These do not establish full story compatibility.
- The user reports adding their API key to `.env`. No live model request or
  account/model availability check has been performed. Never include the key in
  this document, logs, test fixtures, or version control.

## Design decisions

### Model interface and orchestration

Keep `langchain-openai` and `langchain-core`; remove `langchain-classic` once the
replacement works. Use explicit prompt construction, model invocation, and
memory updates. The game controls the turn sequence; a general tool-using agent
is unnecessary for the initial implementation.

Use `ChatOpenAI(use_responses_api=True)` with explicit model IDs. Configure model,
reasoning effort, timeouts, retries, output limits, and memory mode through CLI
options and/or environment variables. Apply model-specific parameter validation:
do not send unsupported sampling or reasoning settings.

Initial candidates from the official documentation reviewed on 2026-10-10:

| Role | Candidate | Validation needed |
| --- | --- | --- |
| Economical player and memory updater | `gpt-6-luna` | Account access, Swedish voice, decision quality, latency |
| Higher-quality player comparison | `gpt-6.1-sol` | Account access, improvement over economical model, cost |

These are benchmark candidates, not confirmed account capabilities or permanent
defaults. Recheck official documentation before implementation and record the
actual tested model IDs and settings. Do not silently substitute another model.

LangGraph is optional later if branching workflows or checkpoint orchestration
justify it. Persistent local state does not require an agent framework.

### Decision contract and turn sequence

Return structured data with `choice` and `character_comment`, using native
Structured Outputs. The comment is short in-character narration or dialogue.
Validate the choice against the current available actions even when the response
matches the schema. Handle refusal, incomplete output, invalid choices, API
errors, and timeouts explicitly, with bounded retries.

The intended turn sequence is:

1. Render the current passage and available choices.
2. Stop if the passage is terminal; do not request another decision.
3. Build context from the persona, current observation, and selected memory mode.
4. Request and validate a structured decision.
5. Apply the selected action once, recording the action and resulting observation.
6. Persist the event and update memory as needed.

Keep decision generation separate from action application. Failed or retried
requests must not advance the game, and resuming must not repeat a committed
action.

### Knowledge boundaries

The engine may load the complete Twine file, but the player model receives only
observations from passages the character has encountered and visible choices.
Do not include unvisited branches, hidden engine variables, or external research
about the story in the player's context. Current beliefs may differ from engine
truth. Memory is scoped to one playthrough unless an explicit carry-over design
is introduced later.

### Memory architecture

Retain an append-only event log as the source of truth, plus derived memory
records in local SQLite storage. Link each derived record to source turn and
passage references. Keep observations, character beliefs, and generated
interpretations distinguishable; do not silently replace contradictory evidence.

| Layer | Contents |
| --- | --- |
| Recent experience | A configurable window of verbatim passages and actions |
| Event memories | Significant encounters, decisions, and consequences |
| Entity knowledge | Beliefs about people, places, and relationships |
| Current intentions | Goals, promises, suspicions, and unresolved plans |
| Retrieval | A bounded selection relevant to the current situation |

Batch memory extraction into at most one update request per update cycle rather
than one request per entity. Start with a simple, inspectable retrieval strategy;
add embeddings only if evaluation shows that they help. Decide the update cadence
and retrieval/token budgets during CP4 and record them here.

Persist game position, variables, random state where needed, transcript, persona,
model configuration, memory records, and run identity for save/resume. Choose one
owner for conversation context: construct it locally for the initial design,
rather than also chaining server-side response history and accidentally restoring
memories excluded by the selected memory policy.

### Memory modes and interpretation

Implement two modes with the same persona and decision contract:

- `full-history`: all observed passages and actions, subject to an explicit token
  budget. If the budget is exceeded, stop or use a documented fallback; do not
  silently call truncated history a full-history baseline.
- `hybrid`: recent experience plus retrieved events, entity knowledge, and
  intentions.

Entity summaries remain useful for compression and retrieval despite larger
context windows. They resemble semantic knowledge, but are not a validated model
of human memory. A believable character may also need episodic experience,
salience, uncertainty, and changing beliefs. A complete transcript also implies
unusually complete recall.

Treat this architecture as a character simulation hypothesis to evaluate.
Deliberate forgetting, biased recall, emotional salience, and reflection are
optional follow-up experiments after a reliable baseline exists. Preserve the
event log even if a character memory policy intentionally forgets information.

## Checkpoints and acceptance criteria

| Checkpoint | Status | Deliverable |
| --- | --- | --- |
| CP0 — environment and design | Complete | Local environment, downloaded stories, this plan |
| CP1 — engine and fixtures | Complete | Reliable turn behavior and representative story fixtures |
| CP2 — modern full-history player | Complete | Responses API, explicit configuration, structured decisions |
| CP3 — persistence and observability | Complete | Save/resume, event log, usage and latency reporting |
| CP4 — hybrid memory | Complete | Batched extraction, retrieval, inspectable derived memories |
| CP5 — comparative evaluation | Not started | Model/memory comparison and justified defaults |
| CP6 — documentation and handoff | Not started | Reproducible setup, CLI guide, validated dependency lock |

### CP1 — engine correctness and evaluation fixtures

- Inspect conditional rendering and link handling; test representative Part 1
  passages against the original browser game, including variable changes.
- Fix invalid action handling and terminal behavior in the engine and AI loop.
- Honor the Twine start passage instead of assuming file order where appropriate.
- Add explicit story-file selection so Part 1 and Part 2 do not require overwriting
  the active cache.
- Audit additional Part 2 macros. Implement supported semantics with fixtures or
  report unsupported constructs clearly. Do not claim full compatibility from
  successful parsing alone.
- Create fixed encountered-history fixtures for later memory and model evaluation.

Acceptance: relevant offline tests pass; valid actions advance once, invalid
actions do not mutate state, terminal passages stop, and documented conditional
fixtures match browser behavior. Record supported story scope and remaining gaps.

### CP2 — modern full-history player

- Replace the classic chain/memory path with explicit modern model calls.
- Add configurable player and memory model IDs and model-compatible settings.
- Implement structured decisions and bounded error handling.
- Preserve Swedish roleplay intent in a clearly separated persona prompt.
- Add `full-history` mode and token-budget behavior.
- Remove unused legacy dependencies and refresh the lock after verification.

Acceptance: offline response/validation tests pass; a bounded live smoke run
confirms model access, Responses API use, structured output, and valid transitions.
Record actual model/settings and any refusals or compatibility problems.

### CP3 — persistence and observability

- Add local SQLite run storage and an append-only event log.
- Implement save/resume for engine state, transcript, and configuration.
- Log per-call usage, latency, model identity, and error category without secrets.
- Provide bounded autorun and a configurable request/token or estimated-cost budget.
  Label costs as estimates and record the pricing assumptions used.

Acceptance: resuming a saved run restores state and history without duplicating
actions; separate runs remain isolated; usage and latency can be reviewed.

### CP4 — hybrid memory

- Implement the memory layers and source-linked structured records above.
- Batch extraction, preserve conflicting observations, and prevent interpretations
  or character comments from silently becoming objective facts.
- Choose and document update cadence, retrieval ranking, and context budgets.
- Make `m` show the stored memories and the memories selected for the current turn.
- Use recent context and relevant records to build the hybrid prompt.

Acceptance: fixtures verify earlier-event recall, source references, contradiction
handling, memory isolation, and exclusion of unvisited/hidden information. Logs
demonstrate batched updates and bounded context assembly.

### CP5 — comparative evaluation

- Compare full-history and hybrid modes on identical encountered-history fixtures,
  with the same persona and settings. Use repeated trials where practical.
- Compare candidate models for factual recall, valid choices, Swedish character
  consistency, invented memories, latency, token usage, and estimated cost.
- Include bounded gameplay runs and representative scenes with challenging story
  content; record refusals instead of assuming all passages will be playable.
- Distinguish reliable game execution from subjective roleplay quality. Story
  completion is not automatically the best measure of believable characterization.
- Select defaults from the evidence; add embeddings or simulated forgetting only
  if a concrete evaluation need emerges.

Acceptance: a reproducible evaluation report records fixtures, prompts, models,
settings, sample counts, results, limitations, and the rationale for defaults.

### CP6 — documentation and handoff

- Document installation, story selection, model settings, memory modes, commands,
  save/resume, costs, and supported Twine features.
- Update `.env.example` with configuration placeholders only; preserve `.env`.
- Verify the final dependency lock and normal manual/AI startup paths.
- Update this plan with completed checkpoints, evidence, and deferred work.

Acceptance: a fresh environment can follow the documented setup and relevant
checks; known limitations and any unfinished scope are explicit.

## CP1 checkpoint outcome

Completed on 2026-10-10. See [CP1_VALIDATION.md](CP1_VALIDATION.md) for scope,
reproduction commands, browser reference cases, and remaining limitations.

- Added the limited Harlowe interpreter in `twine.py`, metadata-aware story loading,
  transactional transitions, strict action validation, terminal handling, and
  `--story-file`/`--seed` options in both CLIs.
- Verified 11 original-browser cases and 6,822 isolated passage/state checks across
  both games. The offline suite contains 18 passing tests and committed browser
  reference excerpts plus two fixed encountered-history traces.
- Preserved the browser's authored bare-link conditional behavior in `vadnu`.
  Browser storage/restart/external navigation remain unavailable and are reported
  as notices; arbitrary interactive hooks and unknown syntax fail explicitly.
- CP1 used offline/mock AI checks only. The API key was not read or changed and
  no paid API requests were made. The AI model and classic chain remain for CP2.

Next action: recheck current model/API compatibility, then replace the classic
chain with explicitly configured Responses calls and structured decisions, using
these fixtures as the CP2 baseline. Do not mark full-game compatibility complete
based on the isolated-passage audit.

Development branch: `development/modernize-ai-player`. The CP1 Git checkpoint
includes the environment setup and story-source configuration that CP1 depends
on. Local credentials, downloaded full games, browser tooling, and caches remain
ignored. The checkpoint commit is titled `Complete CP1 engine and fixture milestone`.

## Progress log

CP2 completed on 2026-10-10. See [CP2_VALIDATION.md](CP2_VALIDATION.md) for actual
request settings, usage, verification, and remaining scope. The classic chain and
entity memory are removed; `player.py` uses Responses/structured decisions and
locally owned full history. Live access to `gpt-6-luna` was confirmed. Sol remains
an explicit, untested comparison option. The CP2 Git checkpoint on
`development/modernize-ai-player` is titled `Complete CP2 Responses and full-history milestone`.

CP3 completed on 2026-10-10. See [CP3_VALIDATION.md](CP3_VALIDATION.md) for the
persistence schema, budget policy, crash recovery, and verification. SQLite stores
independent run checkpoints and append-only encountered/attempt events. Resume
restores rendered state and RNG without replaying macros. Story snapshots preserve
run identity after source changes; saved settings and persona remain fixed across
resumes. Requests are reserved before invocation and all attempts count toward
persisted request/token budgets, including unknown usage. Monetary estimates are
deferred, so no pricing assumption is needed. Stale sessions are rejected and
interrupted requests require explicit acknowledgment. All 48 offline tests and
pip check pass; no additional paid API requests were made. The CP3 Git checkpoint
is titled `Complete CP3 persistence and observability milestone`.

CP4 completed on 2026-10-10. See [CP4_VALIDATION.md](CP4_VALIDATION.md) for the
schema, update/retrieval policies, evidence, and limitations. Hybrid memory uses
recent verbatim experience and bounded event/entity/intention records with exact
run-scoped citations and explicit observation/belief/interpretation labels.
Extraction batches four new sources by default, earlier before recent context
would lose an unprocessed source, with at most one extraction request per step
and no automatic extraction retries. Defaults: four recent turn pairs; 32000
extractor input estimate; 4096 extractor output tokens; eight recalled records
within 8000 JSON bytes. Decision input still has the overall context budget.
Retrieval ranks lexical overlap, entity matches, salience, and intentions, with
recency tie-breaking. No embeddings or automated forgetting are introduced.
Contradictions are preserved; exact source validation does not prove semantic
accuracy of an interpretation. SQLite version 2 preserves CP3 runs and adds
append-only records, batch progress, and selected-context logs. All 69 tests and
pip check pass in Git Bash; no paid requests were made. The CP4 Git checkpoint
is titled `Complete CP4 hybrid memory milestone`.

Next action: implement CP5 reproducible full-history/hybrid comparison on fixed
encountered-history fixtures, using repeated trials where practical. First confirm
live memory-schema acceptance, then measure recall/source fidelity, contradictions,
invented memories, valid decisions, Swedish characterization, refusals, usage,
and latency. Retain the original transcript, hidden/unvisited-data boundaries,
and CP3 persisted budgets. Full-history stays the default pending evidence.

| Date | Checkpoint | Work and evidence | Remaining work |
| --- | --- | --- | --- |
| 2026-10-10 | CP0 | Created Python 3.13.2 `.venv`; installed dependencies; `pip check` passed; verified imports, offline classic-chain execution, synthetic navigation, and manual CLI startup/quit. | Replace legacy architecture and add meaningful regression tests. |
| 2026-10-10 | CP0 | Downloaded both Netlify games; verified 343 Part 1 and 415 Part 2 passages; checked Part 1's first transition. Updated the default source URL. | Broader conditional and Part 2 compatibility checks. |
| 2026-10-10 | CP0 | Reviewed current OpenAI/LangChain documentation and local memory implementation. Recorded design, checkpoints, and acceptance criteria. No modernization code or paid API calls made during planning. | Start CP1, then establish the CP2 full-history baseline. |
| 2026-10-10 | CP1 | Implemented engine/CLI fixes and limited Harlowe support; 18 offline tests, 11 browser cases, and 6,822 audit checks pass. Added persistent reference fixtures and CP1_VALIDATION.md. | CP2 Responses/full-history integration; exhaustive playthrough coverage remains outside CP1 validation. |
| 2026-10-10 | CP2 | Replaced classic chain/entity memory with Responses, strict structured decisions, explicit settings, bounded errors/retries, full-history context budgeting, and bounded CLI runs. 32 tests and pip check pass; four live Luna requests succeeded. Removed unused dependencies and refreshed the lock. | CP3 persistence and complete usage accounting; Sol access and roleplay comparisons remain untested. |
| 2026-10-10 | CP3 | Added SQLite checkpoints and append-only events; story/persona/settings/transcript/RNG save and resume; atomic actions, revision protection, per-attempt usage/latency/error logs, persistent request/token budgets, and explicit interruption recovery. 48 offline tests and pip check pass. Added CP3_VALIDATION.md and CLI documentation. No paid calls. Git checkpoint: `Complete CP3 persistence and observability milestone`. | CP4 hybrid memory, batched extraction, source-linked retrieval, and inspection. |
| 2026-10-10 | CP4 | Implemented hybrid recent/retrieved context; batched strict memory extraction; exact source/quote validation; separate observations, beliefs, and interpretations; deterministic bounded retrieval; append-only SQLite records and progress; shared budgets and purpose telemetry; m/h inspection and Git Bash guide. 69 tests and pip check pass. Added CP4_VALIDATION.md. No paid calls. Git checkpoint: `Complete CP4 hybrid memory milestone`. | CP5 live schema acceptance and comparative evaluation; memory and roleplay quality remain unbenchmarked. |

## How to update this reference

At the start of a work session, read this document and inspect the working tree.
Preserve existing user edits. Confirm the next checkpoint and any unresolved
decisions before changing dependent behavior.

For each checkpoint:

1. Mark it `In progress` when implementation begins.
2. Record material design decisions and deviations, including their reasons.
3. Add verification evidence: command or report location, result, and limitations.
4. Mark it `Complete` only when its acceptance criteria are met. Use `Partial`
   when implemented work still lacks required validation.
5. Update `Last updated`, `Next checkpoint`, and the progress log.

For a handoff or interruption, record the exact next action and any blockers here.
Do not infer completion from dependencies being installed or a model request
merely succeeding. Keep credentials and private API response identifiers out of
the plan. Git commits can serve as optional checkpoints when explicitly requested.

## Sources and freshness

The recommendations below were reviewed on 2026-10-10. Model availability,
parameters, and pricing must be rechecked before live implementation or benchmarking.

- [OpenAI Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [OpenAI current model and migration guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [GPT-6 Luna specifications](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [GPT-6.1 Sol specifications](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [LangChain OpenAI integration and Responses support](https://docs.langchain.com/oss/python/integrations/chat/openai)
- [LangChain migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1)
- [LangChain short-term memory](https://docs.langchain.com/oss/python/langchain/short-term-memory)
- [LangChain memory concepts](https://docs.langchain.com/oss/python/concepts/memory)
- [Generative Agents research](https://arxiv.org/abs/2304.03442)

The Generative Agents paper is an architectural precedent for believable simulated
behavior, not evidence that this project's proposed memory system reproduces human
cognition.
