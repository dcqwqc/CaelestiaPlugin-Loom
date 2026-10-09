"""Typed declarative Loom UI: component trees, patches, undo, events, templates.

A *view* is a small tree of typed components rendered on Loom's board next to
the existing board items. Every node is ``{"type", "id", "props", "children",
"on"}``; each type has a closed prop schema (unknown props are rejected, not
kept), colours are Caelestia theme *tokens* (never raw colours) and text is
always rendered as plain text.

Interaction is declarative only. A node may bind its events to a short list of
actions: ``emit`` (report a named intent to the agent), ``set`` (change one
prop of another node in the same view) and ``toggle`` (show/hide a node). There
is no way to bind a shell command, URL, IPC command or script. Every user
interaction is appended to a bounded event log the agent reads by sequence.
"""
from __future__ import annotations

import copy
import json
import math
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

from tabby.ipc import MAX_PAYLOAD, MAX_REPLY
from tabby.spaces import JsonStore

MAX_VIEWS = 4
MAX_NODES = 64
MAX_DEPTH = 6
# Exact compact UTF-8 size. Every view must fit one IPC request/reply, and all
# views together (ui-get, the reply that carries most) must too, with headroom
# for titles and the JSON envelope.
MAX_VIEW_BYTES = 24 * 1024
MAX_EVENTS_REPLY_BYTES = 96 * 1024
MAX_ACTIONS = 4
MAX_PATCH_OPS = 32
UNDO_DEPTH = 20
MAX_EVENTS = 200
MAX_TEMPLATES = 64

assert MAX_VIEWS * (MAX_VIEW_BYTES + 1024) < min(MAX_PAYLOAD, MAX_REPLY)
assert MAX_EVENTS_REPLY_BYTES + 1024 < MAX_REPLY

TONES = ("neutral", "primary", "secondary", "tertiary", "success", "warning", "error")
GAPS = ("none", "small", "normal", "large")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,47}$")
_PARAM_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]{0,31})\s*\}\}")


class UIError(ValueError):
    pass


def _str(limit: int) -> dict[str, Any]:
    return {"t": "str", "max": limit}


def _enum(*values: str) -> dict[str, Any]:
    return {"t": "enum", "values": values}


def json_bytes(value: Any) -> int:
    """Size of ``value`` exactly as the IPC layer sends it (compact, UTF-8)."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


BOOL = {"t": "bool"}
TONE = _enum(*TONES)
COMMON = {"hidden": BOOL}

# type -> container?, prop specs, defaults, events and the event's value prop.
COMPONENTS: dict[str, dict[str, Any]] = {
    "column": {"container": True, "props": {"gap": _enum(*GAPS)}, "defaults": {"gap": "normal"}},
    "row": {"container": True, "props": {"gap": _enum(*GAPS), "align": _enum("start", "center", "end")},
            "defaults": {"gap": "normal", "align": "start"}},
    "card": {"container": True, "props": {"title": _str(120), "tone": TONE}, "defaults": {"title": "", "tone": "neutral"}},
    "text": {"props": {"text": _str(2000), "style": _enum("body", "label", "title", "caption"), "tone": TONE},
             "defaults": {"text": "", "style": "body", "tone": "neutral"}},
    "badge": {"props": {"text": _str(40), "tone": TONE}, "defaults": {"text": "", "tone": "primary"}},
    "progress": {"props": {"label": _str(120), "value": {"t": "num", "lo": 0.0, "hi": 1.0}, "tone": TONE},
                 "defaults": {"label": "", "value": 0.0, "tone": "primary"}},
    "divider": {"props": {"label": _str(60)}, "defaults": {"label": ""}},
    "list": {"props": {"entries": {"t": "strs", "max": 12, "len": 200}, "ordered": BOOL},
             "defaults": {"entries": [], "ordered": False}},
    "button": {"props": {"label": _str(60), "variant": _enum("filled", "tonal", "outlined", "text"),
                         "tone": TONE, "disabled": BOOL},
               "defaults": {"label": "", "variant": "tonal", "tone": "primary", "disabled": False},
               "events": {"press": None}},
    "toggle": {"props": {"label": _str(120), "value": BOOL, "disabled": BOOL},
               "defaults": {"label": "", "value": False, "disabled": False},
               "events": {"change": "value"}},
    "slider": {"props": {"label": _str(120), "value": {"t": "num", "lo": -1e6, "hi": 1e6},
                         "min": {"t": "num", "lo": -1e6, "hi": 1e6}, "max": {"t": "num", "lo": -1e6, "hi": 1e6},
                         "step": {"t": "num", "lo": 0.0, "hi": 1e6}, "disabled": BOOL},
               "defaults": {"label": "", "value": 0.0, "min": 0.0, "max": 1.0, "step": 0.0, "disabled": False},
               "events": {"change": "value"}},
    "input": {"props": {"label": _str(120), "value": _str(500), "placeholder": _str(120),
                        "max_length": {"t": "int", "lo": 1, "hi": 500}, "disabled": BOOL},
              "defaults": {"label": "", "value": "", "placeholder": "", "max_length": 200, "disabled": False},
              "events": {"change": "value", "submit": "value"}},
    "select": {"props": {"label": _str(120), "options": {"t": "strs", "max": 8, "len": 60, "min": 1},
                         "value": _str(60), "disabled": BOOL},
               "defaults": {"label": "", "options": [], "value": "", "disabled": False},
               "events": {"change": "value"}},
}


def _prop(spec: dict[str, Any], value: Any, where: str) -> Any:
    kind = spec["t"]
    if kind == "str":
        if not isinstance(value, str):
            raise UIError(f"{where} must be a string")
        if len(value) > spec["max"]:
            raise UIError(f"{where} exceeds {spec['max']} characters")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise UIError(f"{where} must be a boolean")
        return value
    if kind in {"num", "int"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise UIError(f"{where} must be a finite number")
        if kind == "int" and value != int(value):
            raise UIError(f"{where} must be an integer")
        if not spec["lo"] <= value <= spec["hi"]:
            raise UIError(f"{where} must be between {spec['lo']} and {spec['hi']}")
        return int(value) if kind == "int" else float(value)
    if kind == "enum":
        if value not in spec["values"]:
            raise UIError(f"{where} must be one of {list(spec['values'])}")
        return value
    if kind == "strs":
        if not isinstance(value, list) or len(value) > spec["max"] or len(value) < spec.get("min", 0):
            raise UIError(f"{where} must be a list of {spec.get('min', 0)}..{spec['max']} strings")
        for entry in value:
            if not isinstance(entry, str) or len(entry) > spec["len"]:
                raise UIError(f"{where} entries must be strings of at most {spec['len']} characters")
        return list(value)
    raise AssertionError(kind)


def _props(kind: str, raw: Any, where: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise UIError(f"{where}.props must be an object")
    specs = {**COMPONENTS[kind]["props"], **COMMON}
    unknown = sorted(set(raw) - set(specs))
    if unknown:
        raise UIError(f"{where}: unknown prop(s) {unknown} for {kind}")
    out = {"hidden": False, **copy.deepcopy(COMPONENTS[kind].get("defaults", {}))}
    for key, value in raw.items():
        out[key] = _prop(specs[key], value, f"{where}.props.{key}")
    if kind == "slider":
        if out["min"] >= out["max"]:
            raise UIError(f"{where}: slider min must be below max")
        out["value"] = min(out["max"], max(out["min"], out["value"]))
    if kind == "input" and len(out["value"]) > out["max_length"]:
        raise UIError(f"{where}: value longer than max_length")
    if kind == "select" and out["value"] and out["value"] not in out["options"]:
        raise UIError(f"{where}: select value must be one of its options")
    return out


def _actions(kind: str, raw: Any, where: str) -> dict[str, list[dict[str, Any]]]:
    if raw is None:
        return {}
    events = COMPONENTS[kind].get("events") or {}
    if not isinstance(raw, dict):
        raise UIError(f"{where}.on must be an object")
    out: dict[str, list[dict[str, Any]]] = {}
    for event, actions in raw.items():
        if event not in events:
            raise UIError(f"{where}: {kind} has no '{event}' event (allowed: {sorted(events)})")
        if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
            raise UIError(f"{where}.on.{event} must be a list of 1..{MAX_ACTIONS} actions")
        bound = []
        for i, action in enumerate(actions):
            at = f"{where}.on.{event}[{i}]"
            if not isinstance(action, dict):
                raise UIError(f"{at} must be an object")
            do = action.get("do")
            if do == "emit":
                if set(action) - {"do", "name"} or not _NAME_RE.match(str(action.get("name") or "")):
                    raise UIError(f"{at}: emit needs only a lowercase 'name'")
                bound.append({"do": "emit", "name": action["name"]})
            elif do == "toggle":
                if set(action) - {"do", "target"} or not isinstance(action.get("target"), str):
                    raise UIError(f"{at}: toggle needs only a 'target' id")
                bound.append({"do": "toggle", "target": action["target"]})
            elif do == "set":
                if set(action) - {"do", "target", "prop", "value", "from_event"}:
                    raise UIError(f"{at}: set accepts target, prop and value or from_event")
                if not isinstance(action.get("target"), str) or not isinstance(action.get("prop"), str):
                    raise UIError(f"{at}: set needs 'target' and 'prop'")
                from_event = action.get("from_event", False)
                if not isinstance(from_event, bool) or from_event == ("value" in action):
                    raise UIError(f"{at}: set needs exactly one of 'value' or from_event: true")
                if from_event and events[event] is None:
                    raise UIError(f"{at}: '{event}' carries no value for from_event")
                entry = {"do": "set", "target": action["target"], "prop": action["prop"]}
                entry.update({"from_event": True} if from_event else {"value": action["value"]})
                bound.append(entry)
            else:
                raise UIError(f"{at}: action 'do' must be emit, set or toggle")
        out[event] = bound
    return out


def _node(raw: Any, depth: int, seen: dict[str, dict[str, Any]], where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise UIError(f"{where} must be an object")
    if depth > MAX_DEPTH:
        raise UIError(f"tree deeper than {MAX_DEPTH} levels")
    unknown = sorted(set(raw) - {"type", "id", "props", "children", "on"})
    if unknown:
        raise UIError(f"{where}: unknown field(s) {unknown}")
    kind = raw.get("type")
    if kind not in COMPONENTS:
        raise UIError(f"{where}: unknown component type {kind!r} (allowed: {sorted(COMPONENTS)})")
    node_id = raw.get("id")
    if not isinstance(node_id, str) or not _ID_RE.match(node_id):
        raise UIError(f"{where}: id must match {_ID_RE.pattern}")
    if node_id in seen:
        raise UIError(f"duplicate component id {node_id!r}")
    where = f"{kind}#{node_id}"
    node: dict[str, Any] = {"type": kind, "id": node_id, "props": _props(kind, raw.get("props"), where)}
    seen[node_id] = node
    if len(seen) > MAX_NODES:
        raise UIError(f"view has more than {MAX_NODES} components")
    children = raw.get("children")
    if COMPONENTS[kind].get("container"):
        if children is None:
            children = []
        if not isinstance(children, list):
            raise UIError(f"{where}.children must be a list")
        node["children"] = [_node(c, depth + 1, seen, f"{where}.children[{i}]") for i, c in enumerate(children)]
    elif children:
        raise UIError(f"{where}: {kind} cannot have children")
    on = _actions(kind, raw.get("on"), where)
    if on:
        node["on"] = on
    return node


# Props other props are validated against. Events may not change them, so a
# bound set can be checked once, statically, against the current tree.
CONSTRAINT_PROPS = {"slider": {"min", "max", "step"}, "input": {"max_length"}, "select": {"options"}}


def _assignable(source: dict[str, Any], event: str, target: dict[str, Any], prop: str) -> str | None:
    """Why the value ``source`` emits on ``event`` cannot go into target.prop, or None if it always can."""
    src, dst = source["props"], target["props"]
    spec = {**COMPONENTS[target["type"]]["props"], **COMMON}[prop]
    kind = source["type"]
    if spec["t"] == "bool":
        return None if kind == "toggle" else f"{kind} {event} value is not a boolean"
    if spec["t"] == "num":
        if kind != "slider":
            return f"{kind} {event} value is not a number"
        if src["min"] < spec["lo"] or src["max"] > spec["hi"]:
            return f"slider range {src['min']}..{src['max']} exceeds {spec['lo']}..{spec['hi']}"
        return None
    if spec["t"] == "str":
        if kind == "input":
            longest = src["max_length"]
        elif kind == "select":
            longest = max((len(o) for o in src["options"]), default=0)
        else:
            return f"{kind} {event} value is not a string"
        limit = dst["max_length"] if target["type"] == "input" and prop == "value" else spec["max"]
        if longest > limit:
            return f"{kind} values may be {longest} characters, target allows {limit}"
        if target["type"] == "select" and prop == "value":
            if kind != "select" or not set(src["options"]) <= set(dst["options"]):
                return "only a select whose options are a subset of the target's can set its value"
        return None
    if spec["t"] == "enum":
        if kind != "select" or not set(src["options"]) <= set(spec["values"]):
            return f"{kind} {event} values are not all in {list(spec['values'])}"
        return None
    return f"{spec['t']} props cannot be set from an event"


def _check_targets(index: dict[str, dict[str, Any]]) -> None:
    for node in index.values():
        for event, actions in (node.get("on") or {}).items():
            for action in actions:
                if action["do"] == "emit":
                    continue
                target = index.get(action["target"])
                at = f"{node['type']}#{node['id']}.on.{event}"
                if target is None:
                    raise UIError(f"{at}: unknown target {action['target']!r}")
                if action["do"] == "set":
                    specs = {**COMPONENTS[target["type"]]["props"], **COMMON}
                    if action["prop"] not in specs:
                        raise UIError(f"{at}: {target['type']} has no prop {action['prop']!r}")
                    if action["prop"] in CONSTRAINT_PROPS.get(target["type"], ()):
                        raise UIError(f"{at}: {target['type']}.{action['prop']} cannot be changed by an event")
                    if "value" in action:
                        # Literal: check the type and the target's cross-prop rules.
                        _prop(specs[action["prop"]], action["value"], f"{at}.value")
                        _props(target["type"], {**target["props"], action["prop"]: action["value"]}, f"{at}.value")
                    else:
                        reason = _assignable(node, event, target, action["prop"])
                        if reason:
                            raise UIError(f"{at}: from_event into {target['type']}.{action['prop']}: {reason}")


def validate_tree(raw: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return the normalised tree and an id -> node index, or raise UIError."""
    index: dict[str, dict[str, Any]] = {}
    root = _node(raw, 1, index, "root")
    _check_targets(index)
    size = json_bytes(root)
    if size > MAX_VIEW_BYTES:
        raise UIError(f"view is too large ({size} bytes as UTF-8 JSON, limit {MAX_VIEW_BYTES})")
    return root, index


def _raw(node: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(node)


def _locate(root: dict[str, Any], node_id: str) -> tuple[dict[str, Any] | None, int]:
    """Return (parent, index) for node_id; (None, -1) for the root."""
    stack = [root]
    while stack:
        parent = stack.pop()
        for i, child in enumerate(parent.get("children") or []):
            if child["id"] == node_id:
                return parent, i
            stack.append(child)
    if root["id"] == node_id:
        return None, -1
    raise UIError(f"unknown component id {node_id!r}")


def _find(root: dict[str, Any], node_id: str) -> dict[str, Any]:
    parent, i = _locate(root, node_id)
    return root if parent is None else parent["children"][i]


def apply_patch(root: dict[str, Any], ops: Any) -> dict[str, Any]:
    """Apply granular ops atomically to a copy of ``root`` and revalidate."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_PATCH_OPS:
        raise UIError(f"ops must be a list of 1..{MAX_PATCH_OPS} operations")
    tree = copy.deepcopy(root)
    for n, op in enumerate(ops):
        if not isinstance(op, dict):
            raise UIError(f"ops[{n}] must be an object")
        kind = op.get("op")
        try:
            if kind == "set_props":
                if set(op) - {"op", "id", "props", "unset"}:
                    raise UIError("set_props accepts id, props and unset")
                node = _find(tree, str(op.get("id")))
                props = op.get("props") or {}
                if not isinstance(props, dict):
                    raise UIError("props must be an object")
                node["props"].update(copy.deepcopy(props))
                for key in op.get("unset") or []:
                    default = {"hidden": False, **COMPONENTS[node["type"]].get("defaults", {})}
                    if key not in default:
                        raise UIError(f"cannot unset unknown prop {key!r}")
                    node["props"][key] = copy.deepcopy(default[key])
            elif kind == "set_on":
                if set(op) - {"op", "id", "on"}:
                    raise UIError("set_on accepts id and on")
                node = _find(tree, str(op.get("id")))
                if op.get("on"):
                    node["on"] = copy.deepcopy(op["on"])
                else:
                    node.pop("on", None)
            elif kind == "insert":
                if set(op) - {"op", "parent", "index", "node"}:
                    raise UIError("insert accepts parent, index and node")
                parent = _find(tree, str(op.get("parent")))
                if "children" not in parent:
                    raise UIError(f"{parent['type']} cannot have children")
                at = op.get("index", len(parent["children"]))
                if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at <= len(parent["children"]):
                    raise UIError("index out of range")
                parent["children"].insert(at, copy.deepcopy(op.get("node")))
            elif kind == "remove":
                if set(op) - {"op", "id"}:
                    raise UIError("remove accepts only id")
                parent, i = _locate(tree, str(op.get("id")))
                if parent is None:
                    raise UIError("cannot remove the root; use loom_ui_close")
                del parent["children"][i]
            elif kind == "move":
                if set(op) - {"op", "id", "parent", "index"}:
                    raise UIError("move accepts id, parent and index")
                parent, i = _locate(tree, str(op.get("id")))
                if parent is None:
                    raise UIError("cannot move the root")
                node = parent["children"].pop(i)
                dest = _find(tree, str(op.get("parent")))  # moving under itself fails: it is detached
                if "children" not in dest:
                    raise UIError(f"{dest['type']} cannot have children")
                at = op.get("index", len(dest["children"]))
                if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at <= len(dest["children"]):
                    raise UIError("index out of range")
                dest["children"].insert(at, node)
            elif kind == "replace":
                if set(op) - {"op", "id", "node"}:
                    raise UIError("replace accepts id and node")
                parent, i = _locate(tree, str(op.get("id")))
                if parent is None:
                    tree = copy.deepcopy(op.get("node"))
                else:
                    parent["children"][i] = copy.deepcopy(op.get("node"))
            else:
                raise UIError("op must be set_props, set_on, insert, remove, move or replace")
        except UIError as error:
            raise UIError(f"ops[{n}] ({kind}): {error}") from None
        if not isinstance(tree, dict):
            raise UIError(f"ops[{n}]: root must stay a component")
    return validate_tree(tree)[0]


def substitute(raw: Any, params: Any) -> Any:
    """Replace ``{{name}}`` in string values with plain-string params."""
    params = params or {}
    if not isinstance(params, dict) or len(params) > 32:
        raise UIError("params must be an object with at most 32 entries")
    for key, value in params.items():
        if not re.match(r"^[a-z_][a-z0-9_]{0,31}$", str(key)) or not isinstance(value, str) or len(value) > 500:
            raise UIError("params must map lowercase names to strings of at most 500 characters")

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            def repl(match: re.Match[str]) -> str:
                if match.group(1) not in params:
                    raise UIError(f"missing template param {match.group(1)!r}")
                return params[match.group(1)]
            return _PARAM_RE.sub(repl, value)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value
    return walk(raw)


class UIViews:
    """Live views, undo/redo stacks and the user event log (backend-owned)."""

    def __init__(self) -> None:
        self.views: dict[str, dict[str, Any]] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self.event_seq = 0

    @staticmethod
    def view_id(value: Any) -> str:
        if not isinstance(value, str) or not _ID_RE.match(value):
            raise UIError(f"view_id must match {_ID_RE.pattern}")
        return value

    def _get(self, view_id: Any) -> dict[str, Any]:
        view = self.views.get(self.view_id(view_id))
        if view is None:
            raise UIError(f"unknown view {view_id!r}")
        return view

    def _check_revision(self, view: dict[str, Any], base: Any) -> None:
        if base is not None and base != view["revision"]:
            raise UIError(f"revision conflict: view is at {view['revision']}, patch was based on {base}")

    def _commit(self, view: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
        view["undo"].append(view["root"])
        del view["undo"][:-UNDO_DEPTH]
        view["redo"] = []
        view["root"] = root
        view["revision"] += 1
        return self.describe(view)

    @staticmethod
    def describe(view: dict[str, Any]) -> dict[str, Any]:
        return {"view_id": view["id"], "title": view["title"], "revision": view["revision"],
                "undo_depth": len(view["undo"]), "redo_depth": len(view["redo"]), "root": _raw(view["root"])}

    def render(self, view_id: Any, tree: Any, title: Any = "", base_revision: Any = None) -> dict[str, Any]:
        view_id = self.view_id(view_id)
        if not isinstance(title, str) or len(title) > 120:
            raise UIError("title must be a string of at most 120 characters")
        root, _ = validate_tree(tree)
        view = self.views.get(view_id)
        if view is None:
            if len(self.views) >= MAX_VIEWS:
                raise UIError(f"at most {MAX_VIEWS} views; close one first")
            view = self.views[view_id] = {"id": view_id, "title": title, "revision": 1, "root": root,
                                          "undo": [], "redo": []}
            return self.describe(view)
        self._check_revision(view, base_revision)
        view["title"] = title or view["title"]
        return self._commit(view, root)

    def patch(self, view_id: Any, ops: Any, base_revision: Any = None) -> dict[str, Any]:
        view = self._get(view_id)
        self._check_revision(view, base_revision)
        return self._commit(view, apply_patch(view["root"], ops))

    def undo(self, view_id: Any, redo: bool = False) -> dict[str, Any]:
        view = self._get(view_id)
        source, sink = (view["redo"], view["undo"]) if redo else (view["undo"], view["redo"])
        if not source:
            raise UIError("nothing to " + ("redo" if redo else "undo"))
        sink.append(view["root"])
        view["root"] = source.pop()
        view["revision"] += 1
        return self.describe(view)

    def close(self, view_id: Any) -> dict[str, Any]:
        self._get(view_id)
        del self.views[view_id]
        return {"closed": view_id}

    def get(self, view_id: Any) -> dict[str, Any]:
        return self.describe(self._get(view_id))

    def snapshot(self) -> list[dict[str, Any]]:
        """Render model for QML: id, title, revision and normalised tree."""
        return [{"id": v["id"], "title": v["title"], "revision": v["revision"], "root": _raw(v["root"])}
                for v in self.views.values()]

    def dispatch(self, view_id: Any, node_id: Any, event: Any, value: Any = None) -> dict[str, Any]:
        """Apply one user interaction: validate, update state, run bound actions."""
        view = self._get(view_id)
        tree = copy.deepcopy(view["root"])
        node = _find(tree, str(node_id))
        spec = COMPONENTS[node["type"]]
        events = spec.get("events") or {}
        if event not in events:
            raise UIError(f"{node['type']} has no '{event}' event")
        if node["props"].get("hidden") or node["props"].get("disabled"):
            raise UIError("component is hidden or disabled")
        value_prop = events[event]
        if value_prop is None:
            value = None
        else:
            if node["type"] == "slider":
                p = node["props"]
                value = _prop(spec["props"]["value"], value, "value")
                value = min(p["max"], max(p["min"], value))
                if p["step"] > 0:
                    value = min(p["max"], p["min"] + round((value - p["min"]) / p["step"]) * p["step"])
            elif node["type"] == "input":
                value = _prop(spec["props"]["value"], value, "value")[: node["props"]["max_length"]]
            elif node["type"] == "select":
                if value not in node["props"]["options"]:
                    raise UIError("value is not one of the select options")
            else:
                value = _prop(spec["props"][value_prop], value, "value")
            node["props"][value_prop] = value
        emitted = []
        for action in (node.get("on") or {}).get(event, []):
            if action["do"] == "emit":
                emitted.append(action["name"])
                continue
            target = _find(tree, action["target"])
            if action["do"] == "toggle":
                target["props"]["hidden"] = not target["props"]["hidden"]
            else:
                specs = {**COMPONENTS[target["type"]]["props"], **COMMON}
                new = value if action.get("from_event") else action["value"]
                target["props"][action["prop"]] = _prop(specs[action["prop"]], new, "set value")
        root, _ = validate_tree(tree)  # a bound set may not break cross-prop rules
        if root != view["root"]:
            view["root"] = root
            view["revision"] += 1
        self.event_seq += 1
        record = {"seq": self.event_seq, "at": round(time.time(), 3), "view_id": view["id"],
                  "node_id": node["id"], "event": event, "value": value, "emitted": emitted,
                  "revision": view["revision"]}
        self.events.append(record)
        return dict(record)

    def read_events(self, since: Any = 0, view_id: Any = None) -> dict[str, Any]:
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise UIError("since must be a non-negative integer")
        out, used, more = [], 0, False
        for e in self.events:
            if e["seq"] <= since or (view_id is not None and e["view_id"] != view_id):
                continue
            used += json_bytes(e) + 1
            if used > MAX_EVENTS_REPLY_BYTES:
                more = True  # keep the reply inside one IPC message; read on from the last seq
                break
            out.append(dict(e))
        return {"events": out, "last_seq": self.event_seq, "more": more}


class TemplateStore(JsonStore):
    """Named, validated view templates in ~/.config/tabby/ui_templates.json."""

    EMPTY = {"version": 1, "templates": {}}

    def __init__(self, path: Path = Path.home() / ".config/tabby/ui_templates.json") -> None:
        super().__init__(path)

    def _valid(self, state: Any) -> bool:
        return isinstance(state, dict) and state.get("version") == 1 and isinstance(state.get("templates"), dict)

    def save(self, name: Any, tree: Any, description: Any = "") -> dict[str, Any]:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise UIError(f"template name must match {_NAME_RE.pattern}")
        if not isinstance(description, str) or len(description) > 300:
            raise UIError("description must be a string of at most 300 characters")
        root, _ = validate_tree(tree)  # placeholders are ordinary strings here
        params = sorted(set(_PARAM_RE.findall(json.dumps(root, ensure_ascii=False))))
        with self._locked():
            state = self._load()
            if name not in state["templates"] and len(state["templates"]) >= MAX_TEMPLATES:
                raise UIError(f"at most {MAX_TEMPLATES} templates")
            old = state["templates"].get(name, {})
            item = {"name": name, "description": description, "params": params, "root": root,
                    "created_at": old.get("created_at", time.time()), "updated_at": time.time()}
            state["templates"][name] = item
            self._save(state)
            return copy.deepcopy(item)

    def get(self, name: Any) -> dict[str, Any]:
        with self._locked():
            item = self._load()["templates"].get(name)
        if item is None:
            raise UIError(f"unknown template {name!r}")
        return copy.deepcopy(item)

    def list(self) -> dict[str, Any]:
        with self._locked():
            items = self._load()["templates"].values()
            return {"templates": [{k: t[k] for k in ("name", "description", "params", "updated_at")} for t in items]}

    def delete(self, name: Any) -> dict[str, Any]:
        with self._locked():
            state = self._load()
            if name not in state["templates"]:
                raise UIError(f"unknown template {name!r}")
            del state["templates"][name]
            self._save(state)
            return {"deleted": name}

    def instantiate(self, name: Any, params: Any = None) -> dict[str, Any]:
        tree = substitute(self.get(name)["root"], params)
        return validate_tree(tree)[0]


def schema_summary() -> dict[str, Any]:
    """Machine-readable component catalogue for loom_ui_schema."""
    catalogue = {}
    for kind, spec in COMPONENTS.items():
        props = {}
        for key, p in {**spec["props"], **COMMON}.items():
            desc = {"str": f"string<={p.get('max')}", "bool": "boolean", "strs": f"string[<={p.get('max')}]",
                    "num": f"number {p.get('lo')}..{p.get('hi')}", "int": f"integer {p.get('lo')}..{p.get('hi')}",
                    "enum": "|".join(p.get("values", ()))}[p["t"]]
            props[key] = desc
        catalogue[kind] = {"container": bool(spec.get("container")), "props": props,
                           "events": {e: v for e, v in (spec.get("events") or {}).items()}}
    return {"components": catalogue, "tones": list(TONES),
            "actions": {"emit": {"name": "lowercase intent name"},
                        "set": {"target": "id", "prop": "prop name", "value": "literal", "from_event": "true"},
                        "toggle": {"target": "id (flips hidden)"}},
            "limits": {"views": MAX_VIEWS, "nodes": MAX_NODES, "depth": MAX_DEPTH, "undo": UNDO_DEPTH,
                       "actions_per_event": MAX_ACTIONS, "ops_per_patch": MAX_PATCH_OPS}}
