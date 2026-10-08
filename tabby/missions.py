"""Narrow, authenticated SSH transport for the existing Philipedia LOOM ledger.

No arbitrary shell, no arbitrary host/command parameters, and no shell interpolation
of model-supplied strings. The existing bridge verifies task scope and outcome.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import subprocess
from pathlib import Path

HOST = "philipedia"
BRIDGE_COMMAND = "cd /home/qwqc/loom && node lib/docklys_bridge.js"
SSH_CONFIG = str(Path.home() / ".ssh/config")
ALLOWED = {"status", "health", "create", "activity", "resume", "decisions", "capture", "inbox", "dispatch"}


class MissionBridgeError(RuntimeError):
    pass


def request(payload, *, runner=subprocess.run):
    if not isinstance(payload, dict) or payload.get("action") not in ALLOWED:
        raise MissionBridgeError("unsupported LOOM bridge action")
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    if len(encoded) > 24_000:
        raise MissionBridgeError("request too large")
    cmd = ["ssh", "-F", SSH_CONFIG, "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=yes",
           "-T", HOST, BRIDGE_COMMAND]
    try:
        proc = runner(cmd, input=encoded + "\n", text=True, capture_output=True, timeout=18)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MissionBridgeError("Philipedia connection failed: " + type(exc).__name__) from exc
    if not proc.stdout or len(proc.stdout) > 1_000_000:
        raise MissionBridgeError("Philipedia returned no usable bridge response")
    try:
        obj = json.loads(proc.stdout.strip())
    except ValueError as exc:
        raise MissionBridgeError("Philipedia returned invalid JSON") from exc
    if not isinstance(obj, dict):
        raise MissionBridgeError("Philipedia returned an invalid object")
    if not obj.get("ok"):
        raise MissionBridgeError(str(obj.get("error") or "LOOM rejected request")[:500])
    if proc.returncode != 0:
        raise MissionBridgeError("Philipedia bridge failed (exit %s)" % proc.returncode)
    return obj


def mission_list():
    return request({"action": "status"})


def mission_health():
    return request({"action": "health"})


def idea_list():
    return request({"action": "inbox"})


def idea_capture(ideas, request_id=None):
    if not isinstance(ideas, list) or not 1 <= len(ideas) <= 32:
        raise MissionBridgeError("ideas must be a list of 1 to 32 objects")
    key = request_id or "loom-" + secrets.token_hex(12)
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise MissionBridgeError("invalid request_id")
    return request({"action": "capture", "request_id": key, "ideas": ideas})



def idea_dispatch(*, idea_id, mission_id):
    """Link an existing captured idea to a verified-scope existing mission.

    Does NOT create/execute a worker; Philipedia enforces repo and top-level
    mission restrictions and rejects redirection of existing links.
    """
    if not isinstance(idea_id, str) or not re.fullmatch(r"[a-f0-9]{24}", idea_id):
        raise MissionBridgeError("idea_id must be a saved 24-character hexadecimal ID")
    if not isinstance(mission_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", mission_id):
        raise MissionBridgeError("invalid mission_id")
    return request({"action": "dispatch", "idea_id": idea_id, "mission_id": mission_id})


# Phil handoff service is an independent, narrowly exposed registrar.
# It does not expose a general SSH command execution surface to model callers.
HANDOFF_REGISTER_COMMAND = "/usr/bin/python3 /home/qwqc/Projects/Loom-Handoff/remote_register.py"
HANDOFF_STATUS_COMMAND = "/usr/bin/python3 /home/qwqc/Projects/Loom-Handoff/handoff.py status"


def _handoff_ssh(command, request_obj=None, *, runner=subprocess.run):
    cmd = ["ssh", "-F", SSH_CONFIG, "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=yes",
           "-T", HOST, command]
    try:
        proc = runner(cmd, input=(json.dumps(request_obj) if request_obj is not None else ""),
                      text=True, capture_output=True, timeout=18)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MissionBridgeError("handoff service unavailable: " + type(exc).__name__) from exc
    try:
        result = json.loads((proc.stdout or "").strip())
    except (TypeError, ValueError) as exc:
        raise MissionBridgeError("handoff service returned invalid JSON") from exc
    if proc.returncode or not isinstance(result, dict) or result.get("ok") is False:
        raise MissionBridgeError(str(result.get("error") or "handoff service rejected request")[:300])
    return result


def handoff_register(*, mission_id, origin_ref, origin_url=None,
                     origin_source="loom", auto_continuation=True):
    if not isinstance(mission_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", mission_id):
        raise MissionBridgeError("invalid mission id for origin registration")
    if not isinstance(origin_ref, str) or not 1 <= len(origin_ref) <= 200:
        raise MissionBridgeError("origin_ref must be a nonempty stable identifier <=200 chars")
    if origin_source not in ("loom", "chatgpt", "api", "codex", "claude"):
        raise MissionBridgeError("unsupported origin source")
    if not isinstance(auto_continuation, bool):
        raise MissionBridgeError("auto_continuation must be boolean")
    return _handoff_ssh(HANDOFF_REGISTER_COMMAND,
             {"mission": mission_id, "source": origin_source,
              "origin_ref": origin_ref, "origin_url": origin_url,
              "continuation": "prepare_integration" if auto_continuation else "notification"})


def handoff_status():
    return _handoff_ssh(HANDOFF_STATUS_COMMAND)


def mission_create(*, goal, repo, agent="codex", title=None, budget_minutes=30,
                   origin_ref=None, origin_url=None, origin_source="loom",
                   auto_continuation=True):
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 12_000:
        raise MissionBridgeError("goal is required and must be at most 12,000 chars")
    if not isinstance(repo, str) or not repo.startswith("/home/qwqc/") or len(repo) > 500 or ".." in Path(repo).parts:
        raise MissionBridgeError("repo must be an absolute user project path on Philipedia")
    if agent not in ("codex", "clawd"):
        raise MissionBridgeError("agent must be codex or clawd")
    if type(budget_minutes) not in (int, float) or not 5 <= budget_minutes <= 90:
        raise MissionBridgeError("budget_minutes must be 5 to 90")
    if not mission_health().get("sandbox_available"):
        raise MissionBridgeError("Philipedia isolated executor is unavailable; use loom_idea_capture to save the idea safely")
    payload = {"action": "create", "goal": goal, "repo": repo, "agent": agent,
               "budgetMin": int(budget_minutes)}
    if title is not None:
        if not isinstance(title, str) or len(title) > 160:
            raise MissionBridgeError("title exceeds 160 chars")
        payload["title"] = title
    created = request(payload)
    task_id = (created.get("task") or {}).get("id")
    if task_id:
        try:
            created["handoff"] = handoff_register(
                mission_id=task_id, origin_ref=origin_ref or ("loom-mcp:" + task_id),
                origin_url=origin_url, origin_source=origin_source,
                auto_continuation=auto_continuation)
        except MissionBridgeError as exc:
            # The mission exists and might already be running! Report the
            # detached registration failure without disguising creation success.
            created["handoff_error"] = str(exc)
    return created


def mission_activity(mission_id):
    if not isinstance(mission_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", mission_id):
        raise MissionBridgeError("invalid mission ID")
    return request({"action": "activity", "id": mission_id})


def mission_resume(mission_id):
    if not isinstance(mission_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", mission_id):
        raise MissionBridgeError("invalid mission ID")
    if not mission_health().get("sandbox_available"):
        raise MissionBridgeError("Philipedia isolated executor is unavailable; mission remains saved for later recovery")
    return request({"action": "resume", "id": mission_id})

