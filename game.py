import pathlib
import random
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
import click
import requests

from twine import Renderer, UnsupportedTwineError


@dataclass(frozen=True)
class Link:
    text: str
    destination: str

    def __str__(self):
        return self.text


@dataclass
class Passage:
    pid: int
    name: str
    text: str
    links: list[Link]


class StoryData(dict):
    """Passages plus the start passage declared by tw-storydata."""

    def __init__(self, passages, start_passage):
        super().__init__(passages)
        self.start_passage = start_passage


def load_game_data(game_url="https://fordarvet.netlify.app/del1", game_file=None):
    explicit_file = game_file is not None
    path = pathlib.Path(game_file or "data/twine_game.html")
    if not path.is_file():
        if explicit_file:
            raise FileNotFoundError(f"Story file does not exist: {path}")
        dowload_game_data(game_url, path)
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    story = soup.find("tw-storydata")
    if story is None:
        raise ValueError(f"No Twine story found in {path}")
    passages = story.find_all("tw-passagedata")
    if not passages:
        raise ValueError(f"No Twine passages found in {path}")
    index = {p["name"]: Passage(int(p["pid"]), p["name"], p.get_text(), []) for p in passages}
    if len(index) != len(passages):
        raise ValueError("Duplicate passage names")
    start = next((p["name"] for p in passages if p["pid"] == story.get("startnode")), None)
    if start is None:
        raise ValueError("Twine startnode does not identify a passage")
    return StoryData(index, start)


def dowload_game_data(url, outputfile):
    print("downloading gamedata...")
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    if not BeautifulSoup(response.content, "lxml").find("tw-storydata"):
        raise ValueError("Downloaded page does not contain a Twine story")
    path = pathlib.Path(outputfile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    print(f"wrote gamedata to file {outputfile}")


class Game:
    re_links = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)

    def __init__(self, game_data: dict, start_passage=None, *, variables=None, seed=None):
        if not game_data:
            raise ValueError("Story has no passages")
        self._data = game_data
        self._current = start_passage or getattr(game_data, "start_passage", next(iter(game_data)))
        if self._current not in game_data:
            raise ValueError(f"Unknown start passage: {self._current}")
        self.vars = dict(variables or {})
        self.rng = random.Random(seed)
        self.notices = []
        self.process()

    def process(self):
        renderer = Renderer(self.vars, self.rng)
        try:
            text = renderer.render(self.passage.text)
        except UnsupportedTwineError as error:
            raise UnsupportedTwineError(f"Passage {self._current!r}: {error}") from error
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        # Story markup is rendered as plain text; passage links stay intact.
        text = BeautifulSoup(text, "html.parser").get_text()
        text = text.replace("//", "").replace("**", "")
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        self.rendered_text = re.sub(r"[ \t]+", " ", text).strip()
        self.notices = renderer.notices

    @property
    def passage(self):
        return self._data[self._current]

    @property
    def links(self):
        links = []
        for value in self.re_links.findall(self.rendered_text):
            if "|" in value:
                label, destination = value.split("|", 1)
            elif "->" in value:
                label, destination = value.split("->", 1)
            elif "<-" in value:
                destination, label = value.split("<-", 1)
            else:
                label = destination = value
            links.append(Link(label.strip(), destination.strip()))
        return links

    def follow(self, link: Link):
        if link not in self.links:
            raise ValueError("Link is not available in the current passage")
        if link.destination not in self._data:
            raise ValueError(f"Unknown destination: {link.destination}")
        previous = (self._current, dict(self.vars), self.rng.getstate(), self.rendered_text, self.notices)
        try:
            self._current = link.destination
            self.process()
        except Exception:
            self._current, variables, rng_state, self.rendered_text, self.notices = previous
            self.vars.clear()
            self.vars.update(variables)
            self.rng.setstate(rng_state)
            raise

    def __str__(self):
        return self.re_links.sub("", self.rendered_text).rstrip()

    def __repr__(self):
        return str(self)


class GameEngine:
    def __init__(self, game: Game):
        self.game = game
        self.running = bool(game.links)

    def validate_choice(self, choice):
        if type(choice) is not int or not 1 <= choice <= len(self.game.links):
            raise ValueError("Choose a number from the available actions")

    def next(self, choice=None):
        if choice is not None:
            self.validate_choice(choice)
            self.game.follow(self.game.links[choice - 1])
        self.running = bool(self.game.links)
        output = [str(self.game)]
        output.extend(f"[{i}. {link}]" for i, link in enumerate(self.game.links, 1))
        if not self.running:
            output.append("SLUTET")
        return "\n".join(output)

    def prompt(self):
        while self.running:
            try:
                action = input("> ").strip()
            except EOFError:
                return "q"
            command = action.casefold().removeprefix('/')
            if command in ('help', '?', '--help'):
                print('Enter a numbered choice; v/vars/variables shows state; q/quit/exit quits. Commands are case-insensitive.')
                continue
            if command in ('q', 'quit', 'exit'):
                return 'q'
            if command in ('v', 'vars', 'variables'):
                return 'v'
            try:
                choice = int(action)
                self.validate_choice(choice)
            except ValueError as error:
                print(f"Invalid action: {error}")
                continue
            return choice


@click.command()
@click.option("--story-file", type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path), help="Local Twine HTML file.")
@click.option("--seed", type=int, help="Seed for repeatable random outcomes.")
def cli(story_file, seed):
    try:
        engine = GameEngine(Game(load_game_data(game_file=story_file), seed=seed))
        print(engine.next())
        while engine.running:
            action = engine.prompt()
            if action == "q":
                break
            if action == "v":
                print(engine.game._current)
                print(engine.game.vars)
                for notice in engine.game.notices:
                    print(notice)
                continue
            print(engine.next(action))
    except (ValueError, OSError, requests.RequestException) as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    cli()
