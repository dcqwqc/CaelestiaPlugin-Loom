"""loom_gui_* MCP tools: isolated graphical workspaces for AI agents.

The MCP process holds the per-workspace tokens in memory and derives the
agent's identity from its environment (AI Workspaces exports AI_SESSION_ID
and LOOM_TASK_ID), so an agent never handles tokens itself. If the MCP process
restarts, the owner is re-attached transparently.

Remote (HTTP) clients get no graphical tools unless the user enabled
``allow_remote_mcp`` in ~/.config/loom/agent-input.json (fail closed).
"""
from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from tabby.agent_input_ipc import request
from tabby.agent_workspaces import PALETTE, load_config

TRANSPORT = "stdio"  # set by loom_mcp.main()
_tokens: dict[str, str] = {}


class GuiToolError(Exception):
    pass


def agent_identity() -> dict[str, Any]:
    env = os.environ
    session = env.get("AI_SESSION_ID", "")
    agent_id = env.get("LOOM_AGENT_ID") or session
    if not agent_id:
        provider = "claude" if env.get("CLAUDECODE") else "codex" if any(k.startswith("CODEX") for k in env) else "agent"
        agent_id = f"{provider}-{os.getppid()}"
    return {"agent_id": agent_id, "task_id": env.get("LOOM_TASK_ID", ""), "ai_session": session,
            "owner_pid": os.getppid() if TRANSPORT == "stdio" else 0}


def _call(payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    if TRANSPORT != "stdio" and not load_config().get("allow_remote_mcp", False):
        raise GuiToolError("graphical workspaces are disabled for remote MCP clients "
                           "(enable allow_remote_mcp in ~/.config/loom/agent-input.json)")
    result = request(payload, timeout=timeout)
    if not result.get("ok"):
        code = result.get("code", "failed")
        raise GuiToolError(f"[{code}] {result.get('error', 'request failed')}")
    return result


def _agent(args: dict[str, Any]) -> str:
    ident = agent_identity()
    if TRANSPORT != "stdio":  # remote callers must name themselves; tokens are still required
        return str(args.get("agent_id") or "chatgpt-remote")
    return ident["agent_id"]


def _auth(args: dict[str, Any]) -> dict[str, Any]:
    ws = str(args.get("workspace_id") or "")
    if not ws:
        raise GuiToolError("[invalid] workspace_id is required")
    agent = _agent(args)
    token = _tokens.get(ws)
    if not token:  # MCP restarted or workspace granted to us: attach (owner/grantee only)
        token = _call({"command": "attach", "workspace_id": ws, "agent_id": agent})["token"]
        _tokens[ws] = token
    return {"workspace_id": ws, "agent_id": agent, "token": token}


def _act(command: str, keys: tuple[str, ...], timeout: float = 60.0) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def run(args: dict[str, Any]) -> dict[str, Any]:
        payload = {"command": command, **_auth(args),
                   **{("argv" if k == "command" else k): args[k] for k in keys if k in args}}
        # A stable action id makes a retried call replay instead of clicking twice.
        if command in {"click", "type", "key", "launch", "navigate", "move", "scroll"} and "action_id" not in payload:
            payload["action_id"] = str(args.get("action_id") or uuid.uuid4())
        try:
            return _call(payload, timeout)
        except GuiToolError as exc:
            if "[forbidden]" in str(exc) and args.get("workspace_id") in _tokens:
                _tokens.pop(str(args["workspace_id"]), None)  # stale token: re-attach once
                payload.update(_auth(args))
                return _call(payload, timeout)
            raise
    return run


def t_acquire(args: dict[str, Any]) -> dict[str, Any]:
    ident = agent_identity()
    payload = {"command": "acquire", "agent_id": _agent(args), "request_key": args.get("request_key", ""),
               "kind": args.get("kind", "browser"), "task_id": args.get("task_id") or ident["task_id"],
               "ai_session": ident["ai_session"], "owner_pid": ident["owner_pid"],
               **{k: args[k] for k in ("label", "size", "url", "agent_name") if k in args}}
    result = _call(payload, timeout=90)
    _tokens[result["workspace"]["id"]] = result.pop("token")
    return result


def t_attach(args: dict[str, Any]) -> dict[str, Any]:
    _tokens.pop(str(args.get("workspace_id", "")), None)
    auth = _auth(args)
    return {"ok": True, "workspace_id": auth["workspace_id"], "attached": True}


def t_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    result = _call({"command": "screenshot", **_auth(args),
                    "max_width": int(args.get("max_width") or 1280)}, timeout=30)
    data = Path(result["path"]).read_bytes()
    result["_mcp_image"] = {"data": base64.b64encode(data).decode(), "mimeType": "image/png"}
    return result


def t_release(args: dict[str, Any]) -> dict[str, Any]:
    result = _call({"command": "release", **_auth(args),
                    "keep_profile": bool(args.get("keep_profile", True))}, timeout=30)
    _tokens.pop(str(args.get("workspace_id", "")), None)
    return result


def t_appearance(args: dict[str, Any]) -> dict[str, Any]:
    return _call({"command": "appearance", "agent_id": _agent(args),
                  "color": args.get("color", ""), "name": args.get("name", "")})


def tools(schema: Callable[..., dict[str, Any]], read_only: dict[str, Any], write: dict[str, Any]) -> list[tuple]:
    S = {"type": "string"}
    WS = {"type": "string", "description": "Graphical workspace id from loom_gui_acquire / loom_gui_list."}
    NUM = {"type": "number"}
    ACT = {"type": "string", "description": "Optional idempotency key; a retry with the same id is not repeated."}
    destructive = {**write, "destructiveHint": True, "idempotentHint": False}
    colors = ", ".join(n for n, _ in PALETTE)
    return [
        ("loom_gui_capabilities",
         "Check which isolated graphical capabilities exist before planning GUI work. Input to the user's own "
         "desktop is never offered.", schema({}), read_only,
         lambda a: _call({"command": "capabilities"})),
        ("loom_gui_acquire",
         "Create or re-acquire YOUR isolated graphical workspace (private display; never the user's desktop). "
         "Use only when a task truly needs a GUI: prefer terminal commands and APIs; use kind=browser for web "
         "pages (selector/text actions), kind=desktop for other X11 apps. request_key makes it idempotent: "
         "reuse it after reconnects or crashes to recover the same workspace instead of creating a duplicate.",
         schema({"request_key": S, "kind": {"type": "string", "enum": ["browser", "desktop"]},
                 "url": S, "label": S, "size": {"type": "string", "description": "WIDTHxHEIGHT, default 1280x800"},
                 "agent_name": S, "task_id": S},
                ["request_key"]), write, t_acquire),
        ("loom_gui_list", "List graphical workspaces (yours and others'; no tokens are exposed).",
         schema({"include_released": {"type": "boolean"}}), read_only,
         lambda a: _call({"command": "list", **({"include_released": True} if a.get("include_released") else {})})),
        ("loom_gui_attach", "Attach to an existing workspace you own or were granted.",
         schema({"workspace_id": WS}, ["workspace_id"]), write, t_attach),
        ("loom_gui_grant", "Owner only: allow another agent id to attach to your workspace.",
         schema({"workspace_id": WS, "grantee": S}, ["workspace_id", "grantee"]), write,
         _act("grant", ("grantee",))),
        ("loom_gui_take_control", "Take the single pointer/keyboard of a shared workspace (agents take turns).",
         schema({"workspace_id": WS}, ["workspace_id"]), write, _act("take-control", ())),
        ("loom_gui_launch", "Desktop workspaces: start an X11-capable app (argv list) inside the private display.",
         schema({"workspace_id": WS, "command": {"type": "array", "items": S, "minItems": 1}, "action_id": ACT},
                ["workspace_id", "command"]), write, _act("launch", ("command", "action_id"))),
        ("loom_gui_navigate", "Browser workspaces: load a URL (http(s), about:, file:, data:).",
         schema({"workspace_id": WS, "url": S, "action_id": ACT}, ["workspace_id", "url"]), write,
         _act("navigate", ("url", "action_id"))),
        ("loom_gui_click",
         "Click. Prefer selector (CSS) or text (visible label) in browser workspaces; x/y are screen pixels of "
         "the workspace (see loom_gui_screenshot). Fails with [obscured] rather than clicking the wrong element.",
         schema({"workspace_id": WS, "selector": S, "text": S, "x": NUM, "y": NUM,
                 "button": {"type": "string", "enum": ["left", "middle", "right"]},
                 "count": {"type": "integer", "minimum": 1, "maximum": 3}, "action_id": ACT},
                ["workspace_id"]), destructive,
         _act("click", ("selector", "text", "x", "y", "button", "count", "action_id"))),
        ("loom_gui_type", "Type text into the focused field, or into selector after clicking it.",
         schema({"workspace_id": WS, "text": S, "selector": S, "action_id": ACT}, ["workspace_id", "text"]),
         destructive, _act("type", ("text", "selector", "action_id"), timeout=180)),
        ("loom_gui_key", "Press a key or shortcut such as Enter, ctrl+l, ctrl+shift+t, alt+F4.",
         schema({"workspace_id": WS, "keys": S, "action_id": ACT}, ["workspace_id", "keys"]), destructive,
         _act("key", ("keys", "action_id"))),
        ("loom_gui_scroll", "Scroll by wheel steps (dy>0 down, dx>0 right), optionally over a selector or point.",
         schema({"workspace_id": WS, "dx": {"type": "integer"}, "dy": {"type": "integer"}, "selector": S,
                 "x": NUM, "y": NUM, "action_id": ACT}, ["workspace_id"]), write,
         _act("scroll", ("dx", "dy", "selector", "x", "y", "action_id"))),
        ("loom_gui_move", "Move your visible pointer to x/y (screen pixels of the workspace) without clicking.",
         schema({"workspace_id": WS, "x": NUM, "y": NUM, "duration_ms": {"type": "integer", "minimum": 0,
                                                                          "maximum": 2000}, "action_id": ACT},
                ["workspace_id", "x", "y"]), write, _act("move", ("x", "y", "duration_ms", "action_id"))),
        ("loom_gui_screenshot", "Capture the workspace as an image (returned inline) plus its pointer position.",
         schema({"workspace_id": WS, "max_width": {"type": "integer", "minimum": 320, "maximum": 3840}},
                ["workspace_id"]), read_only, t_screenshot),
        ("loom_gui_state",
         "Read interaction state: pointer, windows and (browser) url/title/focused element; include_text adds "
         "page text, selector returns matching elements' text.",
         schema({"workspace_id": WS, "include_text": {"type": "boolean"}, "selector": S}, ["workspace_id"]),
         read_only, _act("state", ("include_text", "selector"))),
        ("loom_gui_appearance", f"Set your persistent cursor color ({colors}, or #RRGGBB) and short label.",
         schema({"color": S, "name": S}), write, t_appearance),
        ("loom_gui_pause", "Pause your own workspace's input (the user may also pause it; only they can resume that).",
         schema({"workspace_id": WS}, ["workspace_id"]), write, _act("pause", ())),
        ("loom_gui_resume", "Resume your workspace after you paused it.",
         schema({"workspace_id": WS}, ["workspace_id"]), write, _act("resume", ())),
        ("loom_gui_cancel", "Cancel an in-progress action (e.g. long typing) in your workspace.",
         schema({"workspace_id": WS}, ["workspace_id"]), write, _act("cancel", ())),
        ("loom_gui_release",
         "Release your workspace when done: closes its apps and display; keep_profile=false also deletes "
         "the private browser profile.",
         schema({"workspace_id": WS, "keep_profile": {"type": "boolean"}}, ["workspace_id"]), destructive,
         t_release),
    ]
