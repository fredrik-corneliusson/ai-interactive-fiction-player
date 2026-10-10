# CP5: model and memory comparison

Validated 2026-10-10 on Windows, Python 3.13.2. This checkpoint adds a repeatable
evaluation harness; its live results and default recommendation are recorded
below. It does not change the player persona or relax memory provenance checks.

## Protocol and reproduction

Run these commands in Git Bash from the repository root:

```bash
./.venv/Scripts/python.exe scripts/evaluate.py --dry-run
./.venv/Scripts/python.exe scripts/evaluate.py --output-dir data/evaluations/my-cp5
./.venv/Scripts/python.exe scripts/evaluate.py --report-only --output-dir data/evaluations/my-cp5
./.venv/Scripts/python.exe -m unittest discover -s tests -q
./.venv/Scripts/python.exe -m pip check
```

The live command requires `.env` credentials and the downloaded `data/del1.html`
and `data/del2.html`. Every campaign uses a new directory; report-only reads
existing results without credentials or further requests. Local full prompts,
results, SQLite event logs, and summaries live under ignored `data/evaluations/`.

Three fixed histories come from `tests/fixtures/evaluation_cases.json`: the CP1
12-observation histories for each story and an eight-observation synthetic
history with conflicting Jenny accounts, a fabricated gift in the character's
own comment, and an unknown address. Each has three recall probes. Each
story/model/mode combination gets two independent repetitions. The observations,
scripted decisions, authored comments, Swedish persona, and seed 7 stay fixed;
the generated evaluation choice is validated but does not change the history.
Fixture runs are not resumable gameplay runs.

Candidates are `gpt-6-luna` and `gpt-6.1-sol`, each in full-history and hybrid
mode. Hybrid always uses Luna for extraction: this compares decision models
with a common extractor, not two extractor candidates. Both roles use low
reasoning, Responses, strict schemas, `store=False`, zero retries, and a
45-second timeout. Fixture answers allow 4,096 output tokens; actual gameplay
decisions allow 2,048. Other settings, schemas, and prompt text are in the
campaign manifest and per-trial settings.

Extraction sees only encountered sources, never recall questions or answer keys.
Retrieval uses the current observation and production keyword/entity ranking;
probes do not select memories. Full-history sees the complete transcript;
hybrid sees recent/pending history plus bounded retrieved memories. Probe
requests annotate source IDs and add an `answers` schema, so they are controlled
recall tests rather than ordinary spontaneous gameplay.

Separately, eight bounded gameplay runs exercise four actual AI decisions per
story/model/mode, using the real engine. Their generated paths can diverge and
are not paired recall comparisons. The Part 2 fixture includes murder accounts,
conflicting suspicions, substance references, and threatening encounters.
These scenes test representative challenging material, not every passage.

Campaign allowances are 96 requests, 1,000,000 accounted tokens, and $2 estimated
cost, shared across all runs including extraction and failures. Checks occur
before sending each attempt inside the SQLite request transaction. Unknown
usage retains its input/output reservation; no failed call is treated as free.
Per-run CP3 budgets and interruption accounting remain in force.

## Scoring and interpretation

Positive probes require expected answer terms, exact source quotations that were
visible in the supplied context, the correct channel, and all required source
turns. Negative probes require `unknown` with no evidence. Failures remain in
the planned-probe denominator; report completed-trial recall separately from
end-to-end availability. `unsupported_known` means a strict support check failed,
not a proven hallucination. Review the actual text before interpreting it.

Memory acceptance is separate from recall: one unsupported record rejects the
whole extraction batch and stops that trial. Invalid records are retained for
inspection, while rejected batches are never written into the memory store.
This measures the current production behavior rather than silently repairing
model output, retrying until it passes, or falling back to full-history.

Character voice is assessed qualitatively by the implementing assistant from
the saved comments. This is neither independent human review nor a blinded
evaluation, and it cannot establish how well the system represents human memory.

## Live results

The main campaign finished all 32 scheduled trials without exhausting its
allowances: **64 paid requests, 112,934 reported tokens, approximately $0.12327**.
All attempts reported usage. There were 15 valid evaluation responses, 29 valid
gameplay decisions, eight accepted memory batches, and 12 rejected memory
batches. No refusal, model-access error, timeout, or invalid choice was observed.
A completed fixture means the recall response passed structural/choice checks;
its factual recall is scored separately.

The reviewable archive is [reports/cp5-20261010.json](reports/cp5-20261010.json).
It preserves the manifest, exact fixture prompts, answers, parsed fixture
extraction outputs, accepted memories, source mappings, settings, safe attempt
telemetry, and summary. It also records SHA-256 hashes of the implementation,
dependency lock, fixtures, and downloaded stories. Rejected gameplay extraction
outputs were not captured; their failure categories and paid usage are retained.
Offline fixture diagnoses identify individual records that violate validation.

### Fixed-history comparison

Each row covers six trials: three cases repeated twice, with 18 planned probes.
Latency is the mean per API attempt, including extraction in hybrid rows; it is
not total time per turn. Costs include all extraction attempts and failed trials.

| Decision model | Context | Completed trials | Strict recall / planned | Requests | Tokens | Mean attempt seconds | Estimated USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Luna | Full-history | 6/6 | 16/18 | 6 | 19,993 | 3.784 | 0.003146 |
| Luna | Hybrid | 1/6 | 3/18 | 9 | 18,213 | 7.925 | 0.004922 |
| Sol | Full-history | 6/6 | 18/18 | 6 | 19,585 | 4.836 | 0.058831 |
| Sol | Hybrid | 2/6 | 6/18 | 10 | 19,255 | 6.943 | 0.017558 |

Completed hybrid trials passed 3/3 and 6/6 probes respectively, but all were the
synthetic case. **Every real-story hybrid fixture failed extraction before the
recall request.** Lower aggregate tokens for failed hybrid trials are therefore
not evidence of efficiency. The three successful paired synthetic trials used
4,450–5,140 tokens versus 1,536–1,649 for their full-history counterparts, and
three requests versus one. These short histories do not show a hybrid advantage.

Luna's two strict recall failures deserve separate interpretations:

- Part 1 repetition 2 answered the gift amount correctly as **“En tia.”**, with
  the exact `10-lapp` quotation. The scorer accepts `10` or `tio` and missed this
  colloquial equivalent. The recorded 16/18 remains unchanged; manual correction
  for this single lexical false negative gives 17/18. No answer keys were
  changed after observing the main results.
- Part 2 repetition 1 correctly described Britt's and Krukan's suspicions, but
  labelled the story quotations `character_comment` instead of `observation`.
  That is a real provenance failure. The one `unsupported_known` flag reflects
  this wrong channel, not an invented murderer or incorrect recollection.

All completed synthetic responses preserved the conflicting Jenny accounts,
treated the fabricated gift as unconfirmed, and left the new address unknown.
Both full-history candidates correctly left all eight negative probes unknown.
This small sample found no conversion of the invented gift into a world fact.

### Actual gameplay

Each row covers one four-step attempt per story. A failed hybrid run applied
three valid decisions and stopped at its first extraction, before decision four.

| Decision model | Context | Completed four-step runs | Applied decisions | Requests | Tokens | Estimated USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Luna | Full-history | 2/2 | 8 | 8 | 6,969 | 0.000919 |
| Luna | Hybrid | 0/2 | 6 | 8 | 9,965 | 0.002190 |
| Sol | Full-history | 2/2 | 8 | 8 | 6,908 | 0.017627 |
| Sol | Hybrid | 1/2 | 7 | 9 | 12,046 | 0.018075 |

Across the campaign, mean attempt latency was 2.329 seconds for decisions,
4.346 seconds for recall evaluation, and 8.243 seconds for extraction. These
single-machine measurements include network/provider variability and are not
service latency guarantees. Gameplay reached `Porrfilm?` in Part 1 and
`lasse-skuld` in Part 2 on completed paths; it did not finish either story.

### Memory failure diagnosis and character review

The nine failed hybrid fixtures contain paraphrased text labelled as a verbatim
observation, entity tags absent from the record's own quotes, or quotes with
altered wording/source association. Even a sensible summary or a correctly
resolved pronoun can violate the deliberately strict contract. Rejection proves
the guards work; it does not prove the extractor invented the underlying event.
Changing the decision model does not remedy a common Luna extractor's failures.
Eight batches passed strict validation and saved 45 records across the main run,
so the live memory schema is supported, but production extraction is unreliable.

All saved decision comments were short Swedish prose. Both candidates used the
persona's weary, colloquial voice, concern with money/drink, and distrust of the
police. For example, Sol wrote “Plånboken har väl fått ett bättre liv än jag.”;
Luna's synthetic response questioned the portkeeper rather than asserting a
new address. Some comments added unconfirmed color, such as Jenny previously
offering alcohol. These remain character comments rather than engine facts or
verified observations. This assistant review found no clear voice-quality winner
and offers no numerical claim about believable human behavior.

An exploratory four-request Luna smoke preceded the main campaign and confirmed
strict memory-schema acceptance. It used 6,794 tokens, approximately $0.001557
at the corrected cache-aware rates. One negative question was clarified before
the main trials because a correct explanation had been marked `known` rather
than `unknown`. The smoke is excluded from the comparison above; combined paid
usage was 68 requests, 119,728 tokens, approximately $0.12482.

## Cost assumptions and verification

Rates were checked on 2026-10-10 against the official
[Luna specifications](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [Sol specifications](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
Per million tokens, the assumed input/cache-read/cache-write/output rates are
$0.10/$0.01/$0.125/$0.50 for Luna and $2/$0.10/$2.50/$10 for Sol. Reported output
includes reasoning tokens. Standard text rates below 272K inputs are assumed;
regional premiums, tools, and tax are excluded. These are local estimates, not
an invoice. Missing usage reserves input at the cache-write rate plus the output
allowance. The dated rates live in the manifest so later price changes do not
rewrite this report's assumptions.

All **84 offline tests passed in Git Bash**; `pip check` found no broken
requirements. Coverage includes fixture-path independence, hidden answer keys,
exact visible citations, attribution/contradictions, extraction cost accounting,
cross-run campaign allowances, cache-rate calculations, failed-trial denominators,
and credential-free dry-run/report-only execution. The real main report-only
command rebuilt an identical summary with no requests. Archive aggregation and
fixture hashes were checked against the saved results.

## Default decision and follow-up

Retain **Luna with full-history** as the default. It completed every fixture and
four-step gameplay run and is inexpensive; Sol's full-history fixture cost was
about 18.7 times Luna's for one additional genuinely supported probe after the
lexical correction. Sol remains an accessible opt-in choice when stronger source
fidelity warrants the expense. This sample does not establish broad model
superiority or justify changing the default for character voice.

Keep hybrid opt-in and experimental. Before recommending it for normal play,
give extraction explicit valid examples, handle entity tags around pronouns,
and re-evaluate paraphrase/quote fidelity without weakening provenance guards.
Then add much longer paired histories to measure retrieval loss and total cost.
No embedding index or simulated forgetting is justified by these results.
Explicit memory remains a useful design tool for controllable character beliefs
and selective recall; this evaluation does not validate it as human cognition.

Limitations: only three histories, two repetitions, one persona, one extractor,
and four gameplay steps per story were tested. Short histories favor a complete
transcript. There is no statistical significance claim, independent human rating,
exhaustive content/refusal audit, or long-session evaluation. Model aliases and
provider sampling can change future outputs; fixed fixtures make comparisons
repeatable, not identical. Next checkpoint: CP6 documentation and handoff, with
hybrid reliability retained as an explicit follow-up.
