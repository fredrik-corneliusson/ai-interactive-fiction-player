"""Audit local story syntax and render representative state profiles offline."""

import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from game import Game, load_game_data
from twine import Renderer


def main():
    results = []
    for part in ('del1', 'del2'):
        story = load_game_data(game_file=f'data/{part}.html')
        text = '\n'.join(p.text for p in story.values())
        macros = sorted(set(re.findall(r'\(([\w-]+):', text)))
        supported = {'set', 'if', 'else', 'elseif', 'else-if', 'random', 'link'} | Renderer.styling | Renderer.browser_commands
        assert not set(macros) - supported, f'Unknown macros: {set(macros) - supported}'
        flags = set(re.findall(r'\$(\w+)\s*(?:=\s*(?:true|false)|is\s+true)', text))
        profiles = [{}]
        for flag in (False, True):
            for roll, cash in ((1, -1), (2, 0), (3, 51), (4, 6001)):
                state = dict.fromkeys(flags, flag)
                state.update(cash=cash, health=110, goodness=20, poker=roll, meetbrothers=roll, meetbrothers2=min(roll, 3), fajt=min(roll, 3))
                profiles.append(state)
        for variables in profiles:
            for passage in story.values():
                game = Game(story, passage.name, variables=variables, seed=7)
                assert all(link.destination in story for link in game.links), passage.name
        results.append({'part': part, 'passages': len(story), 'profiles': len(profiles), 'render_checks': len(story) * len(profiles), 'macros': macros})
        print(json.dumps(results[-1]))
    return results


if __name__ == '__main__':
    main()
