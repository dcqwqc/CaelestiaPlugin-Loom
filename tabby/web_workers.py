"""Durable ChatGPT web-worker lifecycle; browser details stay in ZenClient."""
from __future__ import annotations

from urllib.parse import urlparse
import time


def canonical_chat_url(value: str) -> bool:
    u = urlparse(str(value or ""))
    parts = [p for p in u.path.split("/") if p]
    return (u.scheme == "https" and u.netloc == "chatgpt.com" and len(parts) == 2
            and parts[0] == "c" and not parts[1].startswith("local-chatgpt"))


class WebWorkerManager:
    def __init__(self, store, zen):
        self.store, self.zen = store, zen

    def create(self, *, request_id, title, prompt, working_project):
        try:
            task, created = self.store.create_web_worker(request_id, title, prompt)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not created and canonical_chat_url(task.get("url", "")):
            return {"ok": True, "result": "existing", "task": task}
        launched = self.zen.worker_create(task["id"], prompt)
        url = str(launched.get("href") or "")
        if not launched.get("ok") or not canonical_chat_url(url):
            task = self.store.update(task["id"], status="blocked", phase="creation-failed",
                                     summary=str(launched.get("result") or "canonical conversation was not verified"))
            return {"ok": False, "error": "canonical conversation was not verified", "task": task}
        projects = launched.get("projects") or []
        match = next((p for p in projects if p.get("name") == working_project), None)
        if not match:
            task = self.store.update(task["id"], url=url, status="blocked", phase="project-not-found",
                                     summary=f"ChatGPT project not found: {working_project}")
            return {"ok": False, "error": "working project not found", "projects": projects, "task": task}
        moved = self.zen.worker_move_project(task["id"], str(match.get("id") or ""), working_project)
        if not moved.get("ok") or moved.get("projectName") != working_project:
            task = self.store.update(task["id"], url=url, status="blocked", phase="working-move-failed",
                                     summary="Working project move could not be verified")
            return {"ok": False, "error": "working project move could not be verified", "task": task}
        task = self.store.update(task["id"], url=url, kind="web-worker", status="working", phase="running",
                                 projectId=str(match.get("id") or ""), projectName=working_project)
        return {"ok": True, "result": "created", "task": task}

    def inspect(self, task_id):
        task = self.store.get(task_id)
        if not task or task.get("kind") != "web-worker":
            return {"ok": False, "error": "unknown web worker"}
        live = self.zen.worker_latest_response(task_id) if task.get("url") else {}
        task = self.observe(task_id, live) or task
        return {"ok": True, "task": task, "live": live}

    def observe(self, task_id, live):
        task = self.store.get(task_id)
        if (task and task.get("kind") == "web-worker" and task.get("phase") == "running"
                and live.get("ok") and not live.get("working")
                and int(live.get("assistantCount") or 0) > int(task.get("baselineAssistantCount") or 0)):
            return self.store.update(task_id, status="waiting", phase="awaiting-review",
                                     response=str(live.get("assistantText") or "")[:12000])
        return task

    def review(self, *, task_id, decision, reviewer, evidence, done_project):
        task = self.store.get(task_id)
        if not task or task.get("kind") != "web-worker":
            return {"ok": False, "error": "unknown web worker"}
        if task.get("phase") != "awaiting-review":
            return {"ok": False, "error": "worker is not awaiting review"}
        decision = str(decision or "").lower()
        if decision not in {"approved", "rejected"} or not str(reviewer or "").strip() or not str(evidence or "").strip():
            return {"ok": False, "error": "independent reviewer, decision, and evidence are required"}
        if decision == "rejected":
            task = self.store.update(task_id, status="blocked", phase="review-rejected",
                                     reviewDecision=decision, reviewer=reviewer, reviewEvidence=evidence)
            return {"ok": True, "result": "rejected", "task": task}
        moved = self.zen.worker_move_project(task_id, "", done_project)
        if not moved.get("ok") or moved.get("projectName") != done_project:
            return {"ok": False, "error": "Done project move could not be verified", "task": task}
        task = self.store.update(task_id, status="done", progress=1.0, phase="done",
                                 projectName=done_project, reviewDecision=decision,
                                 reviewer=reviewer, reviewEvidence=evidence, completedAt=time.time())
        return {"ok": True, "result": "approved", "task": task}
