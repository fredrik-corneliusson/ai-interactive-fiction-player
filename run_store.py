"""Local checkpoints and append-only telemetry. No provider payloads or credentials."""

import hashlib
import json
from pathlib import Path
import random
import sqlite3
import time
import uuid

from langchain_core.messages import AIMessage, HumanMessage

from game import Game, GameEngine, Passage, StoryData


class StoreError(RuntimeError):
    pass


def snapshot(player):
    game = player.engine.game
    return {
        'passage': game._current, 'variables': dict(game.vars),
        'rng': game.rng.getstate(), 'rendered_text': game.rendered_text,
        'notices': list(game.notices), 'running': player.engine.running,
        'history': [{'role': m.type, 'content': m.content} for m in player.history],
    }


def restore(player, state):
    """Restore rendered state directly: never execute passage macros twice."""
    game = player.engine.game
    game._current = state['passage']
    game.vars = dict(state['variables'])
    version, internal, gaussian = state['rng']
    game.rng.setstate((version, tuple(internal), gaussian))
    game.rendered_text = state['rendered_text']
    game.notices = list(state['notices'])
    player.engine.running = state['running']
    player.history = [(HumanMessage if m['role'] == 'human' else AIMessage)(content=m['content'])
                      for m in state['history']]


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class RunStore:
    def __init__(self, path, *, request_guard=None):
        self.request_guard = request_guard
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2):
            self.db.close()
            raise StoreError('Unsupported run database version.')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                revision INTEGER NOT NULL, story TEXT NOT NULL, story_hash TEXT NOT NULL,
                settings TEXT NOT NULL, persona TEXT NOT NULL, state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                kind TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id, id);
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT, 'Events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'Events are append-only'); END;
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(id),
                batch_event_id INTEGER NOT NULL REFERENCES events(id),
                record TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS memories_by_run ON memories(run_id, id);
            CREATE TRIGGER IF NOT EXISTS memories_no_update BEFORE UPDATE ON memories
                BEGIN SELECT RAISE(ABORT, 'Memories are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS memories_no_delete BEFORE DELETE ON memories
                BEGIN SELECT RAISE(ABORT, 'Memories are append-only'); END;
            PRAGMA user_version=2;
        ''')

    def close(self):
        self.db.close()

    def _event(self, run_id, kind, payload):
        cursor = self.db.execute('INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)',
                                 (run_id, kind, encode(payload)))
        return cursor.lastrowid

    def create(self, player):
        data = player.engine.game._data
        story = {'start': getattr(data, 'start_passage', next(iter(data))),
                 'passages': [{'pid': p.pid, 'name': p.name, 'text': p.text} for p in data.values()]}
        serialized = encode(story)
        run_id = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO runs(id,revision,story,story_hash,settings,persona,state) VALUES(?,0,?,?,?,?,?)',
                            (run_id, serialized, hashlib.sha256(serialized.encode()).hexdigest(),
                             player.settings.model_dump_json(), player.persona, encode(snapshot(player))))
            self._event(run_id, 'created', {'story_hash': hashlib.sha256(serialized.encode()).hexdigest(),
                                          'passage': player.engine.game._current,
                                          'observation': player.history[-1].content})
        session = RunSession(self, run_id, 0)
        player.session = session
        return session

    def resume(self, run_id, *, model=None, sleep=None, memory_model=None):
        from player import Player, Settings
        row = self.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise StoreError('Unknown run ID.')
        if hashlib.sha256(row['story'].encode()).hexdigest() != row['story_hash']:
            raise StoreError('Stored story checksum does not match.')
        story = json.loads(row['story'])
        data = StoryData({p['name']: Passage(**p, links=[]) for p in story['passages']}, story['start'])
        # Bypass Game.__init__ and its process(): this is a checkpoint, not a replay.
        game = Game.__new__(Game)
        game._data = data
        game.rng = random.Random()
        engine = GameEngine.__new__(GameEngine)
        engine.game = game
        player = Player.__new__(Player)
        player.engine = engine
        player.settings = Settings.model_validate_json(row['settings'])
        player.settings.check_compatibility()
        player.persona = row['persona']
        player.model = model
        player.memory_model = memory_model
        player.sleep = sleep if sleep is not None else time.sleep
        player.last_call = None
        player.calls = []
        player.session = RunSession(self, run_id, row['revision'])
        restore(player, json.loads(row['state']))
        return player

    def events(self, run_id):
        return [dict(row) | {'payload': json.loads(row['payload'])} for row in
                self.db.execute('SELECT * FROM events WHERE run_id=? ORDER BY id', (run_id,))]

    def usage(self, run_id):
        events = self.events(run_id)
        starts = {e['id']: e['payload'] for e in events if e['kind'] == 'request_started'}
        ends = {e['payload']['request_id']: e['payload'] for e in events if e['kind'] == 'request_finished'}
        known = 0
        charged = 0
        unknown = 0
        for key, start in starts.items():
            usage = ends.get(key, {}).get('usage')
            tokens = usage.get('total_tokens') if usage else None
            if type(tokens) is int and tokens >= 0:
                known += tokens
                charged += tokens
            else:
                unknown += 1
                charged += start['token_reservation']
        return {'requests': len(starts), 'known_tokens': known, 'budget_tokens': charged,
                'unknown_usage_requests': unknown, 'pending_requests': len(starts.keys() - ends.keys()),
                'seconds': round(sum(e.get('seconds') or 0 for e in ends.values()), 3),
                'unknown_latency_requests': sum(e.get('seconds') is None for e in ends.values()) + len(starts.keys() - ends.keys()),
                'actions': sum(e['kind'] == 'action' for e in events)}

    def list_runs(self):
        return [dict(row) for row in self.db.execute(
            'SELECT id,created,revision,story_hash FROM runs ORDER BY created,id')]

    def sources(self, run_id):
        """Only committed, encountered material. Never consult hidden game variables."""
        result = []
        for event in self.events(run_id):
            if event['kind'] not in ('created', 'action'):
                continue
            payload = event['payload']
            decision = payload.get('decision', {})
            result.append({'source_id': event['id'], 'turn': len(result),
                'passage': payload.get('passage', '(initial passage)'),
                'observation': payload['observation'],
                'action': f"Valde alternativ {decision['choice']}." if decision else '',
                'character_comment': decision.get('character_comment', '')})
        return result

    def pending_sources(self, run_id):
        updates = [event for event in self.events(run_id) if event['kind'] == 'memory_updated']
        last = updates[-1]['payload']['last_source_id'] if updates else 0
        return [source for source in self.sources(run_id) if source['source_id'] > last]

    def memories(self, run_id):
        return [{'id': row['id'], **json.loads(row['record'])} for row in self.db.execute(
            'SELECT id,record FROM memories WHERE run_id=? ORDER BY id', (run_id,))]

    def report(self, run_id):
        row = self.db.execute('SELECT settings,state FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise StoreError('Unknown run ID.')
        return {'run_id': run_id, 'settings': json.loads(row['settings']),
                'passage': json.loads(row['state'])['passage'],
                'running': json.loads(row['state'])['running'], 'usage': self.usage(run_id),
                'attempts': [e for e in self.events(run_id) if e['kind'].startswith('request_')],
                'memory': self.resume(run_id).inspect_memory(),
                'memory_events': [e for e in self.events(run_id) if e['kind'] in ('memory_updated', 'context_selected')]}


class RunSession:
    def __init__(self, store, run_id, revision):
        self.store, self.run_id, self.revision = store, run_id, revision

    def _claim(self):
        cursor = self.store.db.execute('UPDATE runs SET revision=revision+1 WHERE id=? AND revision=?',
                                       (self.run_id, self.revision))
        if cursor.rowcount != 1:
            raise StoreError('Run changed in another session. Resume it again before continuing.')

    def begin_request(self, settings, reservation, attempt, *, purpose='decision'):
        from player import BudgetExceeded
        with self.store.db:
            self._claim()  # Serializes both budget check and reservation.
            usage = self.store.usage(self.run_id)
            if usage['pending_requests']:
                raise StoreError('An earlier request is unresolved. Inspect the run; acknowledge with --acknowledge-pending before continuing.')
            if usage['requests'] >= settings.request_budget:
                raise BudgetExceeded('Run request budget exhausted. No request sent.')
            if usage['budget_tokens'] + reservation > settings.token_budget:
                raise BudgetExceeded('Run token budget cannot cover the next request reservation. No request sent.')
            if self.store.request_guard is not None:
                self.store.request_guard(self.store, settings, reservation)
            request_id = self.store._event(self.run_id, 'request_started',
                {'model': settings.model, 'attempt': attempt, 'token_reservation': reservation, 'purpose': purpose,
                 'input_estimate': reservation - settings.max_output_tokens,
                 'max_output_tokens': settings.max_output_tokens})
        self.revision += 1
        return request_id

    def finish_request(self, request_id, metadata):
        with self.store.db:
            self._claim()
            self.store._event(self.run_id, 'request_finished', {'request_id': request_id, **metadata})
        self.revision += 1

    def acknowledge_pending(self):
        """Explicit crash recovery: no replay; retain unknown usage reservation."""
        events = self.store.events(self.run_id)
        ends = {e['payload']['request_id'] for e in events if e['kind'] == 'request_finished'}
        with self.store.db:
            self._claim()
            for event in events:
                if event['kind'] == 'request_started' and event['id'] not in ends:
                    self.store._event(self.run_id, 'request_finished',
                        {'request_id': event['id'], 'category': 'interrupted_unknown', 'usage': None,
                         'seconds': None, 'model': event['payload']['model'], 'status': None})
        self.revision += 1

    def checkpoint(self, player, decision=None):
        with self.store.db:
            self._claim()
            self.store.db.execute('UPDATE runs SET state=? WHERE id=?', (encode(snapshot(player)), self.run_id))
            self.store._event(self.run_id, 'action' if decision else 'saved',
                              {'decision': decision.model_dump(), 'passage': player.engine.game._current,
                               'observation': player.history[-1].content}
                              if decision else {'passage': player.engine.game._current})
        self.revision += 1

    def commit_memories(self, sources, batch):
        from memory import validate_batch
        # Validate against local, run-scoped sources again at the write boundary.
        local = self.store.pending_sources(self.run_id)[:len(sources)]
        if local != sources:
            raise StoreError('Memory source batch changed; resume before continuing.')
        validate_batch(batch.model_dump(), local)
        refs = {s['source_id']: {'source_id': s['source_id'], 'turn': s['turn'], 'passage': s['passage']} for s in local}
        with self.store.db:
            self._claim()
            batch_id = self.store._event(self.run_id, 'memory_updated',
                {'source_ids': [s['source_id'] for s in sources], 'last_source_id': sources[-1]['source_id'],
                 'record_count': len(batch.records), 'policy_version': 'cp4-v1'})
            for record in batch.records:
                data = record.model_dump()
                data['source_refs'] = [refs[e.source_id] for e in record.evidence]
                self.store.db.execute('INSERT INTO memories(run_id,batch_event_id,record) VALUES(?,?,?)',
                                      (self.run_id, batch_id, encode(data)))
        self.revision += 1

    def record_context(self, selected, recent_ids, estimate):
        with self.store.db:
            self._claim()
            self.store._event(self.run_id, 'context_selected',
                {'memory_ids': [record['id'] for record in selected], 'recent_source_ids': recent_ids,
                 'input_estimate': estimate,
                 'memory_bytes': len(encode(selected).encode('utf-8')), 'policy_version': 'cp4-v1'})
        self.revision += 1
