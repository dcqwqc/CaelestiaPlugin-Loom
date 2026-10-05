"""Validation for Tabby's generic display items.

Every widget on Tabby's board is a plain JSON item with a ``type``. Known types
get a dedicated renderer in Panel.qml; any other well-formed type is still kept
(and rendered with the generic title/text fallback), so a new widget only needs
a QML delegate, never a new MCP server or IPC command.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

MAX_ITEMS = 32
MAX_ITEM_BYTES = 8 * 1024
MAX_TEXT = 4000
MAX_SHORT = 160
MAX_OPTIONS = 6
MAX_ENTRIES = 12

KNOWN_TYPES = {"text", "progress", "status", "choice", "shape", "card", "list", "divider"}
SHAPE_KINDS = {"line", "arrow", "rect", "circle"}
TONES = {"neutral", "primary", "secondary", "tertiary", "success", "warning", "error"}
_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]")


def _text(value: Any, limit: int = MAX_SHORT) -> str:
    return str(value if value is not None else "")[:limit]


def _num(value: Any, default: float = 0.0, lo: float | None = None, hi: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = default
    if out != out:  # NaN
        out = default
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def item_id(value: Any = None) -> str:
    cleaned = _ID_RE.sub("", str(value or ""))[:64]
    return cleaned or uuid.uuid4().hex[:12]


def _plain(value: Any, depth: int = 0) -> Any:
    """JSON-safe copy of an unknown widget's extra fields, size bounded."""
    if depth > 3:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _num(value)
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in list(value)[:MAX_ENTRIES * 2]]
    if isinstance(value, dict):
        return {str(k)[:48]: _plain(v, depth + 1) for k, v in list(value.items())[:24]}
    return str(value)[:MAX_SHORT]


def sanitize_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("display item must be an object")
    kind = str(raw.get("type") or "").strip().lower()
    if not _TYPE_RE.match(kind):
        raise ValueError("display item needs a lowercase 'type'")
    item: dict[str, Any] = {"type": kind, "id": item_id(raw.get("id"))}
    tone = str(raw.get("tone") or "").lower()
    if tone in TONES:
        item["tone"] = tone

    if kind == "text":
        item["title"] = _text(raw.get("title"))
        item["text"] = _text(raw.get("text"), MAX_TEXT)
    elif kind == "progress":
        item["label"] = _text(raw.get("label"))
        item["value"] = _num(raw.get("value"), 0.0, 0.0, 1.0)
    elif kind == "status":
        item["label"] = _text(raw.get("label") or raw.get("text"))
        state = str(raw.get("state") or "info").lower()
        item["state"] = state if state in {"info", "working", "success", "warning", "error"} else "info"
    elif kind == "choice":
        options = raw.get("options") or []
        if not isinstance(options, list) or not options:
            raise ValueError("choice needs a non-empty 'options' list")
        item["label"] = _text(raw.get("label"))
        item["options"] = [_text(o, 60) for o in options[:MAX_OPTIONS] if str(o).strip()]
        if not item["options"]:
            raise ValueError("choice needs at least one non-empty option")
        selected = raw.get("selected")
        item["selected"] = _text(selected, 60) if selected in item["options"] else ""
    elif kind == "shape":
        shape = str(raw.get("kind") or "rect").lower()
        if shape not in SHAPE_KINDS:
            raise ValueError(f"shape kind must be one of {sorted(SHAPE_KINDS)}")
        item["kind"] = shape
        for key in ("x", "y", "w", "h"):
            item[key] = _num(raw.get(key), 0.0, -4096.0, 4096.0)
        item["label"] = _text(raw.get("label"))
        item["filled"] = bool(raw.get("filled", False))
    elif kind == "card":
        item["title"] = _text(raw.get("title"))
        item["body"] = _text(raw.get("body") or raw.get("text"), MAX_TEXT)
        item["badge"] = _text(raw.get("badge"), 40)
        if raw.get("progress") is not None:
            item["progress"] = _num(raw.get("progress"), 0.0, 0.0, 1.0)
    elif kind == "list":
        entries = raw.get("entries") or raw.get("items") or []
        if not isinstance(entries, list):
            raise ValueError("list needs an 'entries' array")
        item["title"] = _text(raw.get("title"))
        item["entries"] = [_text(e, 200) for e in entries[:MAX_ENTRIES]]
        item["ordered"] = bool(raw.get("ordered", False))
    elif kind == "divider":
        item["label"] = _text(raw.get("label"))
    else:
        # Future widget: keep its fields generically; Panel.qml renders the
        # title/text fallback until a dedicated delegate exists.
        for key, value in raw.items():
            if key in {"type", "id", "tone"}:
                continue
            item[str(key)[:48]] = _plain(value)
        item.setdefault("title", "")
        item.setdefault("text", "")

    if len(json.dumps(item, ensure_ascii=False)) > MAX_ITEM_BYTES:
        raise ValueError("display item is too large")
    return item


def upsert(items: list[dict[str, Any]], raw: Any, merge: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Insert ``raw`` or update the item with the same id.

    With ``merge`` an update keeps fields the caller did not send, so
    ``{"id": "build", "type": "progress", "value": .6}`` moves a bar without
    resetting its label. The item's type may be omitted on updates.
    """
    if not isinstance(raw, dict):
        raise ValueError("display item must be an object")
    out = list(items)
    wanted = str(raw.get("id") or "")
    for index, existing in enumerate(out):
        if wanted and existing.get("id") == item_id(wanted):
            same_type = not raw.get("type") or str(raw.get("type")).lower() == existing.get("type")
            combined = {**existing, **raw} if merge and same_type else dict(raw)
            combined.setdefault("type", existing.get("type"))
            if "options" in raw and "selected" not in raw:
                combined.pop("selected", None)  # a new question resets its answer
            item = sanitize_item(combined)
            out[index] = item
            return out, item
    item = sanitize_item(raw)
    out.append(item)
    return out[-MAX_ITEMS:], item
