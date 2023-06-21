# AI interactive fiction player

AI plays interactive fiction game in chanracter (twine game).

## Installation

Python 3.10 and above probably works well.

# Install dependencies:

note: Create a venv and activate it before installing dependencies.

```
pip install -r requirements.txt
```

# Set open api key

Create a `.env` file with your open-ai key:

```
OPENAI_API_KEY=...
```

# Play

## Start game with AI player

```
python ai_player.py
```

## Play as yourself

```
python game.py
```

## Console instructions

Press enter to trigger AI to make decision.

The game has a minimal set of commands that can be used to show current "memory" used by AI player:

 * m - show memory
 * v - show variables and current id of game story
 * q - exit game

