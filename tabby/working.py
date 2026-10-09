from __future__ import annotations
import json, os, re, threading, time, uuid
from pathlib import Path
from typing import Any

WORKING_PATH = Path.home() / ".local/state/tabby/working.json"
VALID_STATUS = {"working", "done", "waiting", "blocked"}
# Durable web-worker routing state (see tabby/web_workers.py).
WEB_WORKER_TEXT_FIELDS = ("lifecycle", "createdBy", "lastError", "routeReason")
WEB_WORKER_INT_FIELDS = ("expectedUserCount", "sendAttempts")


def _now() -> float:
    return time.time()


def _task_id(url: str) -> str:
    m = re.search(r"/c/([^/?#]+)", str(url or ""))
    if m:
        token = re.sub(r"[^A-Za-z0-9_-]", "", m.group(1))[:48]
        if token:
            return token
    return uuid.uuid4().hex[:16]


def _clean_title(title: str) -> str:
    value = " ".join(str(title or "").split()).strip()
    low = value.lower()
    if not value or low in {"chatgpt", "new chat", "tabby engine"} or low.startswith("startup instructions for this tabby conversation"):
        return "Working task"
    return value[:160]


class WorkingStore:
    def __init__(self, path: Path = WORKING_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._tasks: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text()) if self.path.exists() else []
            if isinstance(data, dict):
                data = data.get("tasks", [])
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []
        out = []
        for raw in data:
            # Chat tasks track a ChatGPT conversation; manual tasks (created
            # through the Tabby MCP) have no URL and are driven by tool calls.
            if not isinstance(raw, dict) or not (raw.get("url") or raw.get("kind") in {"manual", "web-worker"}):
                continue
            task = dict(raw)
            task["kind"] = str(raw.get("kind") or ("manual" if not raw.get("url") else "chat"))
            task["id"] = str(task.get("id") or _task_id(task.get("url", "")))[:64]
            task["url"] = str(task.get("url") or "")[:2048]
            task["title"] = _clean_title(task.get("title", ""))
            task["status"] = str(task.get("status") or "working")
            if task["status"] not in VALID_STATUS:
                task["status"] = "working"
            task["progress"] = max(0.0, min(1.0, float(task.get("progress") or 0.0)))
            task["createdAt"] = float(task.get("createdAt") or _now())
            task["updatedAt"] = float(task.get("updatedAt") or task["createdAt"])
            task["completedAt"] = float(task.get("completedAt") or 0.0)
            task["sawWorking"] = bool(task.get("sawWorking", False))
            task["baselineAssistantCount"] = int(task.get("baselineAssistantCount") or 0)
            task["summary"] = str(task.get("summary") or "")[:4000]
            for key in ("requestId", "prompt", "projectId", "projectName", "phase",
                        "reviewDecision", "reviewer", "reviewEvidence", "response",
                        *WEB_WORKER_TEXT_FIELDS, *WEB_WORKER_INT_FIELDS):
                if key in raw:
                    task[key] = raw[key]
            out.append(task)
        self._tasks = out

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"tasks": self._tasks}, indent=2, ensure_ascii=False) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(x) for x in self._tasks]

    def get(self, task_id: str) -> dict[str, Any] | None:
        task_id = str(task_id or "")
        with self._lock:
            hit = next((x for x in self._tasks if x.get("id") == task_id), None)
            return dict(hit) if hit else None

    def find_url(self, url: str) -> dict[str, Any] | None:
        url = str(url or "")
        with self._lock:
            hit = next((x for x in self._tasks if x.get("url") == url), None)
            return dict(hit) if hit else None

    def find_request(self, request_id: str) -> dict[str, Any] | None:
        request_id = str(request_id or "")
        with self._lock:
            hit = next((x for x in self._tasks if x.get("requestId") == request_id), None)
            return dict(hit) if hit else None

    def create_web_worker(self, request_id: str, title: str, prompt: str,
                          created_by: str = "") -> tuple[dict[str, Any], bool]:
        """Reserve one durable worker for an idempotency key before browser I/O."""
        request_id = str(request_id or "").strip()[:160]
        prompt = str(prompt or "").strip()[:50000]
        if not request_id or not prompt:
            raise ValueError("request_id and prompt are required")
        now = _now()
        with self._lock:
            existing = next((x for x in self._tasks if x.get("requestId") == request_id), None)
            if existing:
                if existing.get("prompt") != prompt:
                    raise ValueError("request_id already belongs to a different prompt")
                return dict(existing), False
            task = {
                "id": uuid.uuid4().hex[:16], "kind": "web-worker", "url": "",
                "requestId": request_id, "prompt": prompt, "title": _clean_title(title),
                "status": "waiting", "phase": "reserved", "progress": 0.0,
                "createdAt": now, "updatedAt": now, "completedAt": 0.0,
                "sawWorking": False, "baselineAssistantCount": 0, "promptAcknowledged": False, "summary": "",
                "projectId": "", "projectName": "", "reviewDecision": "",
                "reviewer": "", "reviewEvidence": "", "response": "",
                "lifecycle": "", "createdBy": str(created_by or "").strip()[:160], "lastError": "", "routeReason": "",
                "expectedUserCount": -1, "sendAttempts": 0,
            }
            self._tasks.insert(0, task)
            self._save()
            return dict(task), True

    def pin(self, url: str, title: str, *, saw_working: bool = False, baseline_assistant_count: int = 0) -> dict[str, Any]:
        url = str(url or "")[:2048]
        if not url:
            raise ValueError("missing chat url")
        now = _now()
        with self._lock:
            existing = next((x for x in self._tasks if x.get("url") == url), None)
            if existing:
                existing.update({
                    "title": _clean_title(title or existing.get("title", "")),
                    "status": "working", "updatedAt": now, "completedAt": 0.0,
                    "sawWorking": bool(saw_working or existing.get("sawWorking")),
                    "baselineAssistantCount": int(baseline_assistant_count or existing.get("baselineAssistantCount") or 0),
                })
                task = existing
            else:
                task = {
                    "id": _task_id(url), "kind": "chat", "url": url, "title": _clean_title(title),
                    "status": "working", "progress": 0.0, "createdAt": now,
                    "updatedAt": now, "completedAt": 0.0,
                    "sawWorking": bool(saw_working),
                    "baselineAssistantCount": int(baseline_assistant_count or 0),
                    "summary": "",
                }
                # Extremely unlikely, but keep IDs unique if a malformed URL collides.
                ids = {x.get("id") for x in self._tasks}
                if task["id"] in ids:
                    task["id"] = uuid.uuid4().hex[:16]
                self._tasks.insert(0, task)
            self._save()
            return dict(task)

    def create(self, title: str, *, summary: str = "", progress: float = 0.0, status: str = "working") -> dict[str, Any]:
        now = _now()
        task = {
            "id": uuid.uuid4().hex[:16], "kind": "manual", "url": "",
            "title": _clean_title(title),
            "status": status if status in VALID_STATUS else "working",
            "progress": max(0.0, min(1.0, float(progress or 0.0))),
            "createdAt": now, "updatedAt": now, "completedAt": 0.0,
            "sawWorking": False, "baselineAssistantCount": 0,
            "summary": str(summary or "")[:4000],
        }
        if task["status"] == "done":
            task["progress"], task["completedAt"] = 1.0, now
        with self._lock:
            self._tasks.insert(0, task)
            self._save()
            return dict(task)

    def reopen(self, task_id: str, summary: str | None = None) -> dict[str, Any] | None:
        task = self.get(task_id)
        if not task:
            return None
        if task.get("kind") == "web-worker":
            raise ValueError("web workers must be changed through loom_web_worker_review")
        progress = task.get("progress", 0.0)
        return self.update(
            task_id, status="working", completedAt=0.0, sawWorking=False,
            progress=0.0 if progress >= 1.0 else progress, summary=summary,
        )

    def update(self, task_id: str, **values: Any) -> dict[str, Any] | None:
        return self._update(task_id, False, **values)

    def _update(self, task_id: str, allow_web_worker_done: bool, **values: Any) -> dict[str, Any] | None:
        with self._lock:
            task = next((x for x in self._tasks if x.get("id") == str(task_id)), None)
            if not task:
                return None
            if (task.get("kind") == "web-worker" and values.get("status") == "done"
                    and not allow_web_worker_done):
                raise ValueError("web workers can only become done after verified review")
            if "title" in values and values["title"]:
                task["title"] = _clean_title(values["title"])
            if "status" in values and str(values["status"]) in VALID_STATUS:
                task["status"] = str(values["status"])
            if "progress" in values and values["progress"] is not None:
                task["progress"] = max(0.0, min(1.0, float(values["progress"])))
            if "sawWorking" in values:
                task["sawWorking"] = bool(values["sawWorking"])
            if "baselineAssistantCount" in values:
                task["baselineAssistantCount"] = max(0, int(values["baselineAssistantCount"] or 0))
            if "promptAcknowledged" in values:
                task["promptAcknowledged"] = bool(values["promptAcknowledged"])
            if "summary" in values and values["summary"] is not None:
                task["summary"] = str(values["summary"])[:4000]
            if "completedAt" in values:
                task["completedAt"] = float(values["completedAt"] or 0.0)
            for key in ("kind", "url", "phase", "projectId", "projectName",
                        "reviewDecision", "reviewer", "reviewEvidence", "response",
                        *WEB_WORKER_TEXT_FIELDS):
                if key in values and values[key] is not None:
                    task[key] = str(values[key])
            for key in WEB_WORKER_INT_FIELDS:
                if key in values and values[key] is not None:
                    task[key] = int(values[key])
            task["updatedAt"] = _now()
            self._save()
            return dict(task)

    def complete(self, task_id: str, summary: str = "") -> dict[str, Any] | None:
        task = self.get(task_id)
        if task and task.get("kind") == "web-worker":
            raise ValueError("web workers must be completed through loom_web_worker_review")
        return self.update(task_id, status="done", progress=1.0, completedAt=_now(), summary=summary)

    def complete_web_worker_review(self, task_id: str, **values: Any) -> dict[str, Any] | None:
        """The sole store transition that may mark a reviewed web worker done."""
        with self._lock:
            task = next((x for x in self._tasks if x.get("id") == str(task_id)), None)
            if not task:
                return None
            if (task.get("kind") != "web-worker" or task.get("phase") != "awaiting-review"
                    or values.get("reviewDecision") != "approved" or not values.get("reviewer")
                    or not values.get("reviewEvidence") or not values.get("projectId")
                    or not values.get("projectName")):
                raise ValueError("web worker is not eligible for verified completion")
            return self._update(task_id, True, status="done", progress=1.0, phase="done",
                                completedAt=_now(), **values)

    def delete_user(self, task_id: str) -> bool:
        with self._lock:
            before = len(self._tasks)
            self._tasks = [x for x in self._tasks if x.get("id") != str(task_id)]
            if len(self._tasks) == before:
                return False
            self._save()
            return True
