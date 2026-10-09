"""ChatGPT project lifecycle names and exact project-identity checks.

Loom files ChatGPT conversations into five user-created ChatGPT projects. There
is no ChatGPT API for projects: ids come only from links the ChatGPT web UI
renders, and a move counts only when the conversation's own route proves it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

LIFECYCLES = ("new", "vault", "working", "blocked", "done")
DEFAULT_PROJECT_NAMES = {"new": "New", "vault": "Vault", "working": "Working",
                         "blocked": "Blocked", "done": "Done"}
CONFIG_PATH = Path.home() / ".config" / "tabby" / "chatgpt-projects.json"

_CORE_ID = re.compile(r"^(g-p-[0-9a-f]{32})(?:-|$)", re.IGNORECASE)


def load_project_names(path: Path | None = None) -> dict[str, str]:
    """Lifecycle -> ChatGPT project name, with optional user overrides.

    The file may contain {"projects": {"working": "Loom Working", ...}}; unknown
    keys and blank names are ignored so a bad edit cannot drop a lifecycle.
    """
    names = dict(DEFAULT_PROJECT_NAMES)
    try:
        raw = json.loads(Path(path or CONFIG_PATH).read_text())
    except (OSError, ValueError):
        return names
    overrides = raw.get("projects") if isinstance(raw, dict) else None
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in names and str(value or "").strip():
                names[key] = str(value).strip()[:160]
    return names


def project_core_id(segment) -> str:
    """Stable project id: the `g-p-<32 hex>` prefix of a route segment.

    The trailing slug mirrors the display name and changes on rename, so it is
    not part of the identity. Segments without that shape are compared whole.
    """
    raw = str(segment or "").strip()
    match = _CORE_ID.match(raw)
    return match.group(1).lower() if match else raw


def chat_route(url) -> dict | None:
    """Parse a canonical conversation URL into conversation and project ids."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or parsed.netloc != "chatgpt.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) == 2 and parts[0] == "c":
        conversation, segment = parts[1], ""
    elif len(parts) == 4 and parts[0] == "g" and parts[2] == "c":
        conversation, segment = parts[3], parts[1]
    else:
        return None
    if not conversation or conversation.startswith("local-chatgpt"):
        return None
    return {"conversationId": conversation, "projectSegment": segment,
            "projectId": project_core_id(segment) if segment else ""}


def _same_name(left, right) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def resolve_project(projects, name) -> tuple[dict | None, str]:
    """Find exactly one discovered project by visible name.

    Returns (project, "") or (None, reason). Two projects sharing a name are
    refused: the UI cannot tell them apart, and guessing could file work into
    the wrong project.
    """
    hits = [p for p in (projects or []) if _same_name(p.get("name"), name) and p.get("id")]
    if not hits:
        return None, f"ChatGPT project not found: {name}"
    ids = {project_core_id(p.get("id")) for p in hits}
    if len(ids) > 1:
        return None, f"ChatGPT project name is ambiguous: {name}"
    hit = dict(hits[0])
    hit["id"] = project_core_id(hit["id"])
    hit.setdefault("segment", hits[0].get("id"))
    return hit, ""


def resolve_all(projects, names: dict[str, str]) -> tuple[dict[str, dict], list[str]]:
    resolved, errors = {}, []
    for lifecycle in LIFECYCLES:
        hit, error = resolve_project(projects, names[lifecycle])
        if hit:
            resolved[lifecycle] = hit
        else:
            errors.append(error)
    return resolved, errors
