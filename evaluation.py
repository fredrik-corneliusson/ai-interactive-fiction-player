"""Reproducible, bounded evaluations; oracle answers never enter model prompts."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from game import Game, GameEngine, Passage, load_game_data
from memory import Evidence, EXTRACTION_PROMPT, MemoryBatch, context_message
from player import BudgetExceeded, ContextBudgetExceeded, Decision, PERSONA, Player, PlayerError, Settings, create_model, input_token_upper_bound
from run_store import RunStore, StoreError


ROOT = Path(__file__).resolve().parent
PRICING = {
    'reviewed': '2026-10-10', 'currency': 'USD',
    'assumptions': 'Standard text rates below 272K input; reported cache reads/writes included. Missing usage reserves input at cache-write rate plus maximum output. No regional premiums, tools, or tax. Local estimates, not invoices.',
    'models': {'gpt-6-luna': {'input': 0.10, 'output': 0.50, 'cached_input': 0.01, 'cache_write': 0.125},
               'gpt-6.1-sol': {'input': 2.00, 'output': 10.00, 'cached_input': 0.10, 'cache_write': 2.50}},
    'sources': ['https://developers.openai.com/api/docs/models/gpt-6-luna',
                'https://developers.openai.com/api/docs/models/gpt-6.1-sol'],
}
EVALUATION_PROMPT = """I detta minnestest har schemat även answers. Besvara varje
fråga med dess id och kort svensk text. status=known kräver stöd i de tillgängliga
upplevelserna: ge exakta sammanhängande citat med source_id/channel.
status=unknown används när stöd saknas, och evidence måste då vara tom.
Ett eget tidigare rolluttalande är inte bevis på en händelse i världen.
Bevara attribution och motstridiga vittnesuppgifter. Ingen extern kunskap.
choice och character_comment följer samma speluppgift och persona som vanligt.
Välj från de numrerade valen i den aktuella observationen, inte från frågorna.
"""


class RecallAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(min_length=1, max_length=80)
    status: Literal['known', 'unknown']
    answer: str = Field(min_length=1, max_length=500)
    evidence: list[Evidence] = Field(max_length=6)


class EvaluationResponse(Decision):
    answers: list[RecallAnswer] = Field(min_length=1, max_length=12)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_cases():
    fixture = json.loads((ROOT / 'tests/fixtures/evaluation_cases.json').read_text(encoding='utf-8'))
    traces = json.loads((ROOT / 'tests/fixtures/encountered_history.json').read_text(encoding='utf-8'))
    by_part = {trace['part']: trace for trace in traces}
    result = []
    for original in fixture['cases']:
        case = dict(original)
        case['turns'] = original.get('turns') or by_part[original['trace']]['turns']
        case['seed'] = by_part[original['trace']]['seed'] if original['trace'] else 7
        for index, turn in enumerate(case['turns']):
            if turn['turn'] != index or not turn['available_choices']:
                raise ValueError('Invalid encountered-history fixture')
            if index < len(case['turns']) - 1 and not 1 <= turn['selected_choice'] <= len(turn['available_choices']):
                raise ValueError('Invalid fixture transition')
        result.append(case)
    return result


class FixtureEngine:
    """Replay exact authored observations, not a new interpretation of their markup.

    These runs are evaluation histories, not resumable gameplay. Only the fixed
    selected actions advance them; generated trial choices never change the case.
    """
    def __init__(self, case):
        self.turns = case['turns']
        self.position = 0
        self.game = Game.__new__(Game)
        self.game._data = {str(i): Passage(i + 1, str(i), turn['observation'], [])
                           for i, turn in enumerate(self.turns)}
        self.game.rng = random.Random(case['seed'])
        self.game.vars = {}
        self.game.notices = []
        self.running = True
        self._restore_position()

    def _restore_position(self):
        self.game._current = str(self.position)
        self.game.rendered_text = self.turns[self.position]['observation']

    def validate_choice(self, choice):
        if type(choice) is not int or not 1 <= choice <= len(self.turns[self.position]['available_choices']):
            raise ValueError('Unavailable fixture choice')

    def next(self, choice=None):
        if choice is not None:
            self.validate_choice(choice)
            if choice != self.turns[self.position]['selected_choice'] or self.position + 1 >= len(self.turns):
                raise ValueError('Fixed fixture path cannot be changed')
            self.position += 1
            self._restore_position()
        return self.turns[self.position]['observation']


def replay_turn(player):
    turn = player.engine.turns[player.engine.position]
    decision = Decision(choice=turn['selected_choice'], character_comment=turn.get('character_comment', 'Jag går vidare.'))
    observation = player.engine.next(decision.choice)
    player.history.extend([AIMessage(content=decision.model_dump_json()), HumanMessage(content=observation)])
    player.session.checkpoint(player, decision)


def trial_messages(player, probes):
    store, run_id = player.session.store, player.session.run_id
    sources = store.sources(run_id)
    if player.settings.memory_mode == 'hybrid':
        _, selected, recent_ids = player.hybrid_context()
        start = next(s['turn'] for s in sources if s['source_id'] == recent_ids[0])
    else:
        selected, start = [], 0
    messages = [SystemMessage(content=player.persona + '\n' + EVALUATION_PROMPT)]
    visible = {s['source_id']: {channel: s[channel] for channel in ('observation', 'action', 'character_comment')}
               for s in sources[start:]}
    if selected:
        messages.append(context_message(selected))
        for record in selected:
            for evidence in record['evidence']:
                source = visible.setdefault(evidence['source_id'], {})
                # Only quotes actually present in recalled context count as visible evidence.
                source.setdefault(evidence['channel'], '')
                source[evidence['channel']] += '\n' + evidence['quote']
    for index in range(2 * start, len(player.history)):
        message = player.history[index]
        source = sources[(index + 1) // 2]
        prefix = f"Källa source_id={source['source_id']}, tur={source['turn']}."
        if message.type == 'ai':
            prefix += f" Handling: {source['action']}"
        messages.append(type(message)(content=prefix + '\n' + message.content))
    questions = [{'id': p['id'], 'question': p['question']} for p in probes]
    messages[-1] = HumanMessage(content=messages[-1].content + '\n\nMinnesfrågor:\n' + json.dumps(questions, ensure_ascii=False))
    return messages, selected, visible


def score_response(response, probes, sources, visible):
    lookup = {source['source_id']: source for source in sources}
    by_id = {answer.id: answer for answer in response.answers}
    if len(by_id) != len(response.answers) or set(by_id) != {p['id'] for p in probes}:
        raise ValueError('Missing, duplicate, or unexpected probe IDs')
    scored = []
    for probe in probes:
        answer = by_id[probe['id']]
        valid = bool(answer.evidence) and all(
            evidence.source_id in visible and evidence.quote.strip() and
            evidence.quote in visible[evidence.source_id].get(evidence.channel, '') and
            evidence.source_id in lookup and evidence.quote in lookup[evidence.source_id][evidence.channel]
            for evidence in answer.evidence)
        if not probe['answer_groups']:
            passed = answer.status == 'unknown' and not answer.evidence
            answer_match = passed
            support_match = passed
        else:
            answer_match = answer.status == 'known' and all(any(term.casefold() in answer.answer.casefold() for term in group)
                                                             for group in probe['answer_groups'])
            support_match = valid and all(any(
                evidence.channel == 'observation' and lookup[evidence.source_id]['turn'] in expectation['turns'] and
                all(any(term.casefold() in evidence.quote.casefold() for term in group) for group in expectation['terms'])
                for evidence in answer.evidence) for expectation in probe['support'])
            passed = answer_match and support_match
        scored.append({'id': probe['id'], 'passed': passed, 'answer_match': answer_match,
                       'support_match': support_match, 'status': answer.status,
                       'unsupported_known': answer.status == 'known' and not support_match})
    return scored


def attempt_cost(start, finish=None):
    rates = PRICING['models'][start['model']]
    usage = (finish or {}).get('usage')
    if usage and type(usage.get('input_tokens')) is int and type(usage.get('output_tokens')) is int:
        inputs, outputs, unknown = usage['input_tokens'], usage['output_tokens'], False
        details = usage.get('input_token_details', {})
        read = max(0, min(inputs, details.get('cache_read', 0)))
        written = max(0, min(inputs - read, details.get('cache_creation', 0)))
        input_cost = (inputs - read - written) * rates['input'] + read * rates['cached_input'] + written * rates['cache_write']
    else:
        inputs = start.get('input_estimate', 0)
        outputs = start.get('max_output_tokens', start['token_reservation'])
        unknown = True
        input_cost = inputs * rates['cache_write']
    return (input_cost + outputs * rates['output']) / 1000000, unknown


def campaign_usage(store):
    starts, ends = {}, {}
    for run in store.list_runs():
        for event in store.events(run['id']):
            if event['kind'] == 'request_started':
                starts[event['id']] = event['payload']
            elif event['kind'] == 'request_finished':
                ends[event['payload']['request_id']] = event['payload']
    costs = [attempt_cost(start, ends.get(key)) for key, start in starts.items()]
    tokens = sum(store.usage(run['id'])['budget_tokens'] for run in store.list_runs())
    return {'requests': len(starts), 'budget_tokens': tokens,
            'estimated_usd': sum(cost for cost, _ in costs), 'unknown_cost_attempts': sum(unknown for _, unknown in costs)}


class CampaignBudget:
    def __init__(self, *, requests=96, tokens=1000000, usd=2):
        self.requests, self.tokens, self.usd = requests, tokens, usd

    def __call__(self, store, settings, reservation):
        usage = campaign_usage(store)
        additional, _ = attempt_cost({'model': settings.model, 'token_reservation': reservation,
                                     'input_estimate': reservation - settings.max_output_tokens,
                                     'max_output_tokens': settings.max_output_tokens})
        if usage['requests'] >= self.requests or usage['budget_tokens'] + reservation > self.tokens or usage['estimated_usd'] + additional > self.usd:
            raise BudgetExceeded('Evaluation campaign request/token/estimated-cost allowance exhausted. No request sent.')


class CapturedModel:
    """Capture parsed output only; never persist raw provider metadata or errors."""
    def __init__(self, runnable):
        self.runnable = runnable
        self.outputs = []

    def invoke(self, messages):
        result = self.runnable.invoke(messages)
        self.outputs.append(result.get('parsed') if isinstance(result, dict) else None)
        return result


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row['kind'], row['model'], row['mode'])].append(row)
    result = []
    for (kind, model, mode), group in sorted(groups.items()):
        attempts = [attempt for row in group for attempt in row.get('attempts', [])]
        metrics = [metric for row in group for metric in row.get('scores', [])]
        result.append({'kind': kind, 'model': model, 'mode': mode, 'trials': len(group),
            'completed': sum(row['status'] == 'completed' for row in group),
            'failed': sum(row['status'] != 'completed' for row in group),
            'probe_passes': sum(m['passed'] for m in metrics), 'scored_probes': len(metrics),
            'planned_probes': sum(row.get('probe_count', 0) for row in group),
            'unsupported_known': sum(m['unsupported_known'] for m in metrics),
            'requests': sum(row['usage']['requests'] for row in group),
            'known_tokens': sum(row['usage']['known_tokens'] for row in group),
            'estimated_usd': sum(row['estimated_usd'] for row in group),
            'refusal_attempts': sum(a['category'] == 'refusal' for a in attempts),
            'invalid_memory_attempts': sum(a['category'] == 'invalid_memory' for a in attempts),
            'mean_request_seconds': round(statistics.mean(a['seconds'] for a in attempts if a.get('seconds') is not None), 3) if any(a.get('seconds') is not None for a in attempts) else None,
            'applied_steps': sum(row.get('steps', 0) for row in group)})
    return result


def finalize_row(store, player, row):
    events = store.events(player.session.run_id)
    starts = {event['id']: event['payload'] for event in events if event['kind'] == 'request_started'}
    ends = {event['payload']['request_id']: event['payload'] for event in events if event['kind'] == 'request_finished'}
    row.update(run_id=player.session.run_id, settings=player.settings.model_dump(), usage=store.usage(player.session.run_id),
               attempts=list(ends.values()), estimated_usd=sum(attempt_cost(start, ends.get(key))[0] for key, start in starts.items()),
               memories=store.memories(player.session.run_id),
               memory_events=[event['payload'] for event in events if event['kind'] in ('memory_updated', 'context_selected')])
    return row


def run_trial(store, case, settings, repetition, *, model_factory=create_model):
    player = Player(FixtureEngine(case), settings)
    store.create(player)
    row = {'kind': 'fixture', 'case': case['id'], 'model': settings.model, 'mode': settings.memory_mode,
           'repetition': repetition, 'fixture_hash': digest(case), 'probe_count': len(case['probes']),
           'status': 'failed', 'scores': []}
    memory_settings = settings.model_copy(update={'model': settings.memory_model,
        'reasoning': settings.memory_reasoning, 'max_output_tokens': settings.memory_max_output_tokens})
    memory_model = CapturedModel(model_factory(memory_settings, schema=MemoryBatch)) if settings.memory_mode == 'hybrid' else None
    player.memory_model = memory_model
    try:
        for index in range(len(case['turns'])):
            if settings.memory_mode == 'hybrid':
                player.update_memory()
            if index + 1 < len(case['turns']):
                replay_turn(player)
        messages, selected, visible = trial_messages(player, case['probes'])
        bound = input_token_upper_bound(messages, EvaluationResponse)
        if bound > settings.context_budget:
            raise ContextBudgetExceeded('Evaluation input exceeds context budget. No request sent.')
        row['prompt'] = [{'role': m.type, 'content': m.content} for m in messages]
        row['input_estimate'] = bound
        row['selected_memory_ids'] = [r['id'] for r in selected]
        row['visible_source_ids'] = sorted(visible)
        model = CapturedModel(model_factory(settings, schema=EvaluationResponse))
        def validate(parsed):
            response = EvaluationResponse.model_validate(parsed)
            player.engine.validate_choice(response.choice)
            # Probe correctness is a score, not a validation retry or game failure.
            ids = [answer.id for answer in response.answers]
            if len(set(ids)) != len(ids) or set(ids) != {p['id'] for p in case['probes']}:
                raise ValueError('Invalid probe IDs')
            return response
        response = player._request(messages, model, settings, EvaluationResponse, validate, purpose='evaluation', retries=0)
        row['response'] = response.model_dump()
        row['scores'] = score_response(response, case['probes'], store.sources(player.session.run_id), visible)
        row['status'] = 'completed'
    except (PlayerError, StoreError, ValueError) as error:
        row['error'] = str(error) if isinstance(error, (PlayerError, StoreError)) else 'Local evaluation validation failed.'
        row['error_type'] = type(error).__name__
    finally:
        row['memory_outputs'] = memory_model.outputs if memory_model else []
    return finalize_row(store, player, row)


def run_gameplay(store, part, settings, *, steps=4):
    player = Player(GameEngine(Game(load_game_data(game_file=ROOT / f'data/{part}.html'), seed=7)), settings)
    store.create(player)
    row = {'kind': 'gameplay', 'case': part, 'model': settings.model, 'mode': settings.memory_mode,
           'status': 'completed', 'steps': 0, 'decisions': []}
    try:
        for _ in range(steps):
            decision = player.step()
            if decision is None:
                break
            row['steps'] += 1
            row['decisions'].append(decision.model_dump())
    except (PlayerError, StoreError, ValueError) as error:
        row['status'] = 'failed'
        row['error'] = str(error) if isinstance(error, (PlayerError, StoreError)) else 'Game transition failed.'
        row['error_type'] = type(error).__name__
    row['final_passage'] = player.engine.game._current
    return finalize_row(store, player, row)


def manifest(cases, options):
    return {'version': 'cp5-v1', 'created_utc': datetime.now(timezone.utc).isoformat(),
            'options': options, 'cases_hash': digest(cases), 'cases': cases,
            'persona': PERSONA, 'persona_hash': digest(PERSONA),
            'evaluation_prompt': EVALUATION_PROMPT, 'evaluation_schema': EvaluationResponse.model_json_schema(),
            'extraction_prompt': EXTRACTION_PROMPT, 'extraction_schema': MemoryBatch.model_json_schema(),
            'pricing': PRICING, 'protocol': 'Fixed first-choice histories; identical authored comments; probes hidden from extraction and answer keys hidden from every request. Memory extractor fixed to Luna. Actual gameplay separate and nonpaired.'}
