"""Optional, authenticated adapter from Philipedia to Loom's Mirai desktop.

The destination, MCP method and accepted backend commands are fixed.  Callers
cannot turn this into an HTTP or shell proxy.
"""
from __future__ import annotations

import json
import socket
import http.client
import stat
import urllib.error
import urllib.request
from pathlib import Path

MIRAI_MCP_URL = "http://100.100.120.58:8766/mcp"
MIRAI_TOKEN_PATH = Path.home() / ".config/tabby/mirai-mcp-token"
TACHIBANA_MCP_URL = "http://100.77.60.92:8766/mcp"
TACHIBANA_TOKEN_PATH = Path.home() / ".config/tabby/tachibana-mcp-token"
DESKTOP_ENDPOINTS = {
    "mirai": (MIRAI_MCP_URL, MIRAI_TOKEN_PATH),
    "tachibana": (TACHIBANA_MCP_URL, TACHIBANA_TOKEN_PATH),
}
DESKTOP_COMMANDS = frozenset({
    "clear", "close", "display", "hide", "show", "state", "ui-remove", "ui-state", "wake",
    "work-complete", "work-create", "work-list", "work-open", "work-pin-current", "work-reopen",
    "work-update", "work-voice", "web-worker-create", "web-worker-inspect", "web-worker-review",
})
COMMAND_TO_TOOL = {
    "clear": "loom_clear", "close": "loom_close", "display": "loom_display", "hide": "loom_hide",
    "show": "loom_show", "state": "loom_set_face", "ui-remove": "loom_remove", "ui-state": "loom_status",
    "wake": "loom_wake", "work-complete": "loom_task_done", "work-create": "loom_task_create",
    "work-list": "loom_task_list", "work-open": "loom_task_open", "work-pin-current": "loom_task_pin_current",
    "work-reopen": "loom_task_reopen", "work-update": "loom_task_update", "work-voice": "loom_task_open",
    "web-worker-create": "loom_web_worker_create", "web-worker-inspect": "loom_web_worker_inspect",
    "web-worker-review": "loom_web_worker_review",
}


class DesktopUnavailable(RuntimeError):
    pass


class DesktopOutcomeUnknown(DesktopUnavailable):
    """The authenticated request may have acted before its response disappeared."""
    pass


def _token(token_path: Path) -> str:
    try:
        info = token_path.stat()
        token = token_path.read_text().strip()
    except OSError as exc:
        raise DesktopUnavailable("Mirai desktop adapter is not configured") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise DesktopUnavailable("Mirai desktop adapter token must be a private regular file")
    if len(token) < 32:
        raise DesktopUnavailable("Mirai desktop adapter authentication is not configured")
    return token


def configured(token_path: Path = MIRAI_TOKEN_PATH) -> bool:
    try:
        _token(token_path)
        return True
    except DesktopUnavailable:
        return False


def call(payload: dict, timeout: float = 3.0, *, opener=urllib.request.urlopen,
         token_path: Path = MIRAI_TOKEN_PATH) -> dict:
    command = payload.get("command") if isinstance(payload, dict) else None
    if command not in DESKTOP_COMMANDS:
        raise DesktopUnavailable("desktop adapter rejected a non-allowlisted command")
    token = _token(token_path)
    arguments = {key: value for key, value in payload.items() if key != "command" and value is not None}
    if command == "state":
        arguments = {"state": arguments.pop("value", "idle")}
    elif command == "ui-remove":
        arguments = {"ids": arguments.get("ids", [])}
    elif command in {"work-open", "work-voice"}:
        arguments = {"task_id": arguments.get("task_id", ""), "voice": command == "work-voice"}
    body = json.dumps({"jsonrpc": "2.0", "id": "philipedia-adapter", "method": "tools/call",
                       "params": {"name": COMMAND_TO_TOOL[command], "arguments": arguments}},
                      separators=(",", ":")).encode()
    request = urllib.request.Request(MIRAI_MCP_URL, data=body, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with opener(request, timeout=min(max(float(timeout), .2), 45.0)) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("oversized response")
            result = json.loads(raw)
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        raise DesktopUnavailable("Mirai desktop is unavailable") from exc
    if not isinstance(result, dict) or "error" in result:
        raise DesktopUnavailable("Mirai desktop adapter returned an invalid response")
    tool_result = result.get("result") or {}
    structured = tool_result.get("structuredContent")
    if not isinstance(structured, dict):
        raise DesktopUnavailable("Mirai desktop adapter returned no structured result")
    if tool_result.get("isError") or structured.get("ok") is False:
        raise DesktopUnavailable(str(structured.get("error") or "Mirai desktop rejected the request")[:300])
    return structured


def call_named_tool(name: str, arguments: dict, *, timeout: float = 15.0,
                    opener=urllib.request.urlopen, token_path: Path | None = None,
                    device: str = "mirai") -> dict:
    """Only forward a catalogued Loom tool to the fixed Mirai endpoint.

    Every desktop tool keeps its existing input shape, including GUI and
    notification tools. A caller cannot specify a destination or MCP method.
    """
    if not isinstance(name, str) or not name.startswith("loom_"):
        raise DesktopUnavailable("not a Loom tool")
    if not isinstance(arguments, dict):
        raise DesktopUnavailable("invalid Loom tool arguments")
    if device not in DESKTOP_ENDPOINTS:
        raise DesktopUnavailable("device is not an allowlisted desktop")
    url, configured_token_path = DESKTOP_ENDPOINTS[device]
    token = _token(token_path if token_path is not None else configured_token_path)
    body = json.dumps({
        "jsonrpc": "2.0", "id": "philipedia-forward", "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }, separators=(",", ":")).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with opener(request, timeout=min(max(float(timeout), .2), 45.0)) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("oversized desktop response")
            reply = json.loads(raw)
    except ConnectionRefusedError as exc:
        raise DesktopUnavailable(f"{device} desktop refused connection before sending") from exc
    except urllib.error.HTTPError as exc:
        raise DesktopUnavailable(f"{device} desktop rejected HTTP request") from exc
    except (TimeoutError, socket.timeout, ConnectionResetError, BrokenPipeError,
            http.client.RemoteDisconnected) as exc:
        raise DesktopOutcomeUnknown(f"{device} response lost after possible dispatch") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            raise DesktopUnavailable(f"{device} desktop refused connection") from exc
        raise DesktopOutcomeUnknown(f"{device} connection outcome uncertain") from exc
    except (OSError, ValueError) as exc:
        raise DesktopOutcomeUnknown(f"{device} response could not be verified") from exc
    if not isinstance(reply, dict) or reply.get("error"):
        raise DesktopUnavailable("Mirai desktop returned an invalid MCP response")
    result = reply.get("result")
    if not isinstance(result, dict):
        raise DesktopUnavailable("Mirai desktop returned no MCP result")
    return result


def health(*, timeout: float = 1.0) -> dict:
    if not configured():
        return {"configured": False, "available": False, "reason": "authentication token not configured"}
    try:
        call({"command": "ui-state"}, timeout=timeout)
        return {"configured": True, "available": True}
    except DesktopUnavailable as exc:
        return {"configured": True, "available": False, "reason": str(exc)}
