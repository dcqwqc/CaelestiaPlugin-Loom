"""Loom -> existing Philipedia Fleet Supervisor, not an agent or scheduler.

One authenticated Loom tool, one immutable Fleet job, one existing supervisor.
The host config (not speech/model text) controls repositories and acceptance.
"""
from __future__ import annotations

import base64
import json
import threading
from functools import wraps
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

ORIGIN = "loom-companion-20261021"
ACTOR = "loom-companion"
CONFIG_PATH = Path("/home/qwqc/.config/loom-fleet/config.json")
FLEET_ROOT = Path("/home/qwqc/Projects/ai-system-fleet-integrated")
BRIDGE_CMD = "python3 -B /home/qwqc/Projects/CaelestiaPlugin-Loom/tabby/orbit_delegate.py bridge"
REQUEST_ID = re.compile(r"[a-zA-Z0-9_:.-]{8,160}\Z")
ACTIONS = frozenset({"delegate", "status"})
_GATEWAY_LOCK = threading.RLock()


def _serialize_gateway(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        # FleetGateway temporarily swaps a module-level MCP schema table.
        # One process-level lock prevents competing threaded tool calls.
        with _GATEWAY_LOCK:
            return fn(*args, **kwargs)
    return wrapped



def _error(reason: str, *, state: str = "blocked") -> dict:
    return {"state": state, "reason": reason}


def _validate(action: str, args: dict) -> None:
    if action not in ACTIONS or not isinstance(args, dict):
        raise ValueError("unsupported Orbit action")
    key = args.get("request_id")
    if not isinstance(key, str) or not REQUEST_ID.fullmatch(key):
        raise ValueError("stable request_id of 8-160 safe characters required")
    if action == "delegate":
        if not isinstance(args.get("title"), str) or not 1 <= len(args["title"].strip()) <= 160:
            raise ValueError("invalid task title")
        if not isinstance(args.get("goal"), str) or not 1 <= len(args["goal"].strip()) <= 12000:
            raise ValueError("invalid task goal")
        if not isinstance(args.get("repository"), str) or not REQUEST_ID.fullmatch("repository:" + args["repository"]):
            raise ValueError("invalid configured repository")
        if args.get("provider", "codex") not in ("codex", "claude", "chatgpt-web"):
            raise ValueError("provider not permitted")
        if not isinstance(args.get("paths"), list) or not 1 <= len(args["paths"]) <= 20 or not all(isinstance(p, str) for p in args["paths"]):
            raise ValueError("explicit paths required")
        if (not isinstance(args.get("validation_profile"), str)
                or not 1 <= len(args["validation_profile"]) <= 80):
            raise ValueError("bounded validation_profile required")
        checks = args.get("checks")
        if (not isinstance(checks, list) or not 1 <= len(checks) <= 30
                or not all(isinstance(check, str) and 1 <= len(check) <= 160
                           for check in checks)):
            raise ValueError("bounded approved acceptance checks required")
        if any(len(path)>512 for path in args["paths"]):
            raise ValueError("path too long")


def _fleet(config_path: Path = CONFIG_PATH):
    for root in (str(FLEET_ROOT), str(FLEET_ROOT / "runtime")):
        if root not in sys.path:
            sys.path.insert(0, root)
    from runtime.fleet_mcp import FleetGateway
    from runtime.supervisor.store import SupervisorStore
    config = json.loads(config_path.read_text())
    if ORIGIN not in config.get("allowed_origins", []):
        raise ValueError("Loom is not admitted by existing Supervisor policy")
    return config, FleetGateway, SupervisorStore


def readonly_status(request_id: str, database: str) -> dict:
    """A real SQLite mode=ro status check: no schema initialization or writes."""
    path = Path(database)
    if not path.is_file():
        return {"state": "not_found", "request_id": request_id}
    db = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True, timeout=3)
    try:
        db.row_factory = sqlite3.Row
        row = db.execute("""SELECT o.id,o.state,f.revision,o.next_fence,f.result
                            FROM objectives o JOIN fleet_jobs f
                            ON f.objective_id=o.id
                            WHERE o.actor=? AND o.origin=? AND o.idempotency_key=?""",
                         (ACTOR, ORIGIN, request_id)).fetchone()
        if row is None:
            return {"state": "not_found", "request_id": request_id}
        receipt = json.loads(row["result"]) if row["result"] else {}
        verified = False
        if row["state"] == "done":
            accepted = db.execute("""SELECT 1 FROM attempts a
              JOIN engine_verifications v ON v.attempt_id=a.id
              JOIN reviews r ON r.attempt_id=a.id AND r.objective_id=a.objective_id
              WHERE a.objective_id=? AND a.fence=?
                AND a.status='succeeded_unverified' AND v.passed=1 AND r.passed=1
                AND v.reviewer=r.reviewer AND v.reviewer!=a.worker LIMIT 1""",
                (row["id"],row["next_fence"])).fetchone()
            verified = bool(accepted)
        return _projection({"id":row["id"],"state":row["state"],
                            "revision":row["revision"],
                            "result":dict(receipt, verified=verified)},request_id,True)
    finally:
        db.close()


@_serialize_gateway
def host_call(action: str, args: dict, *, config_path: Path = CONFIG_PATH) -> dict:
    """Only run on trusted Philipedia. No secondary worker or scheduler."""
    try:
        _validate(action, args)
        key = args["request_id"]
        if action == "status":
            config = json.loads(config_path.read_text())
            if ORIGIN not in config.get("allowed_origins", []):
                return _error("Loom origin not authorized")
            return readonly_status(key, config["database"])
        config, FleetGateway, SupervisorStore = _fleet(config_path)
        with SupervisorStore(config["database"]) as store:
            gw = FleetGateway(store, fleet_config=config, caller_id=ACTOR,
                              scopes=frozenset({"worker.read", "worker.write", "gateway.read"}),
                              allowed_origins=frozenset({ORIGIN}), allowed_targets=frozenset())
            row = store.db.execute(
                "SELECT id FROM objectives WHERE actor=? AND origin=? AND idempotency_key=?",
                (ACTOR, ORIGIN, key)).fetchone()
            def invoke(tool, params):
                result = gw.handle({"method": "tools/call", "params": {"name": tool, "arguments": params}})
                obj = json.loads(result["content"][0]["text"])
                if obj.get("ok") is not True:
                    error = obj.get("error", {})
                    raise ValueError(str(error.get("message") if isinstance(error, dict) else error)[:220])
                return obj["result"]
            # Config enforces all exact path/check/provider combinations.
            policy = config["repositories"].get(args["repository"])
            if policy is None:
                return _error("repository outside host allowlist")
            params = {"origin": ORIGIN, "idempotency_key": key,
                      "title": args["title"], "goal": args["goal"],
                      "provider": args.get("provider", "codex"),
                      "repository": args["repository"], "allowed_paths": args["paths"],
                      "acceptance_checks": args["checks"],
                      "validation_profile": args["validation_profile"]}
            if row is not None:
                # Fleet will enforce immutable specification equality before
                # treating this as reuse. Never blindly re-submit after an
                # uncertain send.
                job = invoke("worker_get", {"job_id": row["id"]})
                spec = job.get("specification") or {}
                if any(spec.get(k) != v for k, v in params.items() if k != "origin"):
                    return _error("request_id is already bound to a different objective")
                return _projection(job, key, True)
            created = invoke("worker_spawn", params)
            job = invoke("worker_get", {"job_id": created["job_id"]})
            return _projection(job, key, False)
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        # Do not retry a potentially accepted job from an ambiguous failure.
        return _error(f"{type(exc).__name__}: {str(exc)[:200]}", state="blocked")


def _projection(job: dict, request_id: str, reused: bool) -> dict:
    state = job.get("state")
    result = job.get("result")
    if not isinstance(result, dict):
        result = {}
    return {"state": state if state in ("queued","planning","working","review","blocked","done","failed","canceled") else "unknown",
            "job_id": job.get("id"), "request_id": request_id, "reused": reused,
            "verified_done": state == "done" and result.get("verified") is True,
            "continuing": state == "working",
            "revision": job.get("revision")}


def remote_call(action: str, args: dict, *, runner=subprocess.run) -> dict:
    try:
        _validate(action, args)
    except ValueError as exc:
        return _error(str(exc)[:200], state="denied")
    try:
        payload = base64.b64encode(json.dumps({"action": action, "arguments": args},
                                               separators=(",", ":"), ensure_ascii=False).encode()).decode("ascii")
        if len(payload) > 180_000:
            raise ValueError("bridge request exceeds bounded payload size")
        result = runner(["ssh", "-F", str(Path.home() / ".ssh/config"), "-o", "BatchMode=yes",
                         "-o", "ConnectTimeout=6", "-T", "philipedia", BRIDGE_CMD],
                        input=payload + "\n", text=True, capture_output=True, timeout=20)
        if result.returncode:
            try:
                prior = json.loads(result.stdout.strip())
                if isinstance(prior, dict) and prior.get("state") in (
                    "blocked", "not_found", "unknown", "device_unavailable", "uncertain_outcome"):
                    return prior
            except (ValueError, TypeError):
                pass
            return _error("Philipedia Orbit bridge unavailable", state="device_unavailable")
        data = json.loads(result.stdout.strip())
        if not isinstance(data, dict) or not isinstance(data.get("state"), str):
            raise ValueError("invalid Orbit bridge response")
        return data
    except subprocess.TimeoutExpired:
        return _error("Orbit request may already have been accepted; query same request_id",
                      state="uncertain_outcome")
    except (OSError, ValueError) as exc:
        return _error("Orbit transport unavailable: " + type(exc).__name__, state="device_unavailable")


def call(action: str, args: dict, *, local: bool) -> dict:
    return host_call(action, args) if local else remote_call(action, args)


def bridge_main() -> int:
    # Fixed executable endpoint, no arbitrary commands/hosts. Bounded JSON.
    raw = sys.stdin.readline(200000).strip()
    try:
        data = json.loads(base64.b64decode(raw, validate=True))
        result = host_call(data["action"], data["arguments"])
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps(_error("invalid request")))
        return 1


if __name__ == "__main__":
    if sys.argv[1:] != ["bridge"]:
        raise SystemExit(2)
    raise SystemExit(bridge_main())
