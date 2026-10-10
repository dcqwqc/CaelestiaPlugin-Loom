#!/usr/bin/env python3
"""Loom Agent Input / Workspace Service.

    python3 loom_agent_input.py serve              run the service (systemd: loom-agent-input.service)
    python3 loom_agent_input.py ctl <cmd> [json]   send one request, print the reply
    python3 loom_agent_input.py list               list graphical workspaces

Runs separately from Loom's Voice backend on purpose: deploying or restarting
it never touches an active Voice session. See docs/AGENT_INPUT.md.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tabby.agent_input_ipc import MAX_REQUEST, request, socket_path  # noqa: E402
from tabby.agent_workspaces import AgentWorkspaceService, GuiError  # noqa: E402

VERSION = "1.0.0"

# command -> (service method, keyword arguments it accepts)
AGENT_COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "acquire": ("acquire", ("agent_id", "request_key", "kind", "task_id", "ai_session", "label", "size", "url",
                            "owner_pid", "agent_name")),
    "attach": ("attach", ("workspace_id", "agent_id", "agent_name")),
    "grant": ("grant", ("workspace_id", "agent_id", "token", "grantee")),
    "take-control": ("take_control", ("workspace_id", "agent_id", "token")),
    "release": ("release", ("workspace_id", "agent_id", "token", "keep_profile")),
    "cancel": ("cancel", ("workspace_id", "agent_id", "token")),
    "move": ("move_pointer", ("workspace_id", "agent_id", "token", "x", "y", "duration_ms", "action_id")),
    "click": ("click", ("workspace_id", "agent_id", "token", "x", "y", "selector", "text", "button", "count",
                        "action_id")),
    "scroll": ("scroll", ("workspace_id", "agent_id", "token", "dx", "dy", "x", "y", "selector", "action_id")),
    "type": ("type_text", ("workspace_id", "agent_id", "token", "text", "selector", "action_id")),
    "key": ("key", ("workspace_id", "agent_id", "token", "keys", "action_id")),
    "launch": ("launch", ("workspace_id", "agent_id", "token", "argv", "action_id")),
    "navigate": ("navigate", ("workspace_id", "agent_id", "token", "url", "action_id")),
    "screenshot": ("screenshot", ("workspace_id", "agent_id", "token", "max_width")),
    "state": ("interaction_state", ("workspace_id", "agent_id", "token", "include_text", "selector")),
    "list": ("list", ("agent_id", "include_released")),
    "audit": ("audit_tail", ("workspace_id", "limit")),
}


VIEWER_SPECIAL = "special:loom-agents"


def viewer_open() -> bool:
    import subprocess
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True, timeout=1.5).stdout
        return any((m.get("specialWorkspace") or {}).get("name") == VIEWER_SPECIAL for m in json.loads(out or "[]"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


class Server:
    def __init__(self, service: AgentWorkspaceService):
        self.service = service
        self.path = socket_path()
        self._stop = threading.Event()

    def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        cmd = str(req.get("command", "")).strip().lower()
        s = self.service
        try:
            if cmd in {"ping", "status"}:
                return {"ok": True, "version": VERSION, "pid": os.getpid(),
                        "workspaces": len([w for w in s.workspaces.values() if w["state"] in {"ready", "paused"}])}
            if cmd == "capabilities":
                return s.capabilities()
            if cmd == "appearance":
                return s.set_appearance(str(req.get("agent_id", "")), str(req.get("color", "")),
                                        str(req.get("name", "")))
            if cmd == "pause":
                return s.set_paused(workspace_id=req.get("workspace_id", ""), paused=True, actor="agent",
                                    agent_id=req.get("agent_id", ""), token=req.get("token", ""))
            if cmd == "resume":
                return s.set_paused(workspace_id=req.get("workspace_id", ""), paused=False, actor="agent",
                                    agent_id=req.get("agent_id", ""), token=req.get("token", ""))
            # User controls (overlay controls panel, CLI). No token: the socket is
            # mode 0600 and every call is audit-logged with actor=user.
            if cmd in {"user-pause", "user-resume"}:
                return s.set_paused(workspace_id=req.get("workspace_id", ""), paused=cmd == "user-pause")
            if cmd in {"user-pause-all", "user-resume-all"}:
                done = []
                for ws in list(s.workspaces.values()):
                    if ws["state"] in {"ready", "paused"}:
                        s.set_paused(workspace_id=ws["id"], paused=cmd == "user-pause-all")
                        done.append(ws["id"])
                return {"ok": True, "workspaces": done}
            if cmd == "user-terminate":
                return s.release(workspace_id=req.get("workspace_id", ""), actor="user", reason="terminated by user")
            if cmd in {"user-hide-agent", "user-show-agent"}:
                return s.user_hide_agent(str(req.get("agent_id", "")), cmd == "user-hide-agent")
            if cmd == "user-inspect":
                ws = s.workspaces.get(str(req.get("workspace_id", "")))
                if not ws:
                    raise GuiError("not_found", "unknown workspace")
                return {"ok": True, "workspace": s._public(ws),
                        "audit": s.audit_tail(ws["id"], 20)["entries"]}
            if cmd == "release-owner":  # AI Workspaces launcher: the agent session ended
                return s.release_owner(ai_session=str(req.get("ai_session", "")),
                                       agent_id=str(req.get("agent_id", "")))
            if cmd in AGENT_COMMANDS:
                method, keys = AGENT_COMMANDS[cmd]
                kwargs = {k: req[k] for k in keys if k in req and req[k] is not None}
                return getattr(s, method)(**kwargs)
            return {"ok": False, "code": "invalid", "error": f"unsupported command {cmd!r}"}
        except GuiError as exc:
            return exc.as_dict()
        except TypeError as exc:
            return {"ok": False, "code": "invalid", "error": f"invalid arguments: {exc}"}
        except Exception as exc:  # never kill the service on one bad request
            return {"ok": False, "code": "failed", "error": f"{type(exc).__name__}: {exc}"}

    def _client(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(5)
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > MAX_REQUEST:
                        raise ValueError("request too large")
                req = json.loads(buf or b"{}")
                if not isinstance(req, dict):
                    raise ValueError("invalid request")
                conn.settimeout(None)
                reply = self.handle(req)
            except Exception as exc:
                reply = {"ok": False, "code": "invalid", "error": str(exc)}
            try:
                conn.sendall(json.dumps(reply, separators=(",", ":"), ensure_ascii=False).encode())
            except OSError:
                pass

    def serve(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            probe = request({"command": "ping"}, timeout=1)
            if probe.get("ok"):
                raise SystemExit("loom-agent-input is already running")
            self.path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            srv.bind(str(self.path))
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        srv.listen(32)
        srv.settimeout(0.5)
        print(json.dumps({"event": "reconcile", **self.service.reconcile()}), flush=True)
        threading.Thread(target=self._monitor, name="loom-agent-input-monitor", daemon=True).start()
        threading.Thread(target=self._live, name="loom-agent-input-live", daemon=True).start()
        try:
            while not self._stop.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._client, args=(conn,), daemon=True).start()
        finally:
            srv.close()
            try:
                self.path.unlink()
            except OSError:
                pass
            self.service.shutdown()

    def _monitor(self) -> None:
        while not self._stop.is_set():
            try:
                self.service.tick()
            except Exception as exc:
                print(json.dumps({"event": "tick-error", "error": str(exc)}), flush=True)
            self._stop.wait(1.0)

    def _live(self) -> None:
        """Stream frames only while the user has special:loom-agents open."""
        last_check = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_check > 1.0:
                last_check = now
                was, self.service.live = self.service.live, viewer_open()
                if was != self.service.live:
                    self.service.write_overlay(force=True)
            if self.service.live:
                try:
                    self.service.live_frame_pass()
                except Exception as exc:
                    print(json.dumps({"event": "live-error", "error": str(exc)}), flush=True)
                self._stop.wait(0.06)   # up to ~12 fps; capture+encode takes the rest
            else:
                self._stop.wait(0.5)

    def stop(self, *_: Any) -> None:
        self._stop.set()


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    if argv[0] == "serve":
        server = Server(AgentWorkspaceService())
        signal.signal(signal.SIGTERM, server.stop)
        signal.signal(signal.SIGINT, server.stop)
        server.serve()
        return 0
    if argv[0] == "list":
        reply = request({"command": "list"})
        if not reply.get("ok"):
            print(reply.get("error"), file=sys.stderr)
            return 1
        for w in reply["workspaces"]:
            print(f"{w['id']}  {w['state']:8} {w['kind']:7} {w.get('display', '-'):6} owner={w['owner']} "
                  f"task={w.get('task_id') or '-'} label={w.get('label', '')}")
        return 0
    if argv[0] == "ctl" and len(argv) >= 2:
        payload = json.loads(argv[2]) if len(argv) > 2 else {}
        reply = request({"command": argv[1], **payload}, timeout=float(payload.get("timeout", 60)))
        print(json.dumps(reply, indent=2, ensure_ascii=False))
        return 0 if reply.get("ok") else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
