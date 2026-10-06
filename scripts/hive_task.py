#!/usr/bin/env python3
"""hive-task — the one serialized writer for the Hive task ledger (HAG-23).

The Hive ledger (<hive>/tasks.json on Philipedia) is the ONLY task store.
Tabby Working mirrors it; Loom, the Operator MCP and agents edit it. Every
writer that can cooperate goes through the same protocol, which is the
Operator MCP's (Sumi feat/hag21-operator-mcp-3, withLedgerLock):

  1. lock:   mkdir <hive>/tasks.json.lock, then write owner {pid, time};
             a lock older than 30 s or owned by a dead pid is stale.
  2. edit:   read the ledger, change one card, write a temp file beside it.
  3. rename: os.replace(temp, tasks.json) — readers never see a torn file.

Ticket ids come from the ledger's own counter ({"ticket": {"prefix", "next"}}),
never below an id already used in tasks.json or tasks-archive.json.

The hive app itself (Munder, an unmodified AppImage) cannot take this lock:
it rewrites tasks.json with an unlocked read -> merge -> rename inside one
synchronous tick. So, while holding the lock, we also (a) compare the bytes
just before our rename and start over if they changed, and (b) re-read after
the rename and redo the edit if a Munder write replaced ours. What remains is
a write landing between our final compare and our rename (microseconds); that
Munder write is lost. See docs/HIVE-WORKING-MIRROR.md.

Runs with the Python standard library only. Usage:

  hive-task show HAG-23
  hive-task list [--status doing,blocked]
  hive-task create "Title" assignee=claude-x priority=high
  hive-task update HAG-23 status=blocked blocked="needs Chrome" [--if-status doing]
  hive-task complete HAG-23 "what was done"
  hive-task delete HAG-23
  hive-task json < request.json        (machine use; also --request '<json>')

`key=value` sets a string, `key:=<json>` sets any JSON value (null removes
the field). Output is one JSON object: {"ok": true, ...} or {"ok": false, "error"}.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_ROOT = os.environ.get("HIVE_ROOT", "/home/qwqc/HarnessAgents/hive")
STATUSES = ("todo", "doing", "blocked", "done")
DOING = {"doing", "in-progress", "in_progress", "inprogress", "active", "wip"}
DONE = {"done", "complete", "completed", "closed"}
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
PROTECTED = {"id"}

LOCK_STALE_S = 30.0
LOCK_TRIES = 100
LOCK_WAIT_S = 0.1
CAS_TRIES = 8
VERIFY_DELAY_S = 0.05


class LedgerError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- lock


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, OverflowError):
        return False


def _lock_is_stale(lock: Path) -> bool:
    try:
        owner = json.loads((lock / "owner").read_text())
        age = time.time() - float(owner.get("time", 0)) / 1000.0
        return age > LOCK_STALE_S or not _pid_alive(int(owner.get("pid", 0)))
    except FileNotFoundError:
        # Between another writer's mkdir and its owner write. Only stale if old.
        try:
            return time.time() - lock.stat().st_mtime > LOCK_STALE_S
        except FileNotFoundError:
            return False
    except (ValueError, TypeError, json.JSONDecodeError):
        try:
            return time.time() - lock.stat().st_mtime > LOCK_STALE_S
        except FileNotFoundError:
            return False


def _remove_lock_dir(lock: Path) -> None:
    for child in list(lock.iterdir()) if lock.exists() else []:
        try:
            child.unlink()
        except FileNotFoundError:
            pass
    try:
        lock.rmdir()
    except FileNotFoundError:
        pass


class LedgerLock:
    """The Operator MCP's lock: a directory beside the ledger plus an owner file."""

    def __init__(self, ledger: Path, by: str = ""):
        self.lock = ledger.with_name(ledger.name + ".lock")
        self.by = by

    def __enter__(self) -> "LedgerLock":
        attempts = 0
        while attempts < LOCK_TRIES:
            try:
                self.lock.mkdir()
            except FileExistsError:
                if _lock_is_stale(self.lock):
                    # Rename first so two writers that both judged it stale
                    # cannot both delete it: only one rename succeeds.
                    grave = self.lock.with_name(f"{self.lock.name}.stale-{os.getpid()}-{time.time_ns()}")
                    try:
                        self.lock.rename(grave)
                        _remove_lock_dir(grave)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        attempts += 1
                        time.sleep(LOCK_WAIT_S)
                    continue
                attempts += 1
                time.sleep(LOCK_WAIT_S)
                continue
            (self.lock / "owner").write_text(json.dumps({
                "pid": os.getpid(), "time": int(time.time() * 1000),
                "host": socket.gethostname(), "by": self.by or "hive-task",
            }))
            return self
        raise LedgerError("ledger is locked by another writer (gave up after 10 s)")

    def __exit__(self, *exc: Any) -> None:
        _remove_lock_dir(self.lock)


# ---------------------------------------------------------------- ledger


def _read(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return b"", {"tasks": []}
    doc = json.loads(raw) if raw.strip() else {}
    if not isinstance(doc, dict):
        raise LedgerError(f"{path} is not a JSON object")
    if not isinstance(doc.get("tasks", []), list):
        raise LedgerError(f"{path}: tasks is not a list")
    doc.setdefault("tasks", [])
    return raw, doc


def _dump(doc: dict[str, Any]) -> bytes:
    # Same shape as the hive app's JSON.stringify(data, null, 2).
    return json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")


def _status(card: dict[str, Any]) -> str:
    value = card.get("status")
    return value.strip().lower() if isinstance(value, str) else ""


def find(tasks: list[Any], ref: str) -> dict[str, Any] | None:
    for card in tasks:
        if isinstance(card, dict) and card.get("id") == ref:
            return card
    for card in tasks:
        if isinstance(card, dict) and card.get("alias") == ref:
            return card
    return None


def stamp(card: dict[str, Any], was_status: str | None, now: str) -> None:
    """The hive app's stampTaskTimes for one card we changed."""
    status = _status(card)
    if was_status is None and not card.get("createdAt"):
        card["createdAt"] = now
    if status != (was_status or ""):
        if status in DOING and not card.get("startedAt"):
            card["startedAt"] = now
        if status in DONE and not card.get("doneAt"):
            card["doneAt"] = now
        if was_status and was_status in DONE and status not in DONE:
            card["reopenedAt"] = now
    card["updatedAt"] = now


def _next_ticket(doc: dict[str, Any], archive: list[Any]) -> tuple[str, int]:
    meta = doc.get("ticket") if isinstance(doc.get("ticket"), dict) else {}
    prefix = str(meta.get("prefix") or "HAG")
    nxt = int(meta.get("next") or 1)
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for card in list(doc["tasks"]) + list(archive):
        if isinstance(card, dict):
            m = pat.match(str(card.get("id") or ""))
            if m:
                nxt = max(nxt, int(m.group(1)) + 1)
    return prefix, nxt


def _check_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise LedgerError("nothing to change")
    for key, value in patch.items():
        if not FIELD_RE.match(str(key)):
            raise LedgerError(f"bad field name: {key!r}")
        if key in PROTECTED:
            raise LedgerError(f"field {key!r} cannot be changed")
        if key == "status" and value not in STATUSES:
            raise LedgerError(f"status must be one of {', '.join(STATUSES)}")
    return patch


class Ledger:
    def __init__(self, root: str | Path = DEFAULT_ROOT, by: str = ""):
        self.root = Path(root)
        self.path = self.root / "tasks.json"
        self.by = by

    def read(self) -> dict[str, Any]:
        return _read(self.path)[1]

    def _archive(self) -> list[Any]:
        try:
            doc = json.loads((self.root / "tasks-archive.json").read_text())
            return doc.get("tasks", []) if isinstance(doc, dict) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write(self, mutate: Callable[[dict[str, Any]], Any], verify: Callable[[dict[str, Any], Any], bool]) -> Any:
        """Lock, compare-and-swap, unlock; then check our edit survived."""
        for _ in range(CAS_TRIES):
            with LedgerLock(self.path, self.by):
                raw, doc = _read(self.path)
                result = mutate(doc)
                if result is None:  # nothing to change
                    return None
                tmp = self.path.with_name(f"{self.path.name}.tmp-hivetask-{os.getpid()}")
                with open(tmp, "wb") as f:
                    f.write(_dump(doc))
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.chmod(tmp, self.path.stat().st_mode & 0o7777)
                except FileNotFoundError:
                    pass
                if _read(self.path)[0] != raw:  # an unlocked writer got in: start over
                    tmp.unlink(missing_ok=True)
                    continue
                os.replace(tmp, self.path)
            # Outside the lock, so other writers are not held up by the wait.
            time.sleep(VERIFY_DELAY_S)
            if verify(_read(self.path)[1], result):
                return result
            # An unlocked writer replaced the file right after us: redo.
        raise LedgerError("tasks.json kept changing; edit not applied")

    # -- operations ------------------------------------------------------

    def create(self, fields: dict[str, Any]) -> dict[str, Any]:
        fields = dict(fields)
        title = str(fields.pop("title", "") or "").strip()
        if not title:
            raise LedgerError("a card needs a title")
        if fields:
            _check_patch(fields)
        fields.setdefault("status", "todo")
        if fields["status"] not in STATUSES:
            raise LedgerError(f"status must be one of {', '.join(STATUSES)}")
        archive = self._archive()

        def mutate(doc: dict[str, Any]) -> dict[str, Any]:
            prefix, nxt = _next_ticket(doc, archive)
            card = {"id": f"{prefix}-{nxt}", "title": title, **{k: v for k, v in fields.items() if v is not None}}
            if self.by:
                card["updatedBy"] = self.by
            stamp(card, None, now_iso())
            doc["tasks"].append(card)
            meta = doc.get("ticket") if isinstance(doc.get("ticket"), dict) else {}
            doc["ticket"] = {**meta, "prefix": prefix, "next": nxt + 1}
            return card

        card = self._write(mutate, lambda doc, c: find(doc["tasks"], c["id"]) is not None)
        return {"ok": True, "changed": True, "id": card["id"], "status": card["status"], "card": card}

    def update(self, ref: str, patch: dict[str, Any], if_status: list[str] | None = None) -> dict[str, Any]:
        patch = _check_patch(patch)
        outcome: dict[str, Any] = {}

        def mutate(doc: dict[str, Any]) -> dict[str, Any] | None:
            card = find(doc["tasks"], ref)
            if card is None:
                raise LedgerError(f"no card {ref}")
            outcome["card"] = card
            if if_status and _status(card) not in if_status:
                outcome["skipped"] = f"card is {card.get('status')!r}, expected {'/'.join(if_status)}"
                return None
            before = json.dumps(card, sort_keys=True)
            was = _status(card)
            for key, value in patch.items():
                if value is None:
                    card.pop(key, None)
                else:
                    card[key] = value
            if json.dumps(card, sort_keys=True) == before:
                return None
            if self.by:
                card["updatedBy"] = self.by
            stamp(card, was, now_iso())
            return card

        def verify(doc: dict[str, Any], card: dict[str, Any]) -> bool:
            hit = find(doc["tasks"], card["id"])
            if hit is None:
                return True  # deleted after our write: a newer edit, not ours lost
            ours, theirs = str(card.get("updatedAt") or ""), str(hit.get("updatedAt") or "")
            if theirs > ours:
                return True  # someone edited the card after us; never redo over it
            return all((key not in hit) if value is None else hit.get(key) == value for key, value in patch.items())

        card = self._write(mutate, verify)
        if card is None:
            current = outcome.get("card") or {}
            res = {"ok": True, "changed": False, "id": current.get("id", ref), "status": current.get("status"), "card": current}
            if "skipped" in outcome:
                res["skipped"] = outcome["skipped"]
            return res
        return {"ok": True, "changed": True, "id": card["id"], "status": card.get("status"), "card": card}

    def complete(self, ref: str, result: str = "", if_status: list[str] | None = None) -> dict[str, Any]:
        patch: dict[str, Any] = {"status": "done"}
        if result:
            patch["result"] = result
        return self.update(ref, patch, if_status)

    def delete(self, ref: str) -> dict[str, Any]:
        gone: dict[str, Any] = {}

        def mutate(doc: dict[str, Any]) -> dict[str, Any] | None:
            card = find(doc["tasks"], ref)
            if card is None:
                return None
            doc["tasks"] = [c for c in doc["tasks"] if c is not card]
            gone["card"] = card
            return card

        card = self._write(mutate, lambda doc, c: find(doc["tasks"], c["id"]) is None)
        return {"ok": True, "changed": card is not None, "id": ref}

    def get(self, ref: str) -> dict[str, Any]:
        card = find(self.read()["tasks"], ref)
        if card is None:
            raise LedgerError(f"no card {ref}")
        return {"ok": True, "id": card.get("id"), "status": card.get("status"), "card": card}

    def list(self, statuses: list[str] | None = None) -> dict[str, Any]:
        cards = [c for c in self.read()["tasks"] if isinstance(c, dict)]
        if statuses:
            cards = [c for c in cards if _status(c) in statuses]
        return {"ok": True, "tasks": cards}


def run_request(req: dict[str, Any], root: str | Path = DEFAULT_ROOT) -> dict[str, Any]:
    """One machine request: {op, id?, fields?/patch?, result?, if_status?, by?}."""
    if not isinstance(req, dict):
        raise LedgerError("request must be a JSON object")
    ledger = Ledger(req.get("root") or root, by=str(req.get("by") or ""))
    op = req.get("op")
    ref = str(req.get("id") or "")
    if op != "create" and op != "list" and not ID_RE.match(ref):
        raise LedgerError(f"bad card id: {ref!r}")
    if_status = req.get("if_status")
    if if_status is not None and (not isinstance(if_status, list) or not all(isinstance(s, str) for s in if_status)):
        raise LedgerError("if_status must be a list of statuses")
    if op == "get":
        return ledger.get(ref)
    if op == "list":
        return ledger.list(req.get("statuses"))
    if op == "create":
        return ledger.create(req.get("fields") or {})
    if op == "update":
        return ledger.update(ref, req.get("patch") or {}, if_status)
    if op == "complete":
        return ledger.complete(ref, str(req.get("result") or ""), if_status)
    if op == "delete":
        return ledger.delete(ref)
    raise LedgerError(f"unknown op: {op!r}")


# ---------------------------------------------------------------- CLI


def _parse_assignments(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if ":=" in item:
            key, raw = item.split(":=", 1)
            try:
                out[key] = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"{key}: not JSON ({exc})") from exc
        elif "=" in item:
            key, value = item.split("=", 1)
            out[key] = value
        else:
            raise LedgerError(f"expected key=value or key:=json, got {item!r}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hive-task", description="Edit the Hive task ledger through its one serialized writer.")
    p.add_argument("--root", default=DEFAULT_ROOT, help="hive directory (default: $HIVE_ROOT or %(default)s)")
    p.add_argument("--by", default=os.environ.get("HIVE_AGENT_ID", ""), help="who is writing (stored as updatedBy)")
    p.add_argument("--request", help="machine request as JSON (same as the json command)")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("show"); s.add_argument("id")
    s = sub.add_parser("list"); s.add_argument("--status", default="")
    s = sub.add_parser("create"); s.add_argument("title"); s.add_argument("fields", nargs="*")
    s = sub.add_parser("update"); s.add_argument("id"); s.add_argument("fields", nargs="+"); s.add_argument("--if-status", default="")
    s = sub.add_parser("complete"); s.add_argument("id"); s.add_argument("result", nargs="?", default=""); s.add_argument("--if-status", default="")
    s = sub.add_parser("delete"); s.add_argument("id")
    sub.add_parser("json")
    a = p.parse_args(argv)

    def statuses(text: str) -> list[str] | None:
        return [x.strip() for x in text.split(",") if x.strip()] or None

    try:
        if a.request is not None or a.cmd == "json":
            req = json.loads(a.request if a.request is not None else sys.stdin.read())
            if isinstance(req, dict) and a.by and not req.get("by"):
                req["by"] = a.by
            res = run_request(req, a.root)
        else:
            ledger = Ledger(a.root, by=a.by)
            if a.cmd == "show":
                res = ledger.get(a.id)
            elif a.cmd == "list":
                res = ledger.list(statuses(a.status))
            elif a.cmd == "create":
                res = ledger.create({"title": a.title, **_parse_assignments(a.fields)})
            elif a.cmd == "update":
                res = ledger.update(a.id, _parse_assignments(a.fields), statuses(a.if_status))
            elif a.cmd == "complete":
                res = ledger.complete(a.id, a.result, statuses(a.if_status))
            elif a.cmd == "delete":
                res = ledger.delete(a.id)
            else:
                p.print_help(sys.stderr)
                return 2
    except (LedgerError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
