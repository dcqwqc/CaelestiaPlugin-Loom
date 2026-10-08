"""Durable, versioned Loom module and space registry.

The registry models requested placements. The shell supplies board rendering and a
native floating host for its reserved Performance tasks tile; other module/surface
combinations remain explicit pending cases.
"""
from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config/tabby/modules.json"
KINDS = {"text", "tasks", "memory", "cpu", "storage", "battery", "weather"}
SURFACES = {"board", "performance", "floating"}
ANCHORS = {"free", "top-left", "top-right", "bottom-left", "bottom-right", "center"}
DEFAULT_PLACEMENT = {"surface": "board", "anchor": "free", "x": 0, "y": 0,
                     "width": 340, "height": 220, "monitor": "", "workspace": ""}


class SpaceError(ValueError):
    pass


def _short(value, limit=160):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SpaceError("expected a non-empty string of at most %d characters" % limit)
    return value.strip()


def _safe_data(value):
    if not isinstance(value, dict):
        raise SpaceError("data must be an object")
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf8")) > 16_384:
        raise SpaceError("module data exceeds 16 KiB")
    return json.loads(encoded)


def validate_placement(obj):
    if obj is None:
        obj = {}
    if not isinstance(obj, dict):
        raise SpaceError("placement must be an object")
    allowed = set(DEFAULT_PLACEMENT)
    if set(obj) - allowed:
        raise SpaceError("unknown placement fields")
    p = {**DEFAULT_PLACEMENT, **obj}
    if p["surface"] not in SURFACES or p["anchor"] not in ANCHORS:
        raise SpaceError("invalid surface or anchor")
    for key in ("x", "y", "width", "height"):
        value = p[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SpaceError("%s must be a finite number" % key)
        if key in ("width", "height") and not 80 <= value <= 4096:
            raise SpaceError("%s must be between 80 and 4096" % key)
        if key in ("x", "y") and abs(value) > 100000:
            raise SpaceError("%s exceeds coordinate limit" % key)
    for key in ("monitor", "workspace"):
        if not isinstance(p[key], str) or len(p[key]) > 128:
            raise SpaceError("invalid %s" % key)
    return p


class SpaceStore:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            with os.fdopen(fd, "r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield
        finally:
            pass

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "modules": {}, "spaces": {}, "requests": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf8"))
            if (state.get("version") != 1
                or not all(isinstance(state.get(k), dict) for k in ("modules", "spaces", "requests"))):
                raise SpaceError("unsupported or malformed registry")
            return state
        except (OSError, ValueError, TypeError) as exc:
            raise SpaceError("registry unreadable; refusing to overwrite: %s" % exc) from exc

    def _save(self, state):
        temp = self.path.with_name(self.path.name + "." + secrets.token_hex(6) + ".tmp")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf8") as out:
                json.dump(state, out, ensure_ascii=False, indent=2, allow_nan=False)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
            dfd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            if temp.exists():
                temp.unlink()

    def list(self):
        with self._locked():
            state = self._load()
            return {"modules": list(state["modules"].values()), "spaces": list(state["spaces"].values()),
                    "version": state["version"]}

    def get_module(self, module_id):
        with self._locked():
            obj = self._load()["modules"].get(module_id)
            if obj is None:
                raise SpaceError("unknown module")
            return copy.deepcopy(obj)

    def module_for_request(self, request_id):
        """Return the module created under request_id, or None if absent/deleted."""
        with self._locked():
            state = self._load()
            req = state["requests"].get(request_id)
            module = state["modules"].get(req["id"]) if req else None
            return copy.deepcopy(module) if module else None

    def create_module(self, *, kind, title, data=None, placement=None, visible=True, request_id=None):
        if kind not in KINDS:
            raise SpaceError("unsupported module kind")
        title = _short(title)
        data = _safe_data(data if data is not None else {})
        placement = validate_placement(placement)
        if not isinstance(visible, bool):
            raise SpaceError("visible must be boolean")
        if request_id is not None:
            request_id = _short(request_id, 120)
        signature = json.dumps([kind, title, data, placement, visible], sort_keys=True)
        with self._locked():
            state = self._load()
            if request_id and request_id in state["requests"]:
                req = state["requests"][request_id]
                if req["signature"] != signature:
                    raise SpaceError("request_id reused with different payload")
                return copy.deepcopy(state["modules"][req["id"]])
            module_id = secrets.token_hex(12)
            now = time.time()
            item = {"id": module_id, "kind": kind, "title": title, "data": data,
                    "placement": placement, "visible": visible,
                    "created_at": now, "updated_at": now}
            state["modules"][module_id] = item
            if request_id:
                state["requests"][request_id] = {"id": module_id, "signature": signature}
            self._save(state)
            return copy.deepcopy(item)

    def update_module(self, module_id, *, title=None, data=None, placement=None, visible=None):
        with self._locked():
            state = self._load()
            if module_id not in state["modules"]:
                raise SpaceError("unknown module")
            item = state["modules"][module_id]
            if title is not None:
                item["title"] = _short(title)
            if data is not None:
                item["data"] = _safe_data(data)
            if placement is not None:
                item["placement"] = validate_placement({**item["placement"], **placement})
            if visible is not None:
                if not isinstance(visible, bool):
                    raise SpaceError("visible must be boolean")
                item["visible"] = visible
            item["updated_at"] = time.time()
            self._save(state)
            return copy.deepcopy(item)

    def delete_module(self, module_id):
        with self._locked():
            state = self._load()
            if module_id not in state["modules"]:
                raise SpaceError("unknown module")
            del state["modules"][module_id]
            state["requests"] = {k:v for k,v in state["requests"].items() if v["id"] != module_id}
            for space in state["spaces"].values():
                space["module_ids"] = [mid for mid in space["module_ids"] if mid != module_id]
            self._save(state)
            return {"deleted": module_id}

    def save_space(self, *, name, module_ids, space_id=None):
        name = _short(name)
        if not isinstance(module_ids, list) or len(module_ids) > 64 or any(not isinstance(v, str) for v in module_ids):
            raise SpaceError("module_ids must be a list of up to 64 IDs")
        if len(set(module_ids)) != len(module_ids):
            raise SpaceError("duplicate module ID")
        with self._locked():
            state = self._load()
            if not set(module_ids).issubset(state["modules"]):
                raise SpaceError("space references unknown module")
            if space_id is not None and space_id not in state["spaces"]:
                raise SpaceError("unknown space")
            space_id = space_id or secrets.token_hex(12)
            old = state["spaces"].get(space_id, {})
            space = {"id": space_id, "name": name, "module_ids": list(module_ids),
                     "created_at": old.get("created_at", time.time()), "updated_at": time.time()}
            state["spaces"][space_id] = space
            self._save(state)
            return copy.deepcopy(space)

    def get_space(self, space_id):
        with self._locked():
            state = self._load()
            space = state["spaces"].get(space_id)
            if space is None:
                raise SpaceError("unknown space")
            return {"space": copy.deepcopy(space),
                    "modules": [copy.deepcopy(state["modules"][mid])
                                for mid in space["module_ids"] if mid in state["modules"]]}

    def delete_space(self, space_id):
        with self._locked():
            state = self._load()
            if space_id not in state["spaces"]:
                raise SpaceError("unknown space")
            del state["spaces"][space_id]
            self._save(state)
            return {"deleted": space_id}
