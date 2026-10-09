# CP1 validation — engine and fixtures

Date: 2026-10-10
Result: checkpoint complete for the documented console interpreter scope.

## Delivered behavior

- Story loading honors `tw-storydata.startnode` and validates story metadata.
- Both CLIs accept `--story-file` and `--seed`; selecting an explicit missing file
  fails rather than downloading or replacing another story.
- Nested conditional hooks have local scope. Inactive branches do not emit text,
  choices, assignments, or random draws.
- Boolean/numeric assignments, arithmetic increments/decrements, equality and
  comparisons (including the stories' `is >` form), `elseif`, and seeded inclusive
  integer random outcomes are supported.
- Numeric variables in the Part 2 status display are rendered as visible text.
- Invalid choices and unavailable links are rejected. Failed transitions restore
  passage, variables, rendered text, notices, and random state.
- Inspecting the current turn does not reapply assignments or reroll randomness.
- Terminal passages stop manual prompts and AI decision requests. Autorun stops
  at endings. Invalid legacy AI responses do not advance the game.
- Browser storage/restart/navigation controls are omitted and recorded as notices
  visible with `v`; unknown active macros or expressions fail explicitly.

## Verification evidence

### Offline suite

Command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

18 tests pass, including nested branches, exclusive `elseif` effects, numeric
clamping, state rollback, valid/invalid actions, terminal handling, seeded
randomness, metadata loading, browser-derived reference fixtures, AI loop mocks,
and encountered-history fixture structure. No paid API requests are involved.

### Original browser runtime comparisons

Command:

```powershell
.\.venv\Scripts\python.exe scripts/check_browser.py
```

11 representative passage/state cases were run in headless Microsoft Edge using
the original Harlowe 3.3.9 runtime bundled in each downloaded HTML file. Each case
used an isolated browser context, injected starting variables, and disabled
external network assets. All cases matched normalized visible narrative and link
labels, passage-link destinations, and monitored variable values.

| Story | Cases | Verified behavior |
| --- | --- | --- |
| Part 1: `mannen` | 2 | Nested ending vs continuing branch; browser controls excluded |
| Part 1: `valet1` | 2 | Conditional choices and ordinary text after conditional hooks |
| Part 1: `vadnu` | 1 | Authored bare-link conditional behavior |
| Part 2: `grek-nicke-satsa` | 4 | All poker branches; cash award only on winning outcome |
| Part 2: `Kapitel 1` | 2 | Health increment with and without maximum clamp |

Reference excerpts and browser expectations are committed in
`tests/fixtures/browser_passages.json`; detailed runtime output is in ignored
`.cache/browser-check/results.json`. Browser save/load/restart links and visual
styling are excluded from the plain-text comparison.

Optional tooling was installed into ignored `.cache/browser-tools`, keeping the
application dependency lock unchanged. To reproduce the browser check, install
Playwright there and have Microsoft Edge installed:

```powershell
.\.venv\Scripts\python.exe -m pip install --target .cache/browser-tools playwright
```

### Whole-story syntax/state audit

Command:

```powershell
.\.venv\Scripts\python.exe scripts/audit_stories.py
```

| Story | Passages | State profiles | Rendering checks |
| --- | --- | --- | --- |
| Part 1 | 343 | 9 | 3,087 |
| Part 2 | 415 | 9 | 3,735 |
| Total | 758 | — | 6,822 |

Every check rendered without an unsupported-syntax error and every visible link
destination existed. Profiles cover unset variables, flags true/false, four
random-result values, selected cash boundaries, and health above the maximum.
This is an isolated-passage audit, not exhaustive branch or playthrough coverage.

### Evaluation inputs and CLI checks

`tests/fixtures/encountered_history.json` stores twelve encountered observations
per story, choosing the first visible action with seed 7. It contains no hidden
engine variables or unvisited passage text. These traces are inputs for later
model/memory comparisons, not evidence of AI quality.

Both manual story selections start and quit successfully. Both CLIs expose the
new options through `--help`; no live AI startup is exercised in CP1.

## Supported scope and remaining limitations

This is a limited console interpreter for the audited syntax in these two games,
not a full Harlowe implementation. It flattens styling/HTML into text and does not
implement browser saves, reloads, external score-page navigation, arbitrary
interactive hooks, JavaScript, or general Harlowe expressions.

Part 1 passage `vadnu` uses `(if:)[[link]]` rather than a hook containing a link.
The bundled browser runtime displays both alternatives. The console preserves
that observed behavior and records a notice; it does not silently repair authored
story logic.

The AI integration still uses the classic chain, entity memory, and its old model
default. CP2 replaces those with the modern Responses/full-history baseline.
Invalid legacy responses can still enter that chain's memory before rejection;
the engine itself remains unchanged. Native structured output, provider error
handling, and memory consistency belong to CP2.

Neither full-game completion nor every combination of story variables has been
verified. Save/resume and durable event logging belong to CP3.
