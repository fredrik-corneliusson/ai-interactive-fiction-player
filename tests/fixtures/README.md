# CP1 fixtures

`browser_passages.json` contains representative excerpts from the downloaded
games and expected visible text, links, and state changes measured in their
bundled Harlowe runtime using headless Edge. It covers Part 1 nested endings and
conditional choices, the bare-link conditional in `vadnu`, all four Part 2 poker
outcomes, and health clamping. Browser save/load/restart controls are excluded
because the console player does not implement browser storage.

Regenerate these reference results with `scripts/check_browser.py` only after
reviewing story or engine changes. The script raises if the browser and console
disagree. Runtime details remain in ignored `.cache/browser-check/results.json`.

`encountered_history.json` contains fixed, twelve-observation trajectories through
each story using the first visible action and seed 7. These are engine traces for
later model/memory evaluation, not human playthroughs or quality benchmarks. They
contain encountered text and choices only; no hidden engine variables or unvisited
passages. Each observation is associated with the action leading to the next
observation. The final observation has no selected action because the trace stops
there, even if the game has more choices.

Fixtures are checked into the repository so the offline regression suite does not
need a network connection, the full downloaded games, or a browser installation.
