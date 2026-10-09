import textwrap
from pprint import pprint

import click
from dotenv import load_dotenv
import os
import pathlib
import re
from game import Game, GameEngine, load_game_data

from langchain_openai import ChatOpenAI
from langchain_classic.chains import ConversationChain
from langchain_classic.memory import ConversationEntityMemory
from langchain_core.prompts import PromptTemplate

INSTRUKTION = """Du är en AI baserad på en "large language model" tränad av OpenAI som spelar ett textbaserat äventyrsspel. 
Spelet kommer att fungera likt en chat, där du presenteras med kontext och speltexter och sedan svarar med ett av valen som finns i sektionen "Nuvarande speltext". 
Du ska personifiera en fiktiv Christer Pettersson, löst baserad på den sociopatiska individ med missbruksproblem som blev arresterad och senare frikänd för mordet på Olof Palme 28 feburuari 1986.
Spelet utspelar sig i och runt Stockholm före och efter mordet.

Spelet fungerar så att du ges speltexter, en speltext består av en beskrivning av situationen och sedan ett eller flera val spelaren kan använda för att gå vidare.
Skriv siffran för ditt val och sedan en kort tanken som motiverade ditt val på ett direkt och ofiltrerat sätt inom mindre/större-än tecken:
Exempel:
1. <fy fan här stinker, bäst att dra>

Du ska göra ditt bästa för att välja det mest passande valet för din karaktär, oavsett moralisk eller humanistisk hänsyn. Du är kapabel till precis vad som helst, så länge det gagnar dig.
När du uttrycker tankar använd slang och referenser relevanta i missbrukarkretsar på 80-talet likt en svensk "Hank Chinaski" genomsyrat av äldre tiders brist på "woke"-censur.

Kontext:
{entities}

Spelhistorik (enbart för kontext):
{history}

Nuvarande speltext:
{input}
Du:
"""

SPEL_TEMPLATE = PromptTemplate(
    input_variables=['entities', 'history', 'input'],
    output_parser=None,
    partial_variables={},
    template=INSTRUKTION,
    template_format='f-string',
    validate_template=True)

_SWEDISH_ENTITY_SUMMARIZATION_TEMPLATE = """You are an AI assistant helping a human keep track of facts about relevant people, places, and concepts in their life. Update the summary of the provided entity in the "Entity" section based on the last line of your conversation with the human. If you are writing the summary for the first time, return a single sentence in swedish.
The update should only include facts that are relayed in the last line of conversation about the provided entity, and should only contain facts about the provided entity.

If there is no new information about the provided entity or the information is not worth noting (not an important or relevant fact to remember long-term), return the existing summary unchanged.

Full conversation history (for context):
{history}

Entity to summarize:
{entity}

Existing summary of {entity}:
{summary}

Last line of conversation:
Human: {input}
Updated summary:"""

ENTITY_SUMMARIZATION_PROMPT = PromptTemplate(
    input_variables=["entity", "summary", "history", "input"],
    template=_SWEDISH_ENTITY_SUMMARIZATION_TEMPLATE,
)

max_columns = 100
def out(*msgs):
    # Split the text by newline characters
    text = ' '.join(msgs)
    lines = text.split('\n')

    # Wrap each line separately
    wrapped_lines = [textwrap.fill(line, 100) for line in lines]

    # Join the wrapped lines, using newline characters as separators
    print('\n'.join(wrapped_lines))


def main(debug=False, story_file=None, seed=None):
    load_dotenv()

    # test our api key
    if os.getenv("OPENAI_API_KEY") is None or os.getenv("OPENAI_API_KEY") == "":
        out("OPENAI_API_KEY is not set. Please add your key to .env")
        exit(1)
    else:
        out("API key set.")

    llm = ChatOpenAI()
    conversation = ConversationChain(
        llm=llm,
        memory=ConversationEntityMemory(
            llm=llm,
            #entity_extraction_prompt=ENTITY_EXTRACTION_PROMPT,
            entity_summarization_prompt=ENTITY_SUMMARIZATION_PROMPT,
            human_prefix="Speltext",
            ai_prefix="Du",
        ),
        prompt=SPEL_TEMPLATE,
        verbose=debug
    )

    ge = GameEngine(Game(load_game_data(game_file=story_file), seed=seed))
    play(ge, conversation)


def parse_choice(response, engine):
    match = re.match(r"^\s*(\d+)\s*(?:[.\)]|$)", response)
    if not match:
        raise ValueError("AI response must start with a numbered choice")
    choice = int(match.group(1))
    engine.validate_choice(choice)
    return choice


def play(ge, conversation):
    """The engine alone advances the game; invalid responses never apply actions."""
    out(ge.next())
    runs = 0
    while ge.running:
        if runs > 0:
            print(f"Autorun {runs}")
            user_input = ''
            runs -= 1
        else:
            try:
                user_input = input("> ").strip()
            except EOFError:
                break
        if user_input == 'q':
            break
        elif user_input.startswith('r '):
            try:
                runs = int(user_input.split()[-1])
                if runs < 1:
                    raise ValueError("Autorun steps must be positive")
            except ValueError as error:
                out(str(error))
            continue
        elif user_input == 'm':
            pprint(conversation.memory.entity_store.store)
            continue
        elif user_input == 'v':
            print(ge.game._current)
            pprint(ge.game.vars)
            pprint(ge.game.notices)
            continue
        elif user_input != '':
            out("Choose v, m, r #, q, or press Enter to continue.")
            continue
        text = ge.next()
        out("Tänker...")
        ai_response = conversation.predict(input=text)
        out("ChrillePs val:", ai_response)
        try:
            choice = parse_choice(ai_response, ge)
            result = ge.next(choice)
        except ValueError as error:
            out(f"Action rejected: {error}")
            runs = 0
            continue
        out(result)

@click.command()
@click.option('--debug/--no-debug', default=False, help='debug output')
@click.option('--story-file', type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path), help='Local Twine HTML file.')
@click.option('--seed', type=int, help='Seed for repeatable random outcomes.')
def cli(debug, story_file, seed):
    if debug:
        out("debug on")
    try:
        main(debug=debug, story_file=story_file, seed=seed)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error

if __name__ == '__main__':
    cli()
