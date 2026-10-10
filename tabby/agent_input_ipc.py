"""Client side of the Loom agent-input socket (shared by MCP, CLI and AI Workspaces)."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

MAX_REQUEST = 64 * 1024
MAX_REPLY = 4 * 1024 * 1024


def socket_path() -> Path:
    override = os.environ.get("LOOM_AGENT_INPUT_SOCKET")
    if override:
        return Path(override)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "loom-agent-input.sock"


def request(payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    data = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
    if len(data) > MAX_REQUEST:
        return {"ok": False, "code": "invalid", "error": "request too large"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(socket_path()))
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
            chunks, size = [], 0
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_REPLY:
                    return {"ok": False, "code": "failed", "error": "reply too large"}
                chunks.append(chunk)
    except (FileNotFoundError, ConnectionRefusedError):
        return {"ok": False, "code": "unavailable",
                "error": "Loom agent-input service is not running (systemctl --user start loom-agent-input)"}
    except socket.timeout:
        return {"ok": False, "code": "timeout", "error": "Loom agent-input service did not answer in time"}
    except OSError as exc:
        return {"ok": False, "code": "unavailable", "error": str(exc)}
    try:
        reply = json.loads(b"".join(chunks) or b"{}")
    except ValueError:
        return {"ok": False, "code": "failed", "error": "invalid reply from Loom agent-input service"}
    return reply if isinstance(reply, dict) else {"ok": False, "code": "failed", "error": "invalid reply"}
