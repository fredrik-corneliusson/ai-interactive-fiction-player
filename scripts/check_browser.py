"""Compare representative real passages against their bundled Harlowe runtime.

Install optional tooling with:
  python -m pip install --target .cache/browser-tools playwright
Run from the repository root. Uses installed Edge; does not download a browser.
"""

import html
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(".cache/browser-tools").resolve()))
from playwright.sync_api import sync_playwright

from game import Game, load_game_data


CASES = [
    ("del1", "mannen", {"juha2": True}),
    ("del1", "mannen", {"juha2": False}),
    ("del1", "valet1", {"neger": True}),
    ("del1", "valet1", {"neger": False}),
    ("del1", "vadnu", {"neger": True}),
    ("del2", "grek-nicke-satsa", {"poker": 1, "cash": 150, "health": 100}),
    ("del2", "grek-nicke-satsa", {"poker": 2, "cash": 150, "health": 100}),
    ("del2", "grek-nicke-satsa", {"poker": 3, "cash": 150, "health": 100}),
    ("del2", "grek-nicke-satsa", {"poker": 4, "cash": 150, "health": 100}),
    ("del2", "Kapitel 1", {"health": 95}),
    ("del2", "Kapitel 1", {"health": 70}),
]


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def main():
    output = Path(".cache/browser-check")
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        # The downloaded game can reference external fonts/assets; verification
        # uses only its bundled runtime and has no network dependency.
        for number, (part, name, variables) in enumerate(CASES):
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(3000)
            page.route("http://**/*", lambda route: route.abort())
            page.route("https://**/*", lambda route: route.abort())
            source = Path(f"data/{part}.html").read_text(encoding="utf-8")
            story = load_game_data(game_file=f"data/{part}.html")
            passage = story[name]
            prefix = "".join(f"(set: ${key} to {str(value).lower()})" for key, value in variables.items())
            marker = '<span id="cp1-state">' + ";".join(f"{key}=(print: ${key})" for key in variables) + "</span>"
            source = re.sub(r'(\bstartnode=")[^"]+', lambda m: m[1] + str(passage.pid), source, count=1)
            pattern = rf'(<tw-passagedata\b[^>]*\bpid="{passage.pid}"[^>]*>).*?(</tw-passagedata>)'
            source, count = re.subn(pattern, lambda m: m[1] + html.escape(prefix + passage.text + marker) + m[2], source, count=1, flags=re.DOTALL)
            assert count == 1
            target = output / f"case-{number}.html"
            target.write_text(source, encoding="utf-8")
            page.goto(target.resolve().as_uri())
            page.wait_for_selector("tw-passage", timeout=10000)
            page.wait_for_timeout(500)
            browser_errors = page.locator("tw-error").all_text_contents()
            (output / f"dom-{number}.html").write_text(page.content(), encoding="utf-8")
            # Remove browser-only controls from the text comparison.
            page.locator("tw-passage tw-link").evaluate_all("nodes => nodes.filter(n => !window.jQuery(n).closest('tw-expression').data('linkPassageName')).forEach(n => n.remove())")
            browser_links = page.locator("tw-passage tw-link").evaluate_all("nodes => nodes.map(n => ({text:n.textContent, destination:window.jQuery(n).closest('tw-expression').data('linkPassageName')}))")
            state_text = page.locator("#cp1-state").inner_text() if page.locator("#cp1-state").count() else "MISSING"
            if page.locator("#cp1-state").count():
                page.locator("#cp1-state").evaluate("node => node.remove()")
            text = page.locator("tw-passage").inner_text()
            game = Game(story, name, variables=variables)
            # Both outputs include choice labels in their text, but console
            # passage links use explicit delimiters until Game.__str__ strips them.
            console_text = game.re_links.sub(lambda m: m[1].split("|", 1)[0], game.rendered_text)
            console_links = [{"text": link.text, "destination": link.destination} for link in game.links]
            state = ";".join(f"{key}={str(game.vars.get(key, 0)).lower()}" for key in variables)
            result = {"part": part, "passage": name, "variables": variables,
                      "browser_errors": browser_errors, "links_match": browser_links == console_links,
                      "text_match": normalize(text) == normalize(console_text), "state_match": state_text == state,
                      "browser_links": browser_links, "console_links": console_links}
            result["passage_text"] = passage.text
            result["browser_text"] = text
            result["browser_state"] = state_text
            result["console_state"] = state
            if not result["text_match"]:
                result["browser_text"] = text
                result["console_text"] = console_text
            results.append(result)
            (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(part, name, {key: result[key] for key in ("browser_errors", "links_match", "text_match", "state_match")})
            context.close()
        browser.close()
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(not row["browser_errors"] and all(row[key] for key in ("links_match", "text_match", "state_match")) for row in results):
        raise AssertionError("Browser comparison failed; inspect .cache/browser-check/results.json")
    fixtures = []
    for row in results:
        expected_variables = {}
        for pair in row["browser_state"].split(";"):
            key, value = pair.split("=", 1)
            expected_variables[key] = (value == "true") if value in ("true", "false") else int(value)
        fixtures.append({"part": row["part"], "passage": row["passage"],
                         "text": row["passage_text"], "variables": row["variables"],
                         "expected_text": normalize(row["browser_text"]),
                         "expected_links": row["browser_links"], "expected_variables": expected_variables})
    destination = Path("tests/fixtures/browser_passages.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    main()
