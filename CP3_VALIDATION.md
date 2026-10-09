# CP3: persistence and observability validation

Completed: 2026-10-10. Branch: `development/modernize-ai-player`.
Git checkpoint: `Complete CP3 persistence and observability milestone`.

## Implementation and design decisions

- `run_store.py` uses standard-library SQLite, schema version 1, WAL journaling,
  foreign keys, and database triggers that reject event updates and deletions.
  Runs have UUID identities and independent checkpoints, configuration, and usage.
- Initial creation and each successful action save the game position, variables,
  RNG state, rendered text, interpreter notices, transcript, and terminal status.
  An action's decision and encountered observation enter the event log in the
  same transaction as its checkpoint. Failed writes restore in-memory state too.
- Each run embeds its parsed story and SHA-256 identity. Resume does not depend
  on the source HTML still existing or being unchanged, and checks the embedded
  story checksum. This makes an existing run reproducible after a source update.
- Resume restores the rendered passage directly, without re-executing assignments
  or consuming random values. Saved persona and model settings are restored;
  model credentials are read afresh, never serialized. Environment defaults
  cannot silently change an existing run's settings or budgets.
- A revision check rejects stale sessions before a request or checkpoint write.
  Request reservations and budget checks share a transaction. This prevents
  competing resumes from committing duplicate actions or bypassing run budgets.
- Requests have separate append-only start and finish events, with timestamps,
  model identity, attempt number, response status, usage when known, latency, and
  safe error category. Provider bodies, response IDs, and credentials are omitted.
  Rejected decisions and failed requests count, even when no action commits.
- New CLI commands: `--resume`, `--list-runs`, `--run-info`, `--database`, and
  `--acknowledge-pending`. Interactive `s` saves explicitly and `u` shows usage.
  Default storage is ignored `data/runs.sqlite3`; alternate paths are supported.

## Budget policy

CP3 chooses request/token budgeting rather than estimated monetary cost. This
avoids coupling persistence to changing model prices; there are no pricing
assumptions or currency estimates. Defaults are 100 total attempts and 1,000,000
tokens per run, configured by `--request-budget` / `AI_REQUEST_BUDGET` and
`--token-budget` / `AI_TOKEN_BUDGET` at creation.

Before each attempt, reserve CP2's conservative UTF-8 input estimate plus the
configured maximum output tokens, including reasoning. Replace the reservation
with actual total usage when reported. Unknown usage keeps the reservation,
including connection failures, missing metadata, and interrupted requests.
Budgets cover retries and every resumed session. `--max-steps` additionally bounds
actions in the current session; its value does not reset persisted budgets.

Reports distinguish `known_tokens`, charged `budget_tokens`, and
`unknown_usage_requests`. Measured `seconds` exclude retry backoff;
`unknown_latency_requests` flags missing durations rather than inventing them.
The byte estimate is a local conservative heuristic, not an exact billing cap.
Unknown-usage reservations can cause an early stop.

A hard process interruption can leave a request's result unknown. A resumed
player blocks new requests until explicit `--acknowledge-pending` recovery, after
inspection and after the original process has stopped. Recovery retains the
request and its reservation, marks latency/usage unknown, and never replays an
action. Only committed actions are restored; an uncommitted generation may be
replaced by a later request. Provider calls and SQLite cannot form one distributed
transaction, so exactly-once remote generation is not claimed.

## Verification

Run from the repository directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe ai_player.py --help
```

Results: **48 tests pass** (32 existing CP1/CP2 tests plus 16 CP3 tests),
`pip check` reports no broken requirements, and the CLI help exposes the new
commands. Dependencies and lock file are unchanged.

The CP3 tests verify:

- Database close/reopen restores engine, variables, RNG, transcript, persona,
  settings, and next random outcome. Terminal resume does not repeat its last
  assignment or call a model.
- Separate runs have independent history and usage. Two SQLite connections
  cannot let a stale session send a request or overwrite a newer checkpoint.
- Injecting a database trigger failure during action commit rolls back both the
  action event and checkpoint, and restores in-memory state. The completed model
  attempt remains logged. Unsupported destination syntax behaves similarly.
- Real `ChatOpenAI` / OpenAI SDK Responses calls through an offline HTTP transport
  record an invalid-choice retry plus a valid result: two attempts, 60 reported
  tokens total, both categories and latencies, and one committed action.
- Connection retries retain unknown usage; request budgets include retries and
  survive reopen. Token limits stop before invoking the model.
- A simulated interrupted request blocks resumed generation until acknowledged;
  acknowledgment retains its 12,345-token reservation and unknown latency.
- Refusal, incomplete output, decode error, and HTTP authentication failure have
  explicit safe categories and do not advance the game. Injected provider secrets
  do not appear in reports or user-visible error messages.
- Resumed prompts contain encountered observations, excluding unvisited passage
  text and hidden variables even though the complete story is stored locally.
- SQL update/delete of events fails; unknown run IDs and changed story checksums
  fail explicitly. CLI creation, terminal resume without a key, listing,
  inspection, and configuration-override rejection work without network access.

No live or paid API requests were made for CP3. The existing API key was not
printed or modified. CP2's live Luna evidence remains the account-access baseline.
The interruption tests simulate durable boundary states; they do not claim a
physical power-loss or simultaneous-process stress test.

## Remaining limits and next checkpoint

SQLite and transcripts are ordinary local files, without encryption. Append-only
triggers protect normal application operations, not deliberate database tampering.
Back up the database with the player closed. Custom database paths inside the
repository require their own ignore rule. Persistence currently belongs to the
AI console, not the manual console or original browser game's storage.

CP1's limited Twine interpreter scope and CP2's untested Sol account access are
unchanged. CP4 adds source-linked hybrid memory and retrieval using encountered
events; it must keep the original transcript and hidden/unvisited-data boundaries.
