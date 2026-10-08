"""Live Loom Tasks tile: projects Philipedia LOOM ledger state for the native card.

Everything shown is derived from fields the LOOM bridge actually returned
(mission ``status``/``outcome`` and inbox idea records). There are no progress
percentages, ETAs or synthetic readings: a status the projection does not know
is shown verbatim as ``unknown``. When a refresh fails, the last good snapshot
is kept and explicitly marked stale with the error; nothing is invented.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from pathlib import Path

from tabby import missions

SNAPSHOT_VERSION = 1
CACHE_PATH = Path.home() / ".local/state/tabby/loom-tasks.json"
MAX_MISSIONS = 40
MAX_IDEAS = 40
# Same outcome vocabulary as scripts/tabby_loom_sync.py.
VERIFIED_OUTCOMES = {"autonomous_verified_success", "reviewed_verified_success"}
ACCEPTED_OUTCOMES = {"accepted_by_human"}
MISSION_STATES = {
    "queued": "queued", "pending": "queued", "new": "queued",
    "running": "running", "working": "running", "executing": "running", "active": "running",
    "review": "review", "awaiting_review": "review", "reviewing": "review", "verifying": "review",
    "paused": "blocked", "blocked": "blocked", "decision": "blocked", "waiting": "blocked",
    "failed": "failed", "error": "failed", "rejected": "failed",
    "cancelled": "cancelled", "canceled": "cancelled",
}
LABELS = {
    "queued": "Queued", "running": "Running", "review": "In review", "blocked": "Needs decision",
    "failed": "Failed", "cancelled": "Cancelled", "verified": "Verified", "accepted": "Accepted",
    "done": "Done · unverified", "captured": "Captured", "linked": "Mission linked",
    "unknown": "Unknown",
}
# Order used by the card: things needing attention first.
ORDER = ["blocked", "failed", "review", "running", "queued", "unknown",
         "done", "verified", "accepted", "cancelled", "linked", "captured"]


def _text(value, limit=160):
    if value is None:
        return ""
    return " ".join(str(value).split())[:limit]


def _time(value):
    """Return epoch seconds for numeric or ISO timestamps, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e12 else float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def mission_state(task):
    status = _text(task.get("status"), 40).lower()
    outcome = _text(task.get("outcome"), 80)
    if status in ("done", "completed", "complete"):
        if outcome in ACCEPTED_OUTCOMES:
            return "accepted"
        if outcome in VERIFIED_OUTCOMES:
            return "verified"
        return "done"
    return MISSION_STATES.get(status, "unknown")


def project_mission(task):
    state = mission_state(task)
    status = _text(task.get("status"), 40)
    return {
        "id": _text(task.get("id"), 80),
        "kind": "mission",
        "title": _text(task.get("title") or task.get("goal"), 160) or "Untitled mission",
        "state": state,
        "label": LABELS[state] if state != "unknown" else "Unknown · " + (status or "no status"),
        "status": status,
        "outcome": _text(task.get("outcome"), 80),
        "updated_at": _time(task.get("updatedAt") or task.get("updated_at") or task.get("ts")),
    }


def project_idea(idea):
    linked = _text(idea.get("mission_id") or idea.get("missionId") or idea.get("task_id"), 80)
    state = "linked" if linked else "captured"
    return {
        "id": _text(idea.get("id"), 80),
        "kind": "idea",
        "title": _text(idea.get("title") or idea.get("body"), 160) or "Untitled idea",
        "state": state,
        "label": LABELS[state] + (" · " + linked if linked else ""),
        "status": _text(idea.get("status"), 40),
        "outcome": "",
        "updated_at": _time(idea.get("captured_at") or idea.get("capturedAt") or idea.get("ts")),
    }


def _sorted(rows):
    rank = {s: i for i, s in enumerate(ORDER)}
    return sorted(rows, key=lambda r: (rank.get(r["state"], len(ORDER)), -(r["updated_at"] or 0)))


def _counts(rows):
    counts = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return counts


def project(status_obj, inbox_obj):
    """Project bridge responses (either may be None when unavailable)."""
    missions_rows = None
    if isinstance(status_obj, dict) and isinstance(status_obj.get("tasks"), list):
        missions_rows = _sorted([project_mission(t) for t in status_obj["tasks"]
                                 if isinstance(t, dict) and t.get("id")])[:MAX_MISSIONS]
    ideas_rows = None
    if isinstance(inbox_obj, dict) and isinstance(inbox_obj.get("ideas"), list):
        ideas_rows = _sorted([project_idea(i) for i in inbox_obj["ideas"]
                              if isinstance(i, dict) and i.get("id")])[:MAX_IDEAS]
    return {"missions": missions_rows, "ideas": ideas_rows,
            "counts": _counts((missions_rows or []) + (ideas_rows or []))}


def empty_snapshot():
    return {"version": SNAPSHOT_VERSION, "source": missions.HOST, "fetched_at": None,
            "missions": [], "ideas": [], "counts": {}, "stale": True,
            "errors": ["not refreshed yet"]}


class TasksCache:
    def __init__(self, path=CACHE_PATH):
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return empty_snapshot()
        if not isinstance(data, dict) or data.get("version") != SNAPSHOT_VERSION:
            return empty_snapshot()
        return data

    def save(self, snapshot):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_name(self.path.name + "." + secrets.token_hex(6) + ".tmp")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf8") as out:
                json.dump(snapshot, out, ensure_ascii=False, allow_nan=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()


def refresh(cache, *, fetch_missions=None, fetch_ideas=None, now=None):
    """Fetch both sources independently; a failing source keeps its last good rows."""
    fetch_missions = fetch_missions or missions.mission_list
    fetch_ideas = fetch_ideas or missions.idea_list
    previous = cache.load()
    errors, results = [], {}
    for name, fetch in (("missions", fetch_missions), ("ideas", fetch_ideas)):
        try:
            results[name] = fetch()
        except missions.MissionBridgeError as exc:
            results[name] = None
            errors.append("%s: %s" % (name, _text(exc, 240)))
    fresh = project(results["missions"], results["ideas"])
    if fresh["missions"] is None and results["missions"] is not None:
        errors.append("missions: bridge response had no task list")
    if fresh["ideas"] is None and results["ideas"] is not None:
        errors.append("ideas: bridge response had no idea list")
    got_any = fresh["missions"] is not None or fresh["ideas"] is not None
    snapshot = {
        "version": SNAPSHOT_VERSION, "source": missions.HOST,
        "fetched_at": (time.time() if now is None else now) if got_any else previous.get("fetched_at"),
        "missions": fresh["missions"] if fresh["missions"] is not None else previous.get("missions", []),
        "ideas": fresh["ideas"] if fresh["ideas"] is not None else previous.get("ideas", []),
        "stale": bool(errors),
        "errors": errors,
    }
    snapshot["counts"] = _counts(snapshot["missions"] + snapshot["ideas"])
    cache.save(snapshot)
    return snapshot
