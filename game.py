import pathlib
import re
from dataclasses import dataclass, is_dataclass

from bs4 import BeautifulSoup
import requests


@dataclass
class Link:
    text: str
    destination: str

    def __str__(self):
        return f"{self.text}"


@dataclass
class Passage:
    pid: int
    name: str
    text: str
    links: list[Link]


def load_game_data(game_url="https://fordarvet.neocities.org/860228"):
    game_file="data/twine_game.html"
    if not pathlib.Path(game_file).is_file():
        dowload_game_data(game_url, game_file)

    soup = BeautifulSoup(open(game_file, "r", -1, "utf-8", "ignore"), "lxml")
    passages = soup.find_all("tw-passagedata")
    story_index = {p['name']: Passage(p['pid'], p['name'], p.text, []) for p in passages}
    return story_index

def dowload_game_data(url, outputfile):
    print("downloading gamedata...")
    pathlib.Path(outputfile).parent.mkdir(exist_ok=True)
    response = requests.get(url)
    with open(outputfile, 'wb') as f:
        f.write(response.content)
    print(f"wrote gamedata to file {outputfile}")


class Game:
    re_links = re.compile(r"\[\[([^\[].*?[^\]])\]\]")

    def __init__(self, game_data: dict, start_passage=None):
        self._current = start_passage if start_passage else list(game_data.keys())[0]
        self._data = game_data
        self.vars = {}
        self.process()

    def process(self):
        text = self.process_twine(self.passage.text)
        # Remove citatation formatting
        self.rendered_text = text.replace('//', "")


    @property
    def passage(self):
        return self._data[self._current]

    @property
    def links(self):
        links = []
        for link in self.re_links.findall(self.rendered_text):
            name, text = link.split('|')
            links.append(Link(name, text))

        return links

    def follow(self, link: Link):
        self._current = link.destination
        self.process()

    def __str__(self):
        return self.re_links.sub('', self.rendered_text).rstrip()

    def __repr__(self):
        return str(self)

    def process_twine(self, twine_text, debug=False):
        class State:
            IF = "IF"
            ELSE = "ELSE"

        re_code = re.compile(r'(\([a-zA-Z0-9]+:.*?\))')
        re_if_variable = r'\(if:\s*?\$(\w+)\s*?is\s*?(\w+)\)'
        re_set_variable = r'\(set:\s*?\$(\w+)\s*?=\s*?(\w+)\)'
        re_single_end_square_bracket = re.compile(r'(?<![]])](?![]])')
        re_color = re.compile(r'\(text-colour: .+?\)\[(.*?)\]')
        re_command_link = re.compile(r'\(link: .+?\)\[(.*?)\]')
        re_single_newline = re.compile(r'(?<!\n)\n(?!\n)')
        twine_text = re_color.sub(r'\1', twine_text)
        twine_text = re_command_link.sub(r'\1', twine_text)
        twine_text = re_single_newline.sub(r' ', twine_text)
        twine_text = re.sub(r'[ \t]+', r' ', twine_text)
        twine_text = re.sub(r'\n\n\s+', r'\n\n', twine_text)
        twine_text = re.sub(r'\(save-game:.+?\)', '', twine_text)

        chunks = re_code.split(twine_text)

        vars = self.vars
        states = []
        output = True
        result = []
        for chunk in chunks:
            for chunk in re_single_end_square_bracket.split(chunk):
                if not chunk:
                    continue
                elif chunk.startswith('(if:'):
                    states.append(State.IF)
                    m = re.match(re_if_variable, chunk)
                    var_name = m.group(1)
                    var_must_be = m.group(2)
                    if debug: print(f"{var_name} should be {var_must_be}")
                    output = vars.get(var_name, '').lower() == var_must_be.lower()

                elif chunk.startswith('(else:'):
                    output = not output
                    states.append(State.ELSE)
                elif chunk.startswith('(set:'):
                    if output:
                        m = re.match(re_set_variable, chunk)
                        vars[m.group(1)] = m.group(2)
                        if debug: print(vars)
                elif chunk.startswith('[') and not chunk.startswith('[['):
                    if output:
                        result.append(chunk[1:])
                        if debug: print(chunk[1:])
                    states.pop()

                else:
                    if debug:
                        print(states)
                        print(output)
                        print(chunk)
                    result.append(chunk)
        return ''.join(result)


class GameEngine:
    def __init__(self, game: Game):
        self.game = game
        self.running = True

    def next(self, choice: int = None):
        game = self.game
        if choice:
            game.follow(game.links[choice - 1])
        output = [str(game), ]
        if not game.links:
            print("SLUTET")
            self.running = False
        else:
            for i, link in enumerate(game.links):
                output.append(f"[{i + 1}. {link}]")

        return '\n'.join(output)

    def prompt(self):
        while True:
            action = input(">")
            if action.lower() in ('q', 'v'):
                return action.lower()
            if not self.game.links:
                return
            try:
                choice = int(action)
                if choice < 1 or choice > len(self.game.links):
                    raise ValueError("Not a valid action!")
            except ValueError as e:
                print(f"Invalid action: {e}")
                continue
            return choice


if __name__ == '__main__':
    game = Game(load_game_data())
    ge = GameEngine(game)
    choice = None
    while ge.running:
        if choice == 'v':
            print(ge.game._current)
            print(ge.game.vars)
        elif choice == 'q':
            ge.running = False
            break
        else:
            print("---------")
            text = ge.next(choice)
            print(text)
        choice = ge.prompt()
