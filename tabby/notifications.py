"""Durable local Loom notification inbox, independent of the ChatGPT/Quickshell session.

The MCP may create/read requests. User responses are accepted through the
desktop backend's UI command, not exposed as an MCP agent tool. They are NOT a
substitute for OS/API authorization for high-impact actions.
"""
from __future__ import annotations
import json
from contextlib import contextmanager
import os
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

KINDS = {"info", "status", "action", "approval", "choice"}
URGENCIES = {"low", "normal", "high"}
VALID_RESPONSES = {"approval", "choice"}


def default_db() -> Path:
    return Path(os.environ.get("LOOM_NOTIFICATIONS_DB")
                or Path.home() / ".local/share/tabby/notifications.sqlite3")


class NotificationStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else default_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.is_symlink():
            raise ValueError("Refusing symlink notification database")
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS notifications (
                 id TEXT PRIMARY KEY, request_key TEXT UNIQUE,
                 title TEXT NOT NULL, body TEXT NOT NULL,
                 kind TEXT NOT NULL, urgency TEXT NOT NULL,
                 options TEXT NOT NULL, status TEXT NOT NULL,
                 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                 expires_at INTEGER, response TEXT,
                 desktop_delivered INTEGER NOT NULL DEFAULT 0,
                 phone_delivered INTEGER NOT NULL DEFAULT 0)""")
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _public(row):
        if row is None:
            return None
        out = dict(row)
        out["options"] = json.loads(out["options"])
        out["desktop_delivered"] = bool(out["desktop_delivered"])
        out["phone_delivered"] = bool(out["phone_delivered"])
        return out

    def _expire(self, db, now: int):
        db.execute("""UPDATE notifications SET status='expired', updated_at=?
                      WHERE kind IN ('choice','approval') AND status IN ('pending','read')
                      AND expires_at IS NOT NULL AND expires_at<=?""", (now, now))

    def create(self, *, title: str, body: str = "", kind: str = "info",
               urgency: str = "normal", options=None, request_id: str = "",
               ttl_minutes: int | None = None):
        kind, urgency = str(kind), str(urgency)
        if kind not in KINDS or urgency not in URGENCIES:
            raise ValueError("Unknown notification kind or urgency")
        title, body = str(title).strip(), str(body).strip()
        if not title or len(title) > 140 or len(body) > 1500:
            raise ValueError("Title must be 1..140 and body at most 1500 characters")
        request_id = str(request_id or "").strip()
        if len(request_id) > 160:
            raise ValueError("request_id too long")
        opts = list(options or [])
        if kind == "approval":
            opts = opts or ["Approve", "Reject"]
        if kind in VALID_RESPONSES:
            if not 2 <= len(opts) <= 6 or any(not isinstance(o, str) or not o.strip()
                    or len(o) > 60 for o in opts) or len(set(opts)) != len(opts):
                raise ValueError("Decision needs 2..6 distinct options <=60 chars")
        elif opts:
            raise ValueError("Options only supported for approval/choice")
        if ttl_minutes is not None and (not isinstance(ttl_minutes, int)
                                         or not 1 <= ttl_minutes <= 60*24*30):
            raise ValueError("ttl_minutes must be 1..43200")
        now = int(time.time())
        expires = now + ttl_minutes * 60 if ttl_minutes is not None else None
        ident = uuid.uuid4().hex
        with self._db() as db:
            self._expire(db, now)
            if request_id:
                existing = db.execute("SELECT * FROM notifications WHERE request_key=?", (request_id,)).fetchone()
                if existing:
                    return {**self._public(existing), "created": False}
            # Reject accidental low/normal notification floods, but keep real
            # approval requests and high-priority events available.
            recent = db.execute("SELECT count(*) FROM notifications WHERE created_at>=? AND urgency!='high'",
                                (now-3600,)).fetchone()[0]
            if recent >= 50 and urgency != "high":
                raise ValueError("Hourly notification limit reached; reuse request_id")
            db.execute("""INSERT INTO notifications
              (id,request_key,title,body,kind,urgency,options,status,created_at,updated_at,expires_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (ident, request_id or None, title, body, kind, urgency,
               json.dumps(opts), "pending" if kind in VALID_RESPONSES else "unread",
               now, now, expires))
            result = self._public(db.execute("SELECT * FROM notifications WHERE id=?", (ident,)).fetchone())
        return {**result, "created": True}

    def get(self, ident: str):
        with self._db() as db:
            self._expire(db, int(time.time()))
            return self._public(db.execute("SELECT * FROM notifications WHERE id=?", (ident,)).fetchone())

    def list(self, *, limit: int = 30, include_closed: bool = True):
        limit = max(1, min(100, int(limit)))
        with self._db() as db:
            self._expire(db, int(time.time()))
            where = "" if include_closed else "WHERE status IN ('pending','unread','read')"
            return [self._public(r) for r in db.execute(
                f"SELECT * FROM notifications {where} ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))]

    def respond(self, ident: str, answer: str):
        with self._db() as db:
            now = int(time.time())
            self._expire(db, now)
            row = db.execute("SELECT * FROM notifications WHERE id=?", (str(ident),)).fetchone()
            if not row:
                raise ValueError("Notification not found")
            if row["kind"] not in VALID_RESPONSES:
                raise ValueError("Notification has no choices")
            if answer not in json.loads(row["options"]):
                raise ValueError("Invalid choice")
            if row["status"] == "answered":
                if row["response"] == answer:
                    return {**self._public(row), "idempotent": True}
                raise ValueError("Decision already answered")
            if row["status"] not in {"pending", "read"}:
                raise ValueError("Decision is no longer actionable")
            db.execute("""UPDATE notifications SET status='answered',response=?,updated_at=?
                          WHERE id=?""", (answer, now, ident))
            return self._public(db.execute("SELECT * FROM notifications WHERE id=?", (ident,)).fetchone())

    def mark(self, ident: str, *, dismiss=False):
        with self._db() as db:
            now = int(time.time())
            self._expire(db, now)
            row = db.execute("SELECT * FROM notifications WHERE id=?", (ident,)).fetchone()
            if not row:
                raise ValueError("Notification not found")
            if row["status"] in {"expired", "answered", "dismissed"}:
                return self._public(row)
            target = "dismissed" if dismiss else "read"
            db.execute("UPDATE notifications SET status=?,updated_at=? WHERE id=?", (target, now, ident))
            return self._public(db.execute("SELECT * FROM notifications WHERE id=?", (ident,)).fetchone())

    def delivered(self, ident: str, channel: str):
        field = {"desktop": "desktop_delivered", "phone": "phone_delivered"}.get(channel)
        if not field:
            raise ValueError("Unknown delivery channel")
        with self._db() as db:
            db.execute(f"UPDATE notifications SET {field}=1 WHERE id=?", (ident,))


def in_quiet_hours(spec: str, now=None) -> bool:
    """Return True for HH:MM-HH:MM local intervals, including overnight."""
    import datetime
    if not spec:
        return False
    try:
        first, last = spec.split("-", 1)
        def minutes(part):
            h, m = map(int, part.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("invalid quiet time")
            return 60 * h + m
        start, stop = minutes(first), minutes(last)
        clock = now or datetime.datetime.now().astimezone()
        current = clock.hour * 60 + clock.minute
        return ((start <= current < stop) if start < stop
                else (current >= start or current < stop) if start > stop else False)
    except (ValueError, AttributeError):
        return False


def deliver(item: dict, store: NotificationStore):
    """Deliver branded Loom alerts and optional private Tailnet phone choices.

    Provider acknowledgement does not prove handset receipt or authorization.
    """
    from urllib.parse import urlsplit
    result = {"desktop": "not_configured", "phone": "not_configured"}
    if item["urgency"] != "high" and in_quiet_hours(os.environ.get("LOOM_QUIET_HOURS", "")):
        return {"desktop": "quiet_hours", "phone": "quiet_hours"}
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS") and (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        try:
            subprocess.run(["notify-send", "-a", "Loom", "--", item["title"], item["body"]],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3, check=True)
            store.delivered(item["id"], "desktop")
            result["desktop"] = "delivered"
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            result["desktop"] = "failed"
    url = os.environ.get("LOOM_NTFY_URL", "").strip()
    if not url:
        return result
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.path.strip("/"):
        result["phone"] = "invalid_url"
        return result
    try:
        message = {"topic": parsed.path.strip("/"), "title": "Loom · " + item["title"],
                   "message": item["body"] or item["title"],
                   "priority": 4 if item["urgency"] == "high" else 3}
        icon_url = os.environ.get("LOOM_NTFY_ICON_URL", "").strip()
        if icon_url.startswith("https://"):
            message["icon"] = icon_url
        if item["kind"] in {"approval", "choice"}:
            callback = os.environ.get("LOOM_PHONE_ACTION_BASE", "").strip()
            if not callback:
                result["phone"] = "callback_not_configured"
                return result
            if len(item["options"]) > 3:
                result["phone"] = "too_many_phone_choices"
                return result
            from tabby.phone_actions import prepare
            message["actions"] = prepare(item, store, callback)
        headers = {"Content-Type": "application/json"}
        token_file = os.environ.get("LOOM_NTFY_TOKEN_FILE", "")
        if token_file:
            headers["Authorization"] = "Bearer " + Path(token_file).read_text().strip()
        endpoint = f"{parsed.scheme}://{parsed.netloc}/"
        request = urllib.request.Request(endpoint, data=json.dumps(message).encode("utf-8"),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=6) as response:
            if response.status not in {200, 201, 202}:
                raise OSError("Provider rejected push")
        store.delivered(item["id"], "phone")
        result["phone"] = "accepted_by_provider"
    except Exception:
        result["phone"] = "failed"
    return result
