import textwrap
from pprint import pprint

import click
from dotenv import load_dotenv
import os
from game import Game, GameEngine, load_game_data

from langchain.chat_models import ChatOpenAI
from langchain.chains import ConversationChain
from langchain.memory import ConversationEntityMemory
from langchain.memory.prompt import PromptTemplate

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


def main(debug=False):
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

    spel = Game(load_game_data())
    ge = GameEngine(spel)
    # hoppa över titelskärm
    ge.next(1)

    out("Morsning Korsning barnförlossning! Nu kör vi!")
    christers_val = None
    while True:
        out('-'*max_columns)
        user_input = input("> ")
        out('-'*max_columns)
        if user_input == 'q':
            break
        elif user_input == 'm':
            pprint(conversation.memory.entity_store.store)
            continue
        elif user_input == 'v':
            print(ge.game._current)
            pprint(ge.game.vars)
            continue
        elif user_input != '':
            out("SYNTAX ERROR!, choose one of:\n v - show variables\n m - show entity memory\n q - quit\nor just press enter to continue game.")
            continue
        text = str(ge.next(christers_val))
        out(text)
        out("\nTänker...")
        ai_response = conversation.predict(input=text)

        out('='*max_columns)
        out("\nChrillePs val:\n", ai_response)
        out('='*max_columns)
        christers_val = int(ai_response.split(".")[0])

@click.command()
@click.option('--debug/--no-debug', default=False, help='debug output')
def cli(debug):
    if debug:
        out("debug on")
    main(debug=debug)

if __name__ == '__main__':
    cli()
