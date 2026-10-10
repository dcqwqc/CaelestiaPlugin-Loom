"""Persistent task/chat/agent identity and exactly-once origin callback inbox.

This is an inbox, not a synthetic ChatGPT turn. ChatGPT external sessions
cannot be asynchronously resumed by an ordinary MCP server; they fetch and
acknowledge their own pending events when next invoked.
"""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from tabby.chat_projects import chat_route

DEFAULT_DB = Path.home() / '.local/share/tabby/agent-links.sqlite3'
TERMINAL = {'awaiting-review', 'review-rejected', 'done', 'routed-blocked', 'vaulted', 'working-move-failed', 'prompt-unconfirmed'}


class AgentLinks:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('LOOM_AGENT_LINKS_DB') or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError('Refusing symlink agent ledger')
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS links(
                task_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, agent_name TEXT NOT NULL,
                origin_ref TEXT NOT NULL, origin_url TEXT NOT NULL,
                chat_id TEXT NOT NULL, chat_url TEXT NOT NULL, task_name TEXT NOT NULL,
                chat_name TEXT NOT NULL DEFAULT '', sync_status TEXT NOT NULL DEFAULT 'pending',
                status TEXT NOT NULL, phase TEXT NOT NULL, updated_at REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS events(
                event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL,
                url TEXT NOT NULL, created_at REAL NOT NULL, acknowledged_at REAL NOT NULL DEFAULT 0)''')
            db.execute('CREATE INDEX IF NOT EXISTS events_by_agent ON events(agent_id,created_at)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA busy_timeout=5000')
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def agent_identity(task):
        ref = str(task.get('originRef') or '').strip()[:240]
        explicit = str(task.get('originAgentId') or '').strip()[:90]
        route = chat_route(ref)
        stable_ref = ('chatgpt:' + route['conversationId']) if route else ref
        agent_id = explicit or ('agent-' + hashlib.sha256(stable_ref.encode()).hexdigest()[:20] if ref else 'unbound-' + str(task['id']))
        return agent_id, ref

    def reconcile(self, task):
        """Idempotent record refresh; phase transitions enqueue one durable event."""
        ident = str(task.get('id') or '')
        if not ident:
            raise ValueError('task ID required')
        agent_id, ref = self.agent_identity(task)
        agent_name = str(task.get('originAgentName') or task.get('createdBy') or 'Loom agent')[:120]
        url = str(task.get('url') or '')
        route = chat_route(url)
        chat_id = route['conversationId'] if route else ''
        phase = str(task.get('phase') or '')
        status = str(task.get('status') or '')
        name = str(task.get('title') or '')[:160]
        now = time.time()
        with self.db() as db:
            prev = db.execute('SELECT * FROM links WHERE task_id=?', (ident,)).fetchone()
            # Existing identity is immutable: a lost origin must not silently
            # acquire somebody else's callback stream.
            if prev and prev['agent_id'] != agent_id and (prev['origin_ref'] or not prev['agent_id'].startswith('unbound-')):
                raise ValueError('task is already bound to another origin agent')
            if prev and prev['chat_id'] and chat_id and prev['chat_id'] != chat_id:
                raise ValueError('task conversation identity changed')
            if chat_id:
                duplicate = db.execute('SELECT task_id FROM links WHERE chat_id=? AND task_id<>?',(chat_id,ident)).fetchone()
                if duplicate:
                    raise ValueError('ChatGPT conversation already belongs to task '+duplicate['task_id'])
            chat_name = str(task.get('chatTitle') or (prev['chat_name'] if prev else ''))[:160]
            sync = str(task.get('titleSyncState') or (prev['sync_status'] if prev else 'pending'))[:32]
            db.execute('''INSERT INTO links(task_id,agent_id,agent_name,origin_ref,origin_url,chat_id,chat_url,
                task_name,chat_name,sync_status,status,phase,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET agent_id=excluded.agent_id,origin_ref=excluded.origin_ref,
                origin_url=excluded.origin_url,agent_name=excluded.agent_name,
                chat_id=excluded.chat_id,chat_url=excluded.chat_url,task_name=excluded.task_name,
                chat_name=excluded.chat_name,sync_status=excluded.sync_status,status=excluded.status,
                phase=excluded.phase,updated_at=excluded.updated_at''',
                (ident, agent_id, agent_name, ref, str(task.get('originUrl') or '')[:2048], chat_id, url,
                 name, chat_name, sync, status, phase, now))
            if ref and phase in TERMINAL and (not prev or prev['phase'] != phase or prev['status'] != status):
                # Include transition timestamp to distinguish multiple review
                # cycles, while repeated reconciliation of one state is a no-op.
                event_id = secrets.token_hex(16)
                db.execute('''INSERT INTO events(event_id,task_id,agent_id,kind,title,message,url,created_at)
                    VALUES(?,?,?,?,?,?,?,?)''', (event_id,ident,agent_id,phase,name,
                     str(task.get('response') or task.get('lastError') or task.get('summary') or '')[:2500],url,now))
        return self.get(ident)

    def get(self, task_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM links WHERE task_id=?', (str(task_id),)).fetchone()
        return dict(row) if row else None

    def list_links(self, limit=100):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM links ORDER BY updated_at DESC LIMIT ?', (max(1,min(250,int(limit))),))]

    def inbox(self, agent_id, limit=50):
        with self.db() as db:
            return [dict(r) for r in db.execute('''SELECT * FROM events WHERE agent_id=? AND acknowledged_at=0
                ORDER BY created_at,event_id LIMIT ?''',(str(agent_id),max(1,min(100,int(limit)))))]

    def acknowledge(self, agent_id, event_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM events WHERE event_id=? AND agent_id=?',(str(event_id),str(agent_id))).fetchone()
            if not row:
                return False
            db.execute('UPDATE events SET acknowledged_at=? WHERE event_id=? AND acknowledged_at=0',(time.time(),str(event_id)))
            return True

    def reply(self, task_id, event_key, message):
        """A worker can reply to its recorded origin; idempotent per event key."""
        row = self.get(task_id)
        message = str(message or '').strip()
        event_key = str(event_key or '').strip()
        if not row or not row['origin_ref'] or not message or len(message)>2500 or not event_key or len(event_key)>160:
            raise ValueError('linked task, origin, event_key and message required')
        event_id = hashlib.sha256((row['task_id']+'\0'+event_key).encode()).hexdigest()[:32]
        with self.db() as db:
            hit = db.execute('SELECT message FROM events WHERE event_id=?',(event_id,)).fetchone()
            if hit and hit['message']!=message:
                raise ValueError('event key is already bound to another message')
            db.execute('''INSERT OR IGNORE INTO events(event_id,task_id,agent_id,kind,title,message,url,created_at)
                VALUES(?,?,?,?,?,?,?,?)''', (event_id,row['task_id'],row['agent_id'],'reply',row['task_name'],message,
                row['chat_url'],time.time()))
        return {'event_id':event_id,'agent_id':row['agent_id'],'queued':True}
