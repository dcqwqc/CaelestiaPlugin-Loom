"""Hive ledger <-> Tabby Working mirror (HAG-23).

The Hive ledger (tasks.json on Philipedia) is the only task store. Tabby
Working shows the cards that are being worked on right now:

  card doing    -> a Working task "HAG-n · title", status working
  card blocked  -> the same task, status blocked, summary says why
  card done     -> the task is completed, then removed after `done_linger_s`
  card todo / archived / deleted -> the task is removed
  backlog (todo) cards never appear.

The reverse path: completing a mirrored task in Tabby (MCP tabby_task_done,
or any work-complete), or setting it blocked / working again, edits the card
through hive_task.py, the ledger's one serialized writer. The edit is
conditional (if_status), so a stale Tabby view never overrides a newer card.
Removing a task from Working by hand only hides it there until the card's
status changes; it never changes the card.

Mapping, keyed by card id, lives in ~/.local/state/tabby/hive-mirror.json.
Each tick is idempotent: it compares the card's desired view with what Tabby
shows and with what the mirror itself last wrote, so it can tell a Tabby-side
edit from its own.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

STATE_PATH = Path(os.environ.get("TABBY_HIVE_MIRROR_STATE", Path.home() / ".local/state/tabby/hive-mirror.json"))
HIVE_TASK = Path(__file__).resolve().with_name("hive_task.py")
DEFAULT_HOST = "philipedia"
DEFAULT_ROOT = "/home/qwqc/HarnessAgents/hive"
ACTIVE = {"doing": "working", "blocked": "blocked"}
SEP = " · "

TabbyCmd = Callable[[dict[str, Any]], dict[str, Any]]


class HiveIO:
    """Reads the ledger and sends edits to the one writer, over SSH or locally."""

    def __init__(self, host: str = DEFAULT_HOST, root: str = DEFAULT_ROOT, by: str = "tabby-mirror"):
        self.host = host
        self.root = root
        self.by = by

    def _run(self, argv: list[str], stdin: bytes | None = None) -> bytes:
        if self.host not in ("", "local", "localhost"):
            import shlex
            argv = ["ssh", "-F", str(Path.home() / ".ssh/config"), "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2", self.host, " ".join(shlex.quote(a) for a in argv)]
        proc = subprocess.run(argv, input=stdin, capture_output=True, timeout=30)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise RuntimeError(proc.stderr.decode(errors="replace").strip() or f"exit {proc.returncode}")
        return proc.stdout

    def read(self) -> dict[str, Any]:
        out = self._run(["cat", f"{self.root}/tasks.json"])
        doc = json.loads(out)
        if not isinstance(doc, dict) or not isinstance(doc.get("tasks"), list):
            raise RuntimeError("tasks.json has no task list")
        return doc

    def write(self, req: dict[str, Any]) -> dict[str, Any]:
        # The writer's source goes over stdin, so Mirai and Philipedia can
        # never run different versions of it.
        req = {**req, "by": self.by}
        out = self._run(["python3", "-", "--root", self.root, "--request", json.dumps(req)], HIVE_TASK.read_bytes())
        res = json.loads(out.decode().strip().splitlines()[-1])
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "ledger write failed")
        return res


def blocked_reason(card: dict[str, Any]) -> str:
    reason = card.get("blocked")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    for qa in reversed(card.get("humanQA") or []):
        if isinstance(qa, dict) and qa.get("q") and not qa.get("a"):
            return "waiting on the human: " + " ".join(str(qa["q"]).split())
    return ""


def view(card: dict[str, Any]) -> dict[str, str]:
    """What Working should show for an active card."""
    status = ACTIVE[str(card.get("status"))]
    parts = [str(card.get("assignee") or "unassigned")]
    if status == "blocked":
        parts.append("blocked: " + (blocked_reason(card) or "no reason given"))
    title = " ".join(str(card.get("title") or "").split())
    return {
        "title": f"{card['id']}{SEP}{title}"[:160],
        "status": status,
        "summary": SEP.join(parts)[:300],
    }


class Mirror:
    def __init__(self, hive: Any, tabby: TabbyCmd, state_path: Path = STATE_PATH,
                 done_linger_s: float = 600.0, clock: Callable[[], float] = time.time, log: Callable[[str], None] = print):
        self.hive = hive
        self.tabby = tabby
        self.state_path = state_path
        self.done_linger_s = done_linger_s
        self.clock = clock
        self.log = log

    # -- state -------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text())
            if isinstance(data, dict) and isinstance(data.get("cards"), dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {"cards": {}}

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.state_path)

    def _tabby(self, cmd: dict[str, Any]) -> dict[str, Any]:
        res = self.tabby(cmd)
        if not res.get("ok"):
            raise RuntimeError(f"Tabby {cmd.get('command')}: {res.get('error') or res.get('result') or 'failed'}")
        return res

    @staticmethod
    def _snap(task: dict[str, Any]) -> dict[str, str]:
        return {k: str(task.get(k) or "") for k in ("title", "status", "summary")}

    # -- one pass ----------------------------------------------------------

    def tick(self) -> dict[str, int]:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path.with_suffix(".lock"), "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            return self._tick()

    def _tick(self) -> dict[str, int]:
        counts = {"created": 0, "updated": 0, "completed": 0, "removed": 0, "reverse": 0}
        ledger = self.hive.read()
        cards = {c["id"]: c for c in ledger["tasks"] if isinstance(c, dict) and isinstance(c.get("id"), str)}
        listed = self._tabby({"command": "work-list"})
        tasks = {str(t.get("id")): t for t in listed.get("tasks") or [] if isinstance(t, dict)}
        state = self._load()
        mapped: dict[str, dict[str, Any]] = state["cards"]
        now = self.clock()

        # Adopt mirrored tasks whose mapping was lost, so we never duplicate.
        owned = {e.get("tabby_id") for e in mapped.values()}
        for task in tasks.values():
            title = str(task.get("title") or "")
            card_id = title.split(SEP, 1)[0]
            if (task.get("kind") == "manual" and SEP in title and card_id in cards
                    and card_id not in mapped and task["id"] not in owned):
                mapped[card_id] = {"tabby_id": task["id"], "wrote": self._snap(task)}
                owned.add(task["id"])

        # 1. Reverse: edits made in Tabby go to the ledger first.
        skip_forward: set[str] = set()
        for card_id, entry in list(mapped.items()):
            task = tasks.get(entry.get("tabby_id", ""))
            card = cards.get(card_id)
            if task is None:
                if "hidden" not in entry:
                    # Removed from Working by hand: hide until the card moves.
                    entry["hidden"] = card.get("status") if card else None
                continue
            if card is None:
                continue
            wrote = entry.get("wrote") or {}
            now_t = self._snap(task)
            if now_t["status"] == wrote.get("status") and now_t["summary"] == wrote.get("summary"):
                continue
            try:
                res = self._reverse(card, wrote, now_t)
            except Exception as exc:  # leave both sides as they are; retry next tick
                self.log(f"{card_id}: Tabby edit not written to the ledger yet: {exc}")
                skip_forward.add(card_id)
                continue
            if res is not None:
                counts["reverse"] += 1
                if res.get("changed"):
                    cards[card_id] = res["card"]
                    self.log(f"{card_id}: Tabby -> ledger: status {res.get('status')}")
                elif res.get("skipped"):
                    self.log(f"{card_id}: Tabby edit ignored, {res['skipped']}")
            entry["wrote"] = now_t

        # 2. Forward: the ledger decides what Working shows.
        for card_id in sorted(set(cards) | set(mapped)):
            if card_id in skip_forward:
                continue
            card = cards.get(card_id)
            entry = mapped.get(card_id)
            task = tasks.get(entry["tabby_id"]) if entry else None
            status = str(card.get("status")) if card else None
            try:
                if status in ACTIVE:
                    if entry and "hidden" in entry:
                        if entry["hidden"] == status:
                            continue
                        entry = None  # the card moved since it was hidden
                        mapped.pop(card_id, None)
                    want = view(card)
                    if entry is None or task is None:
                        res = self._tabby({"command": "work-create", **want})
                        mapped[card_id] = {"tabby_id": res["task"]["id"], "wrote": self._snap(res["task"])}
                        counts["created"] += 1
                        self.log(f"{card_id}: added to Working ({want['status']})")
                        continue
                    if task.get("status") == "done":
                        task = self._tabby({"command": "work-reopen", "task_id": task["id"]})["task"]
                    diff = {k: v for k, v in want.items() if str(task.get(k) or "") != v}
                    if diff:
                        task = self._tabby({"command": "work-update", "task_id": task["id"], **diff})["task"]
                        counts["updated"] += 1
                        self.log(f"{card_id}: Working updated ({', '.join(sorted(diff))})")
                    entry["wrote"] = self._snap(task)
                elif entry is not None:
                    if task is None:
                        mapped.pop(card_id, None)
                        continue
                    if status == "done":
                        if task.get("status") != "done":
                            result = " ".join(str(card.get("result") or "").split())
                            summary = f"done{SEP}{result}"[:300] if result else "done"
                            task = self._tabby({"command": "work-complete", "task_id": task["id"], "summary": summary})["task"]
                            entry["wrote"] = self._snap(task)
                            counts["completed"] += 1
                            self.log(f"{card_id}: completed in Working")
                        entry.setdefault("done_at", now)
                        if now - float(entry["done_at"]) < self.done_linger_s:
                            continue
                    self._tabby({"command": "work-delete-user", "task_id": task["id"]})
                    mapped.pop(card_id, None)
                    counts["removed"] += 1
                    self.log(f"{card_id}: removed from Working ({status or 'gone from the ledger'})")
            except Exception as exc:
                self.log(f"{card_id}: {exc}")
        self._save(state)
        return counts

    def _reverse(self, card: dict[str, Any], wrote: dict[str, str], now_t: dict[str, str]) -> dict[str, Any] | None:
        status, summary = now_t["status"], now_t["summary"]
        own_summary = summary == wrote.get("summary")
        if status != wrote.get("status"):
            if status == "done":
                result = "" if own_summary or summary in ("", "done") else summary
                return self.hive.write({"op": "complete", "id": card["id"], "result": result or "completed in Tabby Working",
                                        "if_status": ["doing", "blocked"]})
            if status in ("blocked", "waiting"):
                reason = "" if own_summary else summary
                return self.hive.write({"op": "update", "id": card["id"], "if_status": ["doing"],
                                        "patch": {"status": "blocked", "blocked": reason or "blocked in Tabby Working"}})
            if status == "working":
                return self.hive.write({"op": "update", "id": card["id"], "if_status": ["blocked", "done"],
                                        "patch": {"status": "doing", "blocked": None}})
            return None
        if not own_summary and status in ("blocked", "waiting") and str(card.get("status")) == "blocked":
            reason = summary.split("blocked: ", 1)[-1]
            return self.hive.write({"op": "update", "id": card["id"], "if_status": ["blocked"], "patch": {"blocked": reason}})
        return None
