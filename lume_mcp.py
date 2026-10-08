#!/usr/bin/env python3
"""The one Lume MCP server: Lume UI, whiteboard/display and Working tasks.

Transports (same tool registry):
  python3 lume_mcp.py stdio            local MCP clients (Claude Code, Codex, ...)
  python3 lume_mcp.py http [--port N]  Streamable HTTP for remote clients (ChatGPT)

Every tool maps to one request on Lume's local mode-0600 Unix socket
($XDG_RUNTIME_DIR/tabby.sock). There is deliberately no shell, file or browser
access here: the surface is Lume's display and Working cards only.

Stdlib only, so it starts fast and does not depend on an MCP SDK version.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tabby.ipc import send_command  # noqa: E402

SERVER_NAME = "lume"
SERVER_VERSION = "1.1.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
TOKEN_PATH = Path.home() / ".config/tabby/mcp-token"
DEFAULT_PORT = 8766
MAX_BODY = 256 * 1024
CHOICE_WAIT_MAX = 50.0
ALLOWED_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:115c:a1e0::/48")]

INSTRUCTIONS = (
    "Lume is the user's desktop companion on their Linux laptop. These tools draw on Lume's "
    "small on-screen board (text, progress bars, status lines, cards, lists, shapes and "
    "clickable choices) and manage Working task cards that stay pinned on screen. Use "
    "lume_display for anything composite; give items stable ids so later calls update them "
    "in place instead of piling up. Keep text short: the board is about 340px wide."
)

ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "One board item. type: text{title,text} | progress{label,value 0..1} | "
        "status{label,state:info|working|success|warning|error} | choice{label,options[<=6]} | "
        "card{title,body,badge,progress?} | list{title,entries[],ordered} | "
        "shape{kind:line|arrow|rect|circle,x,y,w,h,label,filled} (coordinates 0..1 of the drawing area) | "
        "divider{label}. Any other lowercase type is kept and shown with its title/text. "
        "Optional tone: neutral|primary|secondary|tertiary|success|warning|error. "
        "Reuse an id to update that item; on update only the sent fields change."
    ),
    "properties": {"type": {"type": "string"}, "id": {"type": "string"}},
    "additionalProperties": True,
}

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
UI_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class ToolError(Exception):
    pass


def _ipc(payload: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    result = send_command(payload, timeout=timeout)
    if not result.get("ok"):
        error = str(result.get("error") or result.get("result") or "Lume rejected the request")
        if "No such file" in error or "Connection refused" in error:
            error = "Lume is not running (its Quickshell plugin backend is down)"
        raise ToolError(error)
    result.pop("state", None)  # full UI state snapshot is noise for the model
    return result


def _opt(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: args[k] for k in keys if args.get(k) not in (None, "")}


def _display(items: list[dict[str, Any]], mode: str = "append") -> dict[str, Any]:
    return _ipc({"command": "display", "items": items, "mode": mode})


def _wait_choice(item_id: str, wait: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, min(CHOICE_WAIT_MAX, float(wait or 0)))
    while True:
        snap = _ipc({"command": "ui-state"})
        hit = next((i for i in snap.get("items", []) if i.get("id") == item_id and i.get("type") == "choice"), None)
        if hit is None:
            return {"ok": True, "item_id": item_id, "status": "gone", "selected": ""}
        if hit.get("selected"):
            return {"ok": True, "item_id": item_id, "status": "answered", "selected": hit["selected"]}
        if time.monotonic() >= deadline:
            return {"ok": True, "item_id": item_id, "status": "pending", "selected": "",
                    "hint": "Call lume_get_choice later to read the answer."}
        time.sleep(0.15)


def t_status(a):
    snap = _ipc({"command": "ui-state"})
    tasks = _ipc({"command": "work-list"}).get("tasks", [])
    return {"ok": True, "summoned": snap.get("summoned"), "face": snap.get("face"),
            "boardVisible": snap.get("whiteboardVisible"), "items": snap.get("items", []),
            "tasks": [_task_view(t) for t in tasks]}


def _task_view(task: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "kind", "title", "status", "progress", "summary", "url")
    return {k: task.get(k) for k in keys if task.get(k) not in (None, "")}


def _task_result(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("task"), dict):
        result["task"] = _task_view(result["task"])
    if isinstance(result.get("tasks"), list):
        result["tasks"] = [_task_view(t) for t in result["tasks"]]
    return result


def t_choice(a):
    item = {"type": "choice", "id": a.get("id") or f"choice-{secrets.token_hex(4)}",
            "label": a.get("label", ""), "options": a.get("options") or []}
    shown = _display([item])["items"][0]
    if float(a.get("wait_seconds") or 0) <= 0:
        return {"ok": True, "item_id": shown["id"], "status": "pending", "selected": ""}
    return _wait_choice(shown["id"], float(a["wait_seconds"]))


Tool = tuple[str, str, dict[str, Any], dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


S = {"type": "string"}
N01 = {"type": "number", "minimum": 0, "maximum": 1}
ID = {"type": "string", "description": "Stable id; reuse it to update this item in place."}
TASK_ID = {"type": "string", "description": "Working task id from lume_task_list / lume_task_create."}
STATUS = {"type": "string", "enum": ["working", "waiting", "blocked", "done"]}

TOOLS: list[Tool] = [
    ("lume_status", "Read Lume's current board items, face state and Working tasks.",
     _schema({}), READ_ONLY, t_status),
    ("lume_show", "Show Lume's board.", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "show"})),
    ("lume_hide", "Hide Lume's board (items are kept).", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "hide"})),
    ("lume_clear", "Remove every item from Lume's board and hide it.", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "clear"})),
    ("lume_write", "Write a text block on Lume's board.",
     _schema({"text": S, "title": S, "id": ID}, ["text"]), UI_WRITE,
     lambda a: _display([{"type": "text", **_opt(a, "text", "title", "id")}])),
    ("lume_progress", "Show or update a progress bar (value 0..1). Reuse id to move the same bar.",
     _schema({"value": N01, "label": S, "id": ID}, ["value"]), UI_WRITE,
     lambda a: _display([{"type": "progress", "value": a.get("value", 0), **_opt(a, "label", "id")}])),
    ("lume_status_line", "Show or update a one-line status with an icon state.",
     _schema({"label": S, "state": {"type": "string", "enum": ["info", "working", "success", "warning", "error"]}, "id": ID},
             ["label"]), UI_WRITE,
     lambda a: _display([{"type": "status", **_opt(a, "label", "state", "id")}])),
    ("lume_set_face", "Set Lume's face/animation state.",
     _schema({"state": {"type": "string", "enum": ["idle", "listening", "thinking", "tool", "speaking", "approval", "success", "error"]}},
             ["state"]), UI_WRITE,
     lambda a: _ipc({"command": "state", "value": a.get("state", "idle")})),
    ("lume_choice", "Show clickable buttons for the user. With wait_seconds (max 50) the call waits for the click and returns it; otherwise read it later with lume_get_choice.",
     _schema({"label": S, "options": {"type": "array", "items": S, "minItems": 1, "maxItems": 6},
              "wait_seconds": {"type": "number", "minimum": 0, "maximum": CHOICE_WAIT_MAX}, "id": ID},
             ["label", "options"]), UI_WRITE, t_choice),
    ("lume_get_choice", "Read the user's answer to a choice; optionally wait up to wait_seconds for it.",
     _schema({"item_id": S, "wait_seconds": {"type": "number", "minimum": 0, "maximum": CHOICE_WAIT_MAX}}, ["item_id"]),
     READ_ONLY, lambda a: _wait_choice(str(a.get("item_id", "")), float(a.get("wait_seconds") or 0))),
    ("lume_shape", "Draw a basic shape in the board's drawing area. x,y,w,h are fractions 0..1 of that area.",
     _schema({"kind": {"type": "string", "enum": ["line", "arrow", "rect", "circle"]},
              "x": N01, "y": N01, "w": {"type": "number"}, "h": {"type": "number"},
              "label": S, "filled": {"type": "boolean"}, "tone": S, "id": ID},
             ["kind", "x", "y", "w", "h"]), UI_WRITE,
     lambda a: _display([{"type": "shape", **_opt(a, "kind", "x", "y", "w", "h", "label", "filled", "tone", "id")}])),
    ("lume_card", "Show or update a card (title, body, optional badge and progress).",
     _schema({"title": S, "body": S, "badge": S, "progress": N01, "tone": S, "id": ID}, ["title"]), UI_WRITE,
     lambda a: _display([{"type": "card", **_opt(a, "title", "body", "badge", "progress", "tone", "id")}])),
    ("lume_display", "Generic renderer: add/update several board items at once. mode=replace clears the board first.",
     _schema({"items": {"type": "array", "items": ITEM_SCHEMA, "minItems": 1, "maxItems": 32},
              "mode": {"type": "string", "enum": ["append", "replace"]}}, ["items"]), UI_WRITE,
     lambda a: _display(a.get("items") or [], str(a.get("mode") or "append"))),
    ("lume_remove", "Remove board items by id.",
     _schema({"ids": {"type": "array", "items": S, "minItems": 1}}, ["ids"]), UI_WRITE,
     lambda a: _ipc({"command": "ui-remove", "ids": a.get("ids") or []})),
    ("lume_task_list", "List Working task cards.", _schema({}), READ_ONLY,
     lambda a: _task_result(_ipc({"command": "work-list"}))),
    ("lume_task_create", "Create a Working task card pinned on screen.",
     _schema({"title": S, "summary": S, "progress": N01, "status": STATUS}, ["title"]), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-create", **_opt(a, "title", "summary", "progress", "status")}))),
    ("lume_task_pin_current", "Pin Lume's current ChatGPT conversation as a Working task card (it completes itself when the reply finishes).",
     _schema({"title": S}), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-pin-current", "title": a.get("title", "")}, timeout=20))),
    ("lume_task_update", "Update a Working task's title, progress, status or summary.",
     _schema({"task_id": TASK_ID, "title": S, "progress": N01, "status": STATUS, "summary": S}, ["task_id"]), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-update", "task_id": a.get("task_id", ""),
                                  "title": a.get("title"), "progress": a.get("progress"),
                                  "status": a.get("status"), "summary": a.get("summary")}))),
    ("lume_task_done", "Mark a Working task done (it stays pinned until the user removes it).",
     _schema({"task_id": TASK_ID, "summary": S}, ["task_id"]), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-complete", "task_id": a.get("task_id", ""), "summary": a.get("summary", "")}))),
    ("lume_task_reopen", "Reopen a done Working task (status back to working).",
     _schema({"task_id": TASK_ID, "summary": S}, ["task_id"]), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-reopen", "task_id": a.get("task_id", ""), **_opt(a, "summary")}))),
    ("lume_task_open", "Open a conversation-backed Working task in Lume (optionally in Voice).",
     _schema({"task_id": TASK_ID, "voice": {"type": "boolean"}}, ["task_id"]), UI_WRITE,
     lambda a: _ipc({"command": "work-voice" if a.get("voice") else "work-open", "task_id": a.get("task_id", "")})),
    ("lume_wake", "Summon Lume on screen (starts its ChatGPT Voice session).", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "wake"}, timeout=10)),
    ("lume_close", "Close Lume (ends Voice and clears the board).", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "close"})),
]
TOOL_INDEX = {t[0]: t for t in TOOLS}
# Incoming calls from already connected Tabby clients remain supported.
TOOL_INDEX.update({"tabby_" + n[5:]: tool for n, tool in list(TOOL_INDEX.items()) if n.startswith("lume_")})


def assistant_name() -> str:
    try:
        data = json.loads((Path.home()/".config/tabby/config.json").read_text())
        return str(data.get("assistant_name") or "Lume").strip()[:60] or "Lume"
    except Exception:
        return "Lume"


def tool_list() -> list[dict[str, Any]]:
    return [{"name": n, "description": d.replace("Lume", assistant_name()), "inputSchema": s, "annotations": ann} for n, d, s, ann, _ in TOOLS]


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    tool = TOOL_INDEX.get(name)
    if not tool:
        raise KeyError(name)
    try:
        result = tool[4](args if isinstance(args, dict) else {})
        is_error = False
    except ToolError as error:
        result, is_error = {"ok": False, "error": str(error)}, True
    except (TypeError, ValueError) as error:
        result, is_error = {"ok": False, "error": f"invalid arguments: {error}"}, True
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": text}], "structuredContent": result, "isError": is_error}


def handle_rpc(message: Any) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; returns the response, or None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    mid, method, params = message.get("id"), message.get("method"), message.get("params") or {}
    if "id" not in message:
        return None  # notification (initialized, cancelled, ...)

    def ok(result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        return ok({"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVER_NAME, "title": assistant_name(), "version": SERVER_VERSION},
                   "instructions": INSTRUCTIONS.replace("Lume", assistant_name())})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_list()})
    if method == "tools/call":
        try:
            return ok(call_tool(str(params.get("name") or ""), params.get("arguments") or {}))
        except KeyError:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {params.get('name')}"}}
    if method in {"resources/list", "prompts/list"}:
        return ok({method.split("/")[0]: []})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def handle_payload(payload: Any) -> Any:
    if isinstance(payload, list):
        out = [r for r in (handle_rpc(m) for m in payload) if r is not None]
        return out or None
    return handle_rpc(payload)


# ---------------------------------------------------------------- stdio

def serve_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            response = handle_payload(json.loads(line))
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# ---------------------------------------------------------------- http

def load_token(path: Path = TOKEN_PATH) -> str:
    try:
        token = path.read_text().strip()
    except FileNotFoundError:
        token = ""
    if len(token) < 32:
        token = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(token + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    return token


class Handler(BaseHTTPRequestHandler):
    server_version = "lume-mcp"
    disable_nagle_algorithm = True
    sys_version = ""
    protocol_version = "HTTP/1.1"
    token = ""
    rpc_note = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # token-free, one line per request
        path = self.path.split("?")[0]
        if self.token:
            path = path.replace(self.token, "<token>")
        sys.stderr.write(f"{self.client_address[0]} {self.command} {path} {args[1] if len(args) > 1 else ''} {self.rpc_note}\n")

    def _source_allowed(self) -> bool:
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return any(address in net for net in ALLOWED_NETS)

    def _authorized(self) -> bool:
        path = self.path.split("?")[0].rstrip("/")
        if path.startswith("/mcp/") and hmac.compare_digest(path[5:].encode(), self.token.encode()):
            return True
        if path == "/mcp":
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer ") and hmac.compare_digest(header[7:].strip().encode(), self.token.encode()):
                return True
        return False

    def _send(self, code: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if code >= 400:
            # Never reuse a connection after a rejection: an unread request
            # body would otherwise be parsed as the next request (the reverse
            # proxy keeps backend connections alive).
            self.send_header("Connection", "close")
            self.close_connection = True
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        # Headers and body in one segment: with Nagle, a separate body write
        # waits a full round trip for the header ACK (costly over Wi-Fi/Tailscale).
        self._headers_buffer.append(b"\r\n")
        self._headers_buffer.append(data)
        self.flush_headers()

    def _gate(self) -> bool:
        if not self._source_allowed():
            self._send(403, {"error": "forbidden"})
            return False
        if not self._authorized():
            self._send(404, {"error": "not found"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(200, {"ok": True})
            return
        if self._gate():
            # Stateless server: no server-initiated SSE stream.
            self._send(405, {"error": "use POST"}, {"Allow": "POST"})

    def do_DELETE(self) -> None:  # noqa: N802
        if self._gate():
            self._send(405, {"error": "stateless server"}, {"Allow": "POST"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY:
            self._send(413 if length > MAX_BODY else 400, {"error": "bad body"})
            return
        raw = self.rfile.read(length)  # always drain before answering
        if not self._gate():
            return
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return
        calls = payload if isinstance(payload, list) else [payload]
        self.rpc_note = ",".join(
            str(m.get("method")) + (":" + str((m.get("params") or {}).get("name")) if m.get("method") == "tools/call" else "")
            for m in calls if isinstance(m, dict))[:200]
        response = handle_payload(payload)
        if response is None:
            self._send(202)
        else:
            self._send(200, response)


def serve_http(host: str, port: int) -> None:
    Handler.token = load_token()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    sys.stderr.write(f"lume-mcp listening on {host}:{port} (token in {TOKEN_PATH})\n")
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", choices=["stdio", "http", "tools"], default="stdio")
    parser.add_argument("--host", default=os.environ.get("TABBY_MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TABBY_MCP_PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    if args.mode == "tools":
        print(json.dumps([t["name"] for t in tool_list()]))
    elif args.mode == "http":
        serve_http(args.host, args.port)
    else:
        serve_stdio()


if __name__ == "__main__":
    main()
