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
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from urllib import error, parse, request

from tabby.chat_projects import chat_route

DEFAULT_DB = Path.home() / ".local/share/tabby/origin-registry.sqlite3"
DEFAULT_REMOTE_TIMEOUT = 3.0
MAX_RESPONSE_BODY = 16 * 1024


class OriginRegistryError(ValueError):
    """A safe, user-facing origin registry failure."""


class OriginRegistryUnavailable(OriginRegistryError):
    """The authoritative remote registry could not be reached."""


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
            # Serialize the read/check/write sequence across processes.
            db.execute("BEGIN IMMEDIATE")
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


def _private_token(path):
    token_path = Path(path)
    if token_path.is_symlink():
        raise OriginRegistryUnavailable("origin registry token file must not be a symlink")
    try:
        info = token_path.stat()
    except OSError as exc:
        raise OriginRegistryUnavailable("origin registry credentials are unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise OriginRegistryUnavailable("origin registry token file must be a regular mode-0600 file")
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OriginRegistryUnavailable("origin registry credentials are unavailable") from exc
    if not 32 <= len(token) <= 512 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise OriginRegistryUnavailable("origin registry token is invalid")
    return token


class RemoteOriginRegistry:
    """Fail-closed stdlib client for the authoritative registry service."""

    def __init__(self, url, token_file, *, timeout=DEFAULT_REMOTE_TIMEOUT):
        parsed = parse.urlsplit(str(url or "").strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("LOOM_ORIGIN_REGISTRY_URL must be an http(s) service URL without credentials")
        self.url = parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self.token_file = Path(token_file)
        self.timeout = max(0.1, min(float(timeout), 30.0))

    def _call(self, action, payload):
        token = _private_token(self.token_file)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = request.Request(
            f"{self.url}/v1/{action}", data=body, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BODY + 1)
        except error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BODY + 1)
            try:
                detail = json.loads(raw.decode("utf-8"))["error"]["message"]
            except (KeyError, TypeError, ValueError, UnicodeDecodeError):
                detail = "origin registry request was rejected"
            raise OriginRegistryError(str(detail)) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise OriginRegistryUnavailable("authoritative origin registry is unavailable") from exc
        if len(raw) > MAX_RESPONSE_BODY:
            raise OriginRegistryUnavailable("origin registry response is too large")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise OriginRegistryUnavailable("origin registry returned an invalid response") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise OriginRegistryUnavailable("origin registry returned an invalid response")
        return result.get("origin")

    def register(self, origin_ref, *, origin_url="", operation_key="", source="explicit"):
        return self._call("register", {"origin_ref": origin_ref, "origin_url": origin_url,
                                       "operation_key": operation_key, "source": source})

    def resolve(self, origin_ref):
        return self._call("resolve", {"origin_ref": origin_ref})


def create_origin_registry(environ=None):
    """Select local or remote storage without contacting the remote service."""
    env = os.environ if environ is None else environ
    url = str(env.get("LOOM_ORIGIN_REGISTRY_URL") or "").strip()
    token_file = str(env.get("LOOM_ORIGIN_REGISTRY_TOKEN_FILE") or "").strip()
    if url:
        if not token_file:
            raise ValueError("LOOM_ORIGIN_REGISTRY_TOKEN_FILE is required for a remote origin registry")
        return RemoteOriginRegistry(url, token_file)
    if token_file:
        raise ValueError("LOOM_ORIGIN_REGISTRY_URL is required when a remote token file is configured")
    return OriginRegistry(env.get("LOOM_ORIGIN_REGISTRY_DB") or None)
