# CP2 validation — modern full-history player

Date: 2026-10-10
Branch: `development/modernize-ai-player`
Result: checkpoint complete.
Git checkpoint: `Complete CP2 Responses and full-history milestone`.

## Delivered design

`player.py` replaces `ConversationChain` and `ConversationEntityMemory` with
explicit LangChain model invocation and locally owned conversation history.
`ai_player.py` provides interactive and bounded-run controls.

- Requests target OpenAI's Responses API via `ChatOpenAI` and strict native JSON
  schema output. Local Pydantic validation checks types and comment length; the
  engine also validates the chosen action against current choices.
- The schema has `choice` and `character_comment`. The Swedish persona asks for a
  brief in-character comment, not an analysis or hidden reasoning transcript.
- Observations and committed decisions are retained verbatim. Rejected decisions,
  failed transitions, hidden variables, and unvisited passages are excluded.
- Terminal observations are retained without requesting another decision.
- Response storage is disabled with `store=False`; server-side response chaining
  is disabled. This does not imply zero provider retention under every account's
  data policy.
- Refusals and incomplete responses stop autorun without advancing or retrying.
  Connection/timeouts, rate limits, server errors, and invalid structured
  decisions have bounded retries. Authentication/access errors are reported
  without exposing provider error bodies or credentials.
- SDK retries are disabled so the player is the only retry owner.
- The full-history input budget is conservatively estimated from UTF-8 bytes of
  serialized messages/schema plus a 1024-token envelope allowance. Exceeding the
  budget stops before any API request, with no truncation or automatic summary.
- CLI options and environment variables control model, reasoning, timeout,
  retries, output limit, context budget, and memory mode. The memory model setting
  is reserved for CP4; no memory-updater request is made in CP2.
- `--max-steps` supports a bounded non-interactive run. With retries enabled, the
  maximum generation attempts are `max_steps * (retries + 1)`.

Defaults: `gpt-6-luna`, reasoning `low`, timeout 30 seconds, two additional
attempts, 2048 output tokens, and 64000 conservative input tokens. The supported
comparison option is `gpt-6.1-sol`, with `low` or higher reasoning. No sampling
override or silent model substitution is used.

## Offline verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

32 tests pass, including CP1 engine/browser reference fixtures and new player
tests for full history, knowledge boundaries, rejected decisions, exhausted
retries, refusal, incomplete responses, context overflow, transactional engine
failure, configuration precedence, model compatibility, and terminal/autorun
behavior.

HTTP transport tests use the real installed LangChain and OpenAI serialization
code with mocked HTTP responses. They verify `/v1/responses`, the explicit model
and reasoning parameters, `max_output_tokens`, strict JSON schema, `store=False`,
no `previous_response_id`, no sampling override, and refusal/incomplete handling.
These tests do not consume live API quota or use the user's key.

`pip check` passes after uninstalling `langchain-classic`,
`langchain-text-splitters`, and SQLAlchemy. The dependency lock was regenerated;
OpenAI and Pydantic are now direct dependencies because the player imports them.

## Bounded live verification

The user-provided `.env` key was loaded normally; its contents were never printed
or changed. Four generation requests succeeded in total. No model substitution
was needed. All responses reported `gpt-6-luna` and `completed`; all decisions
matched the schema, selected available actions, and applied successful transitions.

### Three-decision CLI smoke run

```powershell
.\.venv\Scripts\python.exe ai_player.py --story-file data/del1.html --model gpt-6-luna --reasoning none --max-output-tokens 512 --retries 0 --timeout 30 --max-steps 3 --seed 7 --debug
```

The run started at the title screen, made three valid transitions, and stopped
without prompting. Responses included short Swedish character comments.

| Decision | Input tokens | Output tokens | Reported total | Request latency |
| --- | --- | --- | --- | --- |
| 1 | 338 | 37 | 375 | 2.861 seconds |
| 2 | 742 | 43 | 785 | 1.930 seconds |
| 3 | 978 | 45 | 1023 | 2.484 seconds |

### Default-settings multi-choice check

Four first-choice actions were scripted to reach Part 1's `Porrfilm?` passage,
recording their encountered observations and labeling their decisions as scripted
fixture actions. One live decision then used `low` reasoning and the default
2048-token output limit, with retries disabled for the smoke test.

The model chose an available option and advanced to `Jenny?`. Usage was 1457
input tokens, 94 output tokens (including 53 reasoning tokens), and 1551 total
tokens. Request latency was 2.156 seconds. A credential-free summary is stored
locally in ignored `.cache/cp2-default-smoke.json`.

## Limits and next checkpoint

- These are integration smoke tests, not a roleplay-quality benchmark or proof of
  full-game completion. Long-run recall, refusal rates, and model comparisons
  belong to CP5.
- `gpt-6.1-sol` parameter compatibility is validated offline; account access and
  gameplay quality have not been live-tested.
- Full history currently resides in process memory. SQLite save/resume, durable
  events, complete per-attempt usage accounting, and run budgets belong to CP3.
  Debug output reports the last returned response, not an aggregate billing total.
- The conservative context estimate overcounts typical text. It is not a provider
  token-count endpoint and does not replace handling provider context errors.
- Refusal, malformed-output, rate-limit, and incomplete scenarios were tested
  offline, rather than deliberately inducing paid failures.
- CP1's limited Harlowe scope and authored story quirks still apply.

## Official documentation used

- [OpenAI GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [OpenAI GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [OpenAI migration parameters](https://developers.openai.com/api/docs/guides/latest-model)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [LangChain OpenAI Responses integration](https://docs.langchain.com/oss/python/integrations/chat/openai)

Documentation was rechecked on 2026-10-10; repeat compatibility checks before
changing supported model IDs or settings.
