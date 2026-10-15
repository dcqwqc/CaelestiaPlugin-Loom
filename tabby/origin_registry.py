"""Durable, fail-closed ChatGPT origin reference registry.

Stable references are routing handles, not authentication credentials. A
reference may be registered before its URL is known and bound later, but a
reference or conversation can never be reassigned.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from tabby.chat_projects import chat_route

DEFAULT_DB = Path.home() / ".local/share/tabby/origin-registry.sqlite3"


class OriginRegistry:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("LOOM_ORIGIN_REGISTRY_DB") or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("Refusing symlink origin registry")
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS origins(
                origin_ref TEXT PRIMARY KEY, conversation_id TEXT NOT NULL DEFAULT '',
                canonical_url TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS origins_by_conversation
                ON origins(conversation_id) WHERE conversation_id <> ''""")
            db.execute("""CREATE TABLE IF NOT EXISTS operations(
                operation_key TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
                result_json TEXT NOT NULL, created_at REAL NOT NULL)""")
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _ref(value):
        value = str(value or "").strip()
        if not 1 <= len(value) <= 240 or any(ord(c) < 32 for c in value):
            raise ValueError("origin_ref must be a stable nonempty identifier <=240 characters")
        return value

    @staticmethod
    def _url(value):
        value = str(value or "").strip()
        if not value:
            return "", ""
        route = chat_route(value)
        if not route:
            raise ValueError("origin_url must be an exact canonical https://chatgpt.com conversation URL")
        segment = route["projectSegment"]
        canonical = (f"https://chatgpt.com/g/{segment}/c/{route['conversationId']}"
                     if segment else f"https://chatgpt.com/c/{route['conversationId']}")
        return canonical, route["conversationId"]

    @staticmethod
    def _view(row, replay=False):
        out = dict(row)
        out["registered"] = True
        out["idempotent_replay"] = bool(replay)
        return out

    def register(self, origin_ref, *, origin_url="", operation_key="", source="explicit"):
        ref = self._ref(origin_ref)
        url, conversation_id = self._url(origin_url)
        key = str(operation_key or "").strip()
        if not 8 <= len(key) <= 160 or any(ord(c) < 32 for c in key):
            raise ValueError("operation_key must be a stable identifier of 8..160 characters")
        source = str(source or "explicit").strip()[:40] or "explicit"
        request = {"origin_ref": ref, "origin_url": url, "source": source}
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        now = time.time()
        with self.db() as db:
            replay = db.execute("SELECT * FROM operations WHERE operation_key=?", (key,)).fetchone()
            if replay:
                if replay["request_digest"] != digest:
                    raise ValueError("operation_key replay collision")
                return {**json.loads(replay["result_json"]), "idempotent_replay": True}
            current = db.execute("SELECT * FROM origins WHERE origin_ref=?", (ref,)).fetchone()
            if current and current["conversation_id"] and conversation_id and conversation_id != current["conversation_id"]:
                raise ValueError("origin_ref is already bound to another conversation")
            if conversation_id:
                owner = db.execute("SELECT origin_ref FROM origins WHERE conversation_id=?", (conversation_id,)).fetchone()
                if owner and owner["origin_ref"] != ref:
                    raise ValueError("conversation is already bound to another origin_ref")
            if current:
                db.execute("UPDATE origins SET conversation_id=?,canonical_url=?,source=?,updated_at=? WHERE origin_ref=?",
                           (current["conversation_id"] or conversation_id,
                            url or current["canonical_url"], source, now, ref))
            else:
                db.execute("INSERT INTO origins VALUES(?,?,?,?,?,?)",
                           (ref, conversation_id, url, source, now, now))
            row = db.execute("SELECT * FROM origins WHERE origin_ref=?", (ref,)).fetchone()
            result = self._view(row)
            db.execute("INSERT INTO operations VALUES(?,?,?,?)",
                       (key, digest, json.dumps(result, sort_keys=True), now))
        return result

    def resolve(self, origin_ref):
        ref = self._ref(origin_ref)
        with self.db() as db:
            row = db.execute("SELECT * FROM origins WHERE origin_ref=?", (ref,)).fetchone()
        return self._view(row) if row else None
