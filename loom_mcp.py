#!/usr/bin/env python3
"""The one Loom MCP server: Loom UI, whiteboard/display and Working tasks.

Transports (same tool registry):
  python3 loom_mcp.py stdio            local MCP clients (Claude Code, Codex, ...)
  python3 loom_mcp.py http [--port N]  Streamable HTTP for remote clients (ChatGPT)

Every tool maps to one request on Loom's local mode-0600 Unix socket
($XDG_RUNTIME_DIR/tabby.sock). There is deliberately no shell, file or browser
access here: the surface is Loom's display and Working cards only.

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
from tabby.spaces import SpaceStore  # noqa: E402
from tabby import missions, tasks_tile, ui_tree  # noqa: E402
from tabby.notifications import NotificationStore, deliver  # noqa: E402

SERVER_NAME = "loom"
SERVER_VERSION = "1.2.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
TOKEN_PATH = Path.home() / ".config/tabby/mcp-token"
DEFAULT_PORT = 8766
MAX_BODY = 256 * 1024
CHOICE_WAIT_MAX = 50.0
ALLOWED_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:115c:a1e0::/48")]

INSTRUCTIONS = (
    "Loom is the user's desktop companion on their Linux laptop. These tools draw on Loom's "
    "small on-screen board (text, progress bars, status lines, cards, lists, shapes and "
    "clickable choices) and manage Working task cards that stay pinned on screen. Use "
    "loom_display for anything composite; give items stable ids so later calls update them "
    "in place instead of piling up. Keep text short: the board is about 340px wide. "
    "Use loom_notify sparingly for important status/action events, loom_request_decision only "
    "when a genuine user decision blocks work; never infer approval from delivery or silence. "
    "For interactive panels use loom_ui_render (typed component tree, see loom_ui_schema), "
    "then loom_ui_patch for small changes and loom_ui_events to read interactions."
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
        error = str(result.get("error") or result.get("result") or "Loom rejected the request")
        if "No such file" in error or "Connection refused" in error:
            error = "Loom is not running (its Quickshell plugin backend is down)"
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
                    "hint": "Call loom_get_choice later to read the answer."}
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
TASK_ID = {"type": "string", "description": "Working task id from loom_task_list / loom_task_create."}
STATUS = {"type": "string", "enum": ["working", "waiting", "blocked", "done"]}

TOOLS: list[Tool] = [
    ("loom_status", "Read Loom's current board items, face state and Working tasks.",
     _schema({}), READ_ONLY, t_status),
    ("loom_show", "Show Loom's board.", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "show"})),
    ("loom_hide", "Hide Loom's board (items are kept).", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "hide"})),
    ("loom_clear", "Remove every item from Loom's board and hide it.", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "clear"})),
    ("loom_write", "Write a text block on Loom's board.",
     _schema({"text": S, "title": S, "id": ID}, ["text"]), UI_WRITE,
     lambda a: _display([{"type": "text", **_opt(a, "text", "title", "id")}])),
    ("loom_progress", "Show or update a progress bar (value 0..1). Reuse id to move the same bar.",
     _schema({"value": N01, "label": S, "id": ID}, ["value"]), UI_WRITE,
     lambda a: _display([{"type": "progress", "value": a.get("value", 0), **_opt(a, "label", "id")}])),
    ("loom_status_line", "Show or update a one-line status with an icon state.",
     _schema({"label": S, "state": {"type": "string", "enum": ["info", "working", "success", "warning", "error"]}, "id": ID},
             ["label"]), UI_WRITE,
     lambda a: _display([{"type": "status", **_opt(a, "label", "state", "id")}])),
    ("loom_set_face", "Set Loom's face/animation state.",
     _schema({"state": {"type": "string", "enum": ["idle", "listening", "thinking", "tool", "speaking", "approval", "success", "error"]}},
             ["state"]), UI_WRITE,
     lambda a: _ipc({"command": "state", "value": a.get("state", "idle")})),
    ("loom_choice", "Show clickable buttons for the user. With wait_seconds (max 50) the call waits for the click and returns it; otherwise read it later with loom_get_choice.",
     _schema({"label": S, "options": {"type": "array", "items": S, "minItems": 1, "maxItems": 6},
              "wait_seconds": {"type": "number", "minimum": 0, "maximum": CHOICE_WAIT_MAX}, "id": ID},
             ["label", "options"]), UI_WRITE, t_choice),
    ("loom_get_choice", "Read the user's answer to a choice; optionally wait up to wait_seconds for it.",
     _schema({"item_id": S, "wait_seconds": {"type": "number", "minimum": 0, "maximum": CHOICE_WAIT_MAX}}, ["item_id"]),
     READ_ONLY, lambda a: _wait_choice(str(a.get("item_id", "")), float(a.get("wait_seconds") or 0))),
    ("loom_shape", "Draw a basic shape in the board's drawing area. x,y,w,h are fractions 0..1 of that area.",
     _schema({"kind": {"type": "string", "enum": ["line", "arrow", "rect", "circle"]},
              "x": N01, "y": N01, "w": {"type": "number"}, "h": {"type": "number"},
              "label": S, "filled": {"type": "boolean"}, "tone": S, "id": ID},
             ["kind", "x", "y", "w", "h"]), UI_WRITE,
     lambda a: _display([{"type": "shape", **_opt(a, "kind", "x", "y", "w", "h", "label", "filled", "tone", "id")}])),
    ("loom_card", "Show or update a card (title, body, optional badge and progress).",
     _schema({"title": S, "body": S, "badge": S, "progress": N01, "tone": S, "id": ID}, ["title"]), UI_WRITE,
     lambda a: _display([{"type": "card", **_opt(a, "title", "body", "badge", "progress", "tone", "id")}])),
    ("loom_display", "Generic renderer: add/update several board items at once. mode=replace clears the board first.",
     _schema({"items": {"type": "array", "items": ITEM_SCHEMA, "minItems": 1, "maxItems": 32},
              "mode": {"type": "string", "enum": ["append", "replace"]}}, ["items"]), UI_WRITE,
     lambda a: _display(a.get("items") or [], str(a.get("mode") or "append"))),
    ("loom_remove", "Remove board items by id.",
     _schema({"ids": {"type": "array", "items": S, "minItems": 1}}, ["ids"]), UI_WRITE,
     lambda a: _ipc({"command": "ui-remove", "ids": a.get("ids") or []})),
    ("loom_task_list", "List Working task cards.", _schema({}), READ_ONLY,
     lambda a: _task_result(_ipc({"command": "work-list"}))),
    ("loom_task_create", "Create a Working task card pinned on screen.",
     _schema({"title": S, "summary": S, "progress": N01, "status": STATUS}, ["title"]), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-create", **_opt(a, "title", "summary", "progress", "status")}))),
    ("loom_task_pin_current", "Pin Loom's current ChatGPT conversation as a Working task card (it completes itself when the reply finishes).",
     _schema({"title": S}), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-pin-current", "title": a.get("title", "")}, timeout=20))),
    ("loom_task_update", "Update a Working task's title, progress, status or summary.",
     _schema({"task_id": TASK_ID, "title": S, "progress": N01, "status": STATUS, "summary": S}, ["task_id"]), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-update", "task_id": a.get("task_id", ""),
                                  "title": a.get("title"), "progress": a.get("progress"),
                                  "status": a.get("status"), "summary": a.get("summary")}))),
    ("loom_task_done", "Mark a Working task done (it stays pinned until the user removes it).",
     _schema({"task_id": TASK_ID, "summary": S}, ["task_id"]), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-complete", "task_id": a.get("task_id", ""), "summary": a.get("summary", "")}))),
    ("loom_task_reopen", "Reopen a done Working task (status back to working).",
     _schema({"task_id": TASK_ID, "summary": S}, ["task_id"]), UI_WRITE,
     lambda a: _task_result(_ipc({"command": "work-reopen", "task_id": a.get("task_id", ""), **_opt(a, "summary")}))),
    ("loom_task_open", "Open a conversation-backed Working task in Loom (optionally in Voice).",
     _schema({"task_id": TASK_ID, "voice": {"type": "boolean"}}, ["task_id"]), UI_WRITE,
     lambda a: _ipc({"command": "work-voice" if a.get("voice") else "work-open", "task_id": a.get("task_id", "")})),
    ("loom_web_worker_create", "Idempotently create a background ChatGPT web worker, send its task, verify its canonical conversation, discover real projects, and move it to Working.",
     _schema({"request_id": S, "title": S, "prompt": S, "working_project": S}, ["request_id", "title", "prompt"]), UI_WRITE,
     lambda a: _ipc({"command":"web-worker-create", **_opt(a,"request_id","title","prompt","working_project")}, timeout=45)),
    ("loom_web_worker_inspect", "Inspect durable and live web-worker state. A finished response becomes awaiting-review, never Done.",
     _schema({"task_id": TASK_ID}, ["task_id"]), UI_WRITE,
     lambda a: _ipc({"command":"web-worker-inspect", "task_id":a["task_id"]}, timeout=8)),
    ("loom_web_worker_review", "Record an independent reviewer decision and evidence; approved work moves to Done only after project state is verified.",
     _schema({"task_id": TASK_ID, "decision":{"type":"string","enum":["approved","rejected"]},
              "reviewer":S, "evidence":S, "done_project":S}, ["task_id","decision","reviewer","evidence"]), UI_WRITE,
     lambda a: _ipc({"command":"web-worker-review", **_opt(a,"task_id","decision","reviewer","evidence","done_project")}, timeout=30)),
    ("loom_wake", "Summon Loom on screen (starts its ChatGPT Voice session).", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "wake"}, timeout=10)),
    ("loom_close", "Close Loom (ends Voice and clears the board).", _schema({}), UI_WRITE,
     lambda a: _ipc({"command": "close"})),
]

def _notification_new(args, kind=None):
    store = NotificationStore()
    item = store.create(
        title=args.get("title", ""), body=args.get("body", ""),
        kind=kind or args.get("kind", "info"),
        urgency=args.get("urgency", "normal"),
        options=args.get("options", []),
        request_id=args.get("request_id", ""),
        ttl_minutes=args.get("ttl_minutes"),
    )
    delivery = {"desktop": "existing", "phone": "existing"}
    if item["created"]:
        delivery = deliver(item, store)
        try:
            _ipc({"command": "notification-refresh"})
        except ToolError:
            pass
    return {"ok": True, "notification": item, "delivery": delivery}


NOTIFICATION_COMMON = {"title": S, "body": S,
                       "urgency": {"type": "string", "enum": ["low", "normal", "high"]},
                       "request_id": S, "ttl_minutes": {"type": "integer", "minimum": 1, "maximum": 43200}}
TOOLS.extend([
    ("loom_notify",
     "Persist a meaningful notification. Use for important milestones, urgent warnings and actionable blockers. Reuse request_id to deduplicate. Delivery is best effort; check the result.",
     _schema({**NOTIFICATION_COMMON, "kind": {"type": "string", "enum": ["info", "status", "action"]}},
             ["title"]), UI_WRITE, lambda a: _notification_new(a)),
    ("loom_request_decision",
     "Persist a decision requiring a user response. Options must be clear and unambiguous. Expiry, dismissal and no response mean no decision. Check loom_notification_get for the answer.",
     _schema({**NOTIFICATION_COMMON, "kind": {"type": "string", "enum": ["approval", "choice"]},
              "options": {"type": "array", "items": S, "minItems": 2, "maxItems": 6}},
             ["title", "kind"]), UI_WRITE, lambda a: _notification_new(a)),
    ("loom_notification_list",
     "Read persistent notifications and pending decisions.",
     _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 100},
              "include_closed": {"type": "boolean"}}), READ_ONLY,
     lambda a: {"ok": True, "notifications": NotificationStore().list(
         limit=a.get("limit", 30), include_closed=a.get("include_closed", True))}),
    ("loom_notification_get",
     "Read notification delivery and user response status.",
     _schema({"notification_id": S}, ["notification_id"]), READ_ONLY,
     lambda a: {"ok": True, "notification": NotificationStore().get(a["notification_id"])}),
])

# Versioned reusable modules/spaces. Stored in ~/.config/tabby/modules.json so
# the existing sandboxed user service can write it without widening privileges.
SPACE_STORE = SpaceStore()


def _module_ui(module):
    if not module["visible"]:
        return None, "hidden"
    if module["kind"] == "tasks" and module["placement"]["surface"] == "performance":
        return None, ("performance host renderer pending; Loom Tasks is shown only "
                      "while hovering the counter after enabling `qs ipc call loom toggleTasks`")
    if module["placement"]["surface"] != "board":
        return None, "surface renderer not installed"
    if module["kind"] == "text":
        return {"type": "card", "id": "module-" + module["id"],
                "title": module["title"], "body": str(module["data"].get("text", ""))[:3000]}, None
    if module["kind"] == "tasks":
        tasks = _task_result(_ipc({"command": "work-list"})).get("tasks", [])
        lines = [(str(t.get("status", "")) + " · " + str(t.get("title", "")))[:130]
                 for t in tasks[:8]]
        return {"type": "list", "id": "module-" + module["id"],
                "title": module["title"], "entries": lines or ["No current Working tasks"]}, None
    return None, "native live " + module["kind"] + " renderer pending"


def _space_show(a):
    space = SPACE_STORE.get_space(a["space_id"])
    items, skipped = [], []
    for module in space["modules"]:
        ui, reason = _module_ui(module)
        if ui is not None:
            items.append(ui)
        else:
            skipped.append({"module_id": module["id"], "reason": reason})
    if items:
        _display(items, str(a.get("mode") or "replace"))
    return {"ok": True, "space": space["space"], "rendered_ids": [it["id"] for it in items],
            "skipped": skipped, "board_visible": bool(items)}


MODULE_KIND_SCHEMA = {"type": "string", "enum": ["text", "tasks", "memory", "cpu", "storage", "battery", "weather"]}
PLACEMENT_SCHEMA = {"type": "object", "description": "Desired surface/anchor/geometry; only board rendering is implemented",
                    "properties": {"surface": {"type": "string", "enum": ["board", "performance", "floating"]},
                                   "anchor": {"type": "string", "enum": ["free", "top-left", "top-right", "bottom-left", "bottom-right", "center"]},
                                   "x": {"type": "number"}, "y": {"type": "number"},
                                   "width": {"type": "number"}, "height": {"type": "number"},
                                   "monitor": S, "workspace": S}, "additionalProperties": False}
TOOLS.extend([
    ("loom_module_list", "List durable Loom modules and spaces including their sizes and placements.",
     _schema({}), READ_ONLY, lambda a: SPACE_STORE.list()),
    ("loom_module_get", "Inspect one saved Loom module.",
     _schema({"module_id": S}, ["module_id"]), READ_ONLY,
     lambda a: SPACE_STORE.get_module(a["module_id"])),
    ("loom_module_create", "Create a persistent Loom module. Board text/tasks render now; system/performance/floating need native renderer.",
     _schema({"kind": MODULE_KIND_SCHEMA, "title": S, "data": {"type": "object"},
              "placement": PLACEMENT_SCHEMA, "visible": {"type": "boolean"}, "request_id": S},
             ["kind", "title"]), UI_WRITE,
     lambda a: SPACE_STORE.create_module(**{k:a[k] for k in ("kind","title","data","placement","visible","request_id") if k in a})),
    ("loom_module_update", "Update a saved module title, data, visibility or requested geometry.",
     _schema({"module_id": S, "title": S, "data": {"type": "object"},
              "placement": PLACEMENT_SCHEMA, "visible": {"type": "boolean"}}, ["module_id"]), UI_WRITE,
     lambda a: SPACE_STORE.update_module(a["module_id"], **{k:a[k] for k in ("title","data","placement","visible") if k in a})),
    ("loom_module_delete", "Delete a module and unlink it from saved spaces.",
     _schema({"module_id": S}, ["module_id"]), UI_WRITE,
     lambda a: SPACE_STORE.delete_module(a["module_id"])),
    ("loom_space_list", "List saved Loom spaces and module configurations.",
     _schema({}), READ_ONLY, lambda a: SPACE_STORE.list()["spaces"]),
    ("loom_space_get", "Read saved space and full module specs.",
     _schema({"space_id": S}, ["space_id"]), READ_ONLY,
     lambda a: SPACE_STORE.get_space(a["space_id"])),
    ("loom_space_save", "Save or update a named reusable collection of module IDs.",
     _schema({"name": S, "module_ids": {"type": "array", "items": S, "maxItems": 64}, "space_id": S},
             ["name","module_ids"]), UI_WRITE,
     lambda a: SPACE_STORE.save_space(name=a["name"], module_ids=a["module_ids"], space_id=a.get("space_id"))),
    ("loom_space_show", "Open a saved space on Loom board. Returns explicit skipped modules for unsupported renderers.",
     _schema({"space_id": S, "mode": {"type": "string", "enum": ["replace","append"]}}, ["space_id"]),
     UI_WRITE, _space_show),
    ("loom_space_delete", "Delete a saved space without deleting its reusable modules.",
     _schema({"space_id": S}, ["space_id"]), UI_WRITE,
     lambda a: SPACE_STORE.delete_space(a["space_id"])),
])

# Written by loom_tasks.py refresh (QML host); the MCP service only reads it.
TASKS_CACHE = tasks_tile.TasksCache()

# Capture-before-execute works even when the remote sandbox cannot start workers.
IDEA_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {"title": S, "body": S, "repo": S,
                              "priority": {"type": "string", "enum": ["low", "normal", "high"]}},
               "required": ["title"]}
TOOLS.extend([
    ("loom_idea_capture", "Persist 1-32 ideas in the Philipedia durable inbox BEFORE delegation. Idempotent when request_id is reused. This never starts a worker.",
     _schema({"ideas": {"type": "array", "items": IDEA_SCHEMA, "minItems": 1, "maxItems": 32},
              "request_id": S}, ["ideas"]), UI_WRITE,
     lambda a: missions.idea_capture(a["ideas"], request_id=a.get("request_id"))),
    ("loom_idea_dispatch", "Link a captured idea to an already created top-level Claude/Codex LOOM mission. Does not start workers. Requires exact repository scope and refuses redirects.",
     _schema({"idea_id": S, "mission_id": S}, ["idea_id", "mission_id"]), UI_WRITE,
     lambda a: missions.idea_dispatch(idea_id=a["idea_id"], mission_id=a["mission_id"])),
    ("loom_idea_list", "Read every captured idea from the durable Philipedia inbox.",
     _schema({}), READ_ONLY, lambda a: missions.idea_list()),
    ("loom_tasks_snapshot", "Read the cached Tasks tile projection (mission/idea states from the LOOM ledger, stale flag and errors). No network; never contains progress percentages.",
     _schema({}), READ_ONLY, lambda a: TASKS_CACHE.load()),
    ("loom_mission_health", "Read whether Philipedia currently supports isolated coding worker execution.",
     _schema({}), READ_ONLY, lambda a: missions.mission_health()),
])

# The mission bridge has a fixed destination and fixed remote executable.
# No arbitrary shell command, remote host, or SSH arguments are accepted.
TOOLS.extend([
    ("loom_handoff_status", "Read event watcher status, registered mission origins, and pending delivery count from Philipedia.",
     _schema({}), READ_ONLY, lambda a: missions.handoff_status()),
    ("loom_handoff_register", "Attach a verified origin reference and optional ChatGPT conversation URL to an existing mission. Does not send a ChatGPT message.",
     _schema({"mission_id": S, "origin_ref": S, "origin_url": S,
              "origin_source": {"type":"string","enum":["loom","chatgpt","api","codex","claude"]},
              "auto_continuation": {"type":"boolean"}}, ["mission_id","origin_ref"]), UI_WRITE,
     lambda a: missions.handoff_register(**{k:a[k] for k in
              ("mission_id","origin_ref","origin_url","origin_source","auto_continuation") if k in a})),
    ("loom_mission_list", "Read durable LOOM missions on Philipedia and their verified run/review status.",
     _schema({}), READ_ONLY, lambda a: missions.mission_list()),
    ("loom_mission_activity", "Read recorded execution events for a LOOM mission.",
     _schema({"mission_id": S}, ["mission_id"]), READ_ONLY,
     lambda a: missions.mission_activity(a["mission_id"])),
    ("loom_mission_create", "Delegate a coding mission to Claude/Codex on Philipedia. Requires existing absolute repo path; do not claim completion until status verifies it.",
     _schema({"goal": S, "repo": S, "agent": {"type": "string", "enum": ["codex","clawd"]},
              "title": S, "budget_minutes": {"type": "integer", "minimum": 5, "maximum": 90},
              "origin_ref": S, "origin_url": S,
              "origin_source": {"type":"string","enum":["loom","chatgpt","api","codex","claude"]},
              "auto_continuation": {"type":"boolean"}},
             ["goal","repo"]), UI_WRITE,
     lambda a: missions.mission_create(**{k:a[k] for k in ("goal","repo","agent","title","budget_minutes","origin_ref","origin_url","origin_source","auto_continuation") if k in a})),
    ("loom_capability_request", "For a requested missing feature, tool, integration or behavior change, durably save/reuse one capability gap, then dispatch at most one isolated Codex mission if Philipedia is healthy; returns state=blocked when not runnable, never claims deployment or a ChatGPT web worker.",
     _schema({"request_id": S, "title": S, "body": S, "repo": S,
              "priority": {"type": "string", "enum": ["low","normal","high"]},
              },
             ["request_id","title","body","repo"]), UI_WRITE,
     lambda a: missions.capability_request(**{k:a[k] for k in ("request_id","title","body","repo","priority") if k in a})),
    ("loom_mission_resume", "Resume a failed, paused, or review mission after resolving its blocker.",
     _schema({"mission_id": S}, ["mission_id"]), UI_WRITE,
     lambda a: missions.mission_resume(a["mission_id"])),
])

# Declarative interactive UI (tabby/ui_tree.py). Live views and their undo
# stacks live in the backend; templates are validated trees saved by this process.
UI_TEMPLATES = ui_tree.TemplateStore()
VIEW_ID = {"type": "string", "description": "View id (letters, digits, _ or -); one view per id, at most 4."}
UI_NODE = {"type": "object", "description": (
    "Component {type, id, props?, children?, on?}. Containers: column, row, card. Leaves: text, badge, "
    "progress, divider, list, button(press), toggle(change), slider(change), input(change, submit), "
    "select(change). Colours are theme tones only (neutral|primary|secondary|tertiary|success|warning|error). "
    "on: {event: [actions]} with actions {do:emit,name} | {do:set,target,prop,value|from_event:true} | "
    "{do:toggle,target}. Ids are unique per view. Call loom_ui_schema for every prop."),
    "properties": {"type": S, "id": S}, "required": ["type", "id"], "additionalProperties": True}
UI_OP = {"type": "object", "description": (
    "op: set_props{id,props,unset?} | set_on{id,on} | insert{parent,index?,node} | remove{id} | "
    "move{id,parent,index?} | replace{id,node}"), "properties": {"op": S}, "required": ["op"],
    "additionalProperties": True}
REVISION = {"type": "integer", "minimum": 1, "description": "Optional optimistic lock: fail if the view changed since this revision."}


def _ui_render(a):
    root, _ = ui_tree.validate_tree(a.get("root"))  # fail fast with a precise error
    return _ipc({"command": "ui-render", "view_id": a.get("view_id"), "root": root,
                **_opt(a, "title", "base_revision")})


def _ui_events(a):
    since = int(a.get("since") or 0)
    deadline = time.monotonic() + max(0.0, min(CHOICE_WAIT_MAX, float(a.get("wait_seconds") or 0)))
    while True:
        result = _ipc({"command": "ui-events", "since": since, **_opt(a, "view_id")})
        if result.get("events") or time.monotonic() >= deadline:
            return result
        time.sleep(0.15)


def _ui_template_save(a):
    root = a.get("root")
    if root is None:
        if not a.get("from_view"):
            raise ValueError("give root or from_view")
        root = _ipc({"command": "ui-get", "view_id": a["from_view"]})["view"]["root"]
    return UI_TEMPLATES.save(a.get("name"), root, a.get("description") or "")


def _ui_template_render(a):
    root = UI_TEMPLATES.instantiate(a.get("name"), a.get("params"))
    return _ipc({"command": "ui-render", "view_id": a.get("view_id") or a.get("name", "").replace(".", "-"),
                "root": root, "title": a.get("title") or ""})


TOOLS.extend([
    ("loom_ui_schema", "Read the declarative UI component catalogue: component types, typed props, events, actions, tones and limits.",
     _schema({}), READ_ONLY, lambda a: ui_tree.schema_summary()),
    ("loom_ui_render", "Render (or fully replace) an interactive view on Loom's board from a typed component tree. Existing board items stay.",
     _schema({"view_id": VIEW_ID, "root": UI_NODE, "title": S, "base_revision": REVISION}, ["view_id", "root"]),
     UI_WRITE, _ui_render),
    ("loom_ui_patch", "Apply granular, atomic patch ops to a view (set props, rebind events, insert/remove/move/replace components). Undoable.",
     _schema({"view_id": VIEW_ID, "ops": {"type": "array", "items": UI_OP, "minItems": 1, "maxItems": ui_tree.MAX_PATCH_OPS},
              "base_revision": REVISION}, ["view_id", "ops"]),
     UI_WRITE, lambda a: _ipc({"command": "ui-patch", "view_id": a.get("view_id"), "ops": a.get("ops"),
                              **_opt(a, "base_revision")})),
    ("loom_ui_undo", "Undo the last render/patch of a view (up to 20 steps).",
     _schema({"view_id": VIEW_ID}, ["view_id"]), UI_WRITE,
     lambda a: _ipc({"command": "ui-undo", "view_id": a.get("view_id")})),
    ("loom_ui_redo", "Redo the last undone change of a view.",
     _schema({"view_id": VIEW_ID}, ["view_id"]), UI_WRITE,
     lambda a: _ipc({"command": "ui-redo", "view_id": a.get("view_id")})),
    ("loom_ui_get", "Read one view's current tree, revision and undo depth, or every view when view_id is omitted.",
     _schema({"view_id": VIEW_ID}), READ_ONLY, lambda a: _ipc({"command": "ui-get", **_opt(a, "view_id")})),
    ("loom_ui_close", "Remove a view from Loom's board.",
     _schema({"view_id": VIEW_ID}, ["view_id"]), UI_WRITE,
     lambda a: _ipc({"command": "ui-close", "view_id": a.get("view_id")})),
    ("loom_ui_events", "Read user interactions (button presses, toggles, slider/input/select changes and emitted intents) after sequence `since`; optionally wait up to wait_seconds for one.",
     _schema({"since": {"type": "integer", "minimum": 0}, "view_id": VIEW_ID,
              "wait_seconds": {"type": "number", "minimum": 0, "maximum": CHOICE_WAIT_MAX}}), READ_ONLY, _ui_events),
    ("loom_ui_template_save", "Save a validated component tree (or a live view via from_view) as a named reusable template. {{param}} placeholders in strings are filled at render time.",
     _schema({"name": {"type": "string", "description": "lowercase name, e.g. deploy.confirm"}, "root": UI_NODE,
              "from_view": VIEW_ID, "description": S}, ["name"]), UI_WRITE, _ui_template_save),
    ("loom_ui_template_list", "List saved UI templates and their parameters.",
     _schema({}), READ_ONLY, lambda a: UI_TEMPLATES.list()),
    ("loom_ui_template_get", "Read one saved UI template.",
     _schema({"name": S}, ["name"]), READ_ONLY, lambda a: UI_TEMPLATES.get(a.get("name"))),
    ("loom_ui_template_delete", "Delete a saved UI template (live views are unaffected).",
     _schema({"name": S}, ["name"]), UI_WRITE, lambda a: UI_TEMPLATES.delete(a.get("name"))),
    ("loom_ui_template_render", "Render a saved template as a view, filling its {{params}} with plain strings.",
     _schema({"name": S, "view_id": VIEW_ID, "title": S,
              "params": {"type": "object", "additionalProperties": {"type": "string"}}}, ["name"]),
     UI_WRITE, _ui_template_render),
])

TOOL_INDEX = {t[0]: t for t in TOOLS}
# Keep legacy Tabby and short-lived Lume clients working without advertising them.
TOOL_INDEX.update({"lume_" + n[5:]: tool for n, tool in list(TOOL_INDEX.items()) if n.startswith("loom_")})
TOOL_INDEX.update({"tabby_" + n[5:]: tool for n, tool in list(TOOL_INDEX.items()) if n.startswith("loom_")})


def assistant_name() -> str:
    try:
        data = json.loads((Path.home()/".config/tabby/config.json").read_text())
        return str(data.get("assistant_name") or "Loom").strip()[:60] or "Loom"
    except Exception:
        return "Loom"


def tool_list() -> list[dict[str, Any]]:
    return [{"name": n, "description": d.replace("Loom", assistant_name()), "inputSchema": s, "annotations": ann} for n, d, s, ann, _ in TOOLS]


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    tool = TOOL_INDEX.get(name)
    if not tool:
        raise KeyError(name)
    try:
        result = tool[4](args if isinstance(args, dict) else {})
        is_error = False
    except ToolError as error:
        result, is_error = {"ok": False, "error": str(error)}, True
    except (TypeError, ValueError, missions.MissionBridgeError) as error:
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
                   "instructions": INSTRUCTIONS.replace("Loom", assistant_name())})
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
    server_version = "loom-mcp"
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
    sys.stderr.write(f"loom-mcp listening on {host}:{port} (token in {TOKEN_PATH})\n")
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
