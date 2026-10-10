# CP4: hybrid memory validation

Completed: 2026-10-10. Branch: `development/modernize-ai-player`.
Git checkpoint: `Complete CP4 hybrid memory milestone`.

## Design and implementation

`full-history` remains the default and retains every encountered observation and
committed decision. `--memory-mode hybrid` instead assembles the same persona,
a bounded retrieved-memory message, and recent verbatim action/observation pairs.
The complete transcript remains stored even when excluded from the prompt.

`memory.py` adds a strict `MemoryBatch` schema and three derived layers:
`event`, `entity`, and `intention`. Each record has a text, entity tags, salience
1–5, an epistemic label, and supporting source IDs/channel/exact quote. Local
storage adds encountered turn/passage references. The extractor receives only
committed encountered observations, explicitly labeled selected-action text,
and separate character comments. It receives no full story, hidden variables,
internal passage titles, persona assumptions, or external story research.

Epistemic labels deliberately describe evidence rather than assert engine truth:

- `observation`: a verbatim quote from an encountered observation or committed
  action. Its record text must equal a cited quote. Dialogue and uncertain
  narrative remain what was encountered, not independently verified facts.
- `character_belief`: an expressed thought, intention, or character comment;
  classification is model generated and may be wrong.
- `interpretation`: a model-generated interpretation explicitly marked uncertain.

Citation IDs must belong to the supplied source batch and run. Quotes must be
exact nonempty substrings of their declared channel. Observation records cannot
cite character comments. Entity tags must occur in the supporting quotes.
Validation rejects malformed/fabricated citations and objective paraphrases;
it does not prove semantic entailment or correct attribution. Contradictions and
changing intentions stay as separate records rather than overwriting old evidence.

## Cadence and context policy

Default hybrid settings, persisted with each new run:

| Setting | Default | Policy |
| --- | --- | --- |
| Memory model | `gpt-6-luna` | Independent `--memory-model`; no automatic substitution |
| Memory reasoning | `low` | Separate from decision reasoning; compatibility checked |
| Recent window | 4 turns | Four complete action pairs plus current observation |
| Update interval | 4 sources | Initial observation and each committed action/result count as sources |
| Extractor input budget | 32000 | Conservative UTF-8/schema/envelope input estimate |
| Extractor maximum output | 4096 | Includes reasoning tokens |
| Retrieved-memory budget | 8000 bytes | JSON records, excluding static message wrapper |
| Retrieved-record limit | 8 | Maximum per decision |
| Decision context budget | 64000 | Entire assembled prompt, same accounting as full-history |

Before a decision, update when enough sources accumulate, or earlier when an
unprocessed source would leave the recent window. Make at most one extraction
request in that step, batching all entities in the oldest pending sources.
Extraction does not automatically retry; a failure stops the step before any
action. Decision retries retain their CP2 behavior.

If the oldest batch is too large, process the longest complete-source prefix
within the extractor input budget. Never cut a source to fit. Older unprocessed
sources remain in verbatim decision context until extracted, so temporary backlog
can extend the recent window. If even one complete source cannot fit, or assembled
decision context exceeds its budget, stop explicitly with transcript preserved.
Memory failures do not silently fall back to lossy history or an unlabelled mode.

Retrieval is deterministic and inspectable. Rank by word overlap with the current
observation, matching entity tags, salience, and an intention bonus; break ties
by newer record ID. Skip records that do not fit the byte budget, with no textual
truncation. No embeddings, external retrieval, automatic conflict resolution,
record deletion, or deliberate forgetting are used. Policy version `cp4-v1` is
logged with batch and context events.

## Persistence, observability, and failure behavior

SQLite schema version 2 adds run-scoped append-only derived records with batch
event references. Opening a version 1 database creates the additional table and
indexes/triggers without changing existing events or checkpoints. CP3 full-history
runs retain their mode; missing new configuration fields use defaults. Old CP3
code cannot open a version 2 database; there is no automatic downgrade.

An extraction batch commits its records and source cursor together. A failed
database write leaves both unchanged; the paid/attempted request remains logged.
Successful extraction can persist independently of a following rejected decision:
its sources were already committed, and resume does not re-extract them.
Revision protection also applies to memory writes and context logging.

Every API attempt uses the shared request implementation and CP3 budget ownership.
Events label `purpose=memory` or `decision` and retain model, usage, status,
latency, and safe category. Both purposes consume the same persisted request/token
budgets, including unknown-usage reservations and interruption recovery. Context
events record selected memory IDs, recent source IDs, memory bytes, and the full
input estimate. Terminal passages send no more requests, leaving any final
unprocessed sources intact in the transcript.

Interactive `m` displays stored records, current retrieval selection, pending
sources, input estimate, and the last logged context. Inspection never invokes a
model. If an update is due, `selection_stage=preview_before_update` distinguishes
the preview from the eventual next decision context. `h` displays the full
transcript in either mode. `--run-info` includes persisted memory and context logs.

## Verification

Run in Git Bash from the repository root:

```bash
source .venv/Scripts/activate
python -m unittest discover -s tests -v
python -m pip check
python ai_player.py --help
```

Results: **69 tests pass**, including 48 CP1–CP3 regressions and 21 CP4 tests;
`pip check` reports no broken requirements; CLI help exposes hybrid configuration.
No new dependencies or lock-file changes are required. `git diff --check` passes.

CP4 tests verify:

- Batched cadence, one extraction request per cycle, early updating for a short
  recent window, source IDs in logs, and combined request accounting.
- Conflicting Jenny observations survive together with their original source,
  turn, and passage references. Memory updates/deletes are rejected by SQLite.
- Unknown, foreign-run, out-of-batch, invented, and incorrectly channeled citations
  fail; entity tags cannot introduce names absent from quoted evidence.
- Character comments cannot become observation records. A generated interpretation
  remains labelled and cannot be relabelled as a verbatim observation.
- Retrieval is deterministic, relevant on a fixed entity example, and bounded by
  both count and serialized byte size; an oversized record is excluded intact.
- The existing Part 1 encountered-history fixture supplies an earlier Jenny quote
  that remains recalled after leaving the recent window. Neither extractor nor
  decision prompts receive an unvisited secret passage or injected hidden state.
- Close/reopen restores records, progress cursor, selection preview, transcript,
  and engine state. Further extraction processes only new sources.
- Run isolation prevents retrieving or committing another run's memories.
- Request budget includes extraction and can stop the next decision before
  invocation. An invalid memory batch or refusal stops before action, with usage
  and safe category retained. Memory input limits stop before any request.
- Injected memory-write failure rolls back records and cursor together while
  retaining request usage. A successful update survives a failed decision and
  subsequent resume without duplicate extraction.
- Logged context estimates match the supplied decision messages. Terminal and
  full-history modes make no memory requests; interactive inspection makes none.
- Real LangChain/OpenAI SDK serialization through offline HTTP transport sends
  strict `MemoryBatch` Responses output, preserves usage, uses `store=False`,
  omits server-side history, and applies the 4096-token extractor output setting.
- A version 1 schema/configuration fixture migrates while preserving its saved
  full-history state. Memory-model compatibility and CLI options are checked.

No live or paid API calls were made for CP4, and `.env` was neither modified nor
printed. The transport test verifies client integration using mocked HTTP;
server acceptance of this new schema and live extraction quality await CP5.
The quoted-memory fixture uses a deterministic extractor stub, so it proves
source handling and retrieval rather than model recall quality.

The structured-output implementation follows the fetched
[official OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses),
reviewed on 2026-10-10. Schema conformance alone does not establish factual
grounding, hence the additional local citation checks and explicit epistemic labels.

## Limits and next checkpoint

This is a testable character-memory architecture, not evidence of human cognitive
fidelity or improved roleplay. Extraction may omit important events, assign poor
salience, or misclassify a belief; supporting quotes do not prove a free-form
interpretation. Keyword retrieval can miss synonyms and recall outdated intentions.
Selection budgets can omit conflicting records from a prompt while preserving
them in storage. The original narrative and source references remain inspectable.

CP5 should compare full-history and hybrid on fixed encountered histories,
with repeated model trials for recall, contradictions, unsupported beliefs,
Swedish characterization, valid actions, refusal rates, tokens, and latency.
Confirm live memory-schema acceptance before those trials. Keep full-history as
the default until evidence supports a change. CP1 interpreter and CP2 account
access limitations remain unchanged.
