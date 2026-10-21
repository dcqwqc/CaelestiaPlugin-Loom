"""Bounded, deterministic desktop actions for Loom.

No generated commands, shell evaluation, or agent execution. This is the local
fast path for a trusted, known active desktop; Philipedia may forward only to
a fixed authenticated desktop bridge. Receipts are written before effects.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
import re
import socket
import sqlite3
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID


@dataclass(frozen=True)
class App:
    desktop_ids: tuple[str, ...]
    window_classes: tuple[str, ...]


APPS = {
    "spotify": App(("com.spotify.Client.desktop", "spotify.desktop"), ("spotify",)),
    "zen": App(("zen.desktop", "app.zen_browser.zen.desktop", "zen-browser.desktop"),
               ("zen", "zen-browser", "app.zen_browser.zen")),
    "firefox": App(("firefox.desktop", "org.mozilla.firefox.desktop"), ("firefox",)),
    "terminal": App(("com.mitchellh.ghostty.desktop", "org.wezfurlong.wezterm.desktop"),
                    ("com.mitchellh.ghostty", "ghostty", "org.wezfurlong.wezterm")),
    "sumi": App(("sumi.desktop", "dev.sumi.app.desktop", "app.sumi.desktop"), ("sumi", "dev.sumi.app")),
}
ALIASES = {"spotify": "spotify", "zen": "zen", "zen browser": "zen", "firefox": "firefox",
           "terminal": "terminal", "ghostty": "terminal", "sumi": "sumi"}
VERBS = re.compile(r"^(?:open|launch|start|öffne|starte|zeige)\s+", re.I)
PAGE = re.compile(r"^(?:sumi://page/|(?:my |mein(?:e|en)? )?sumi (?:page |seite ))([\da-fA-F-]{36})$")
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{1,20}$")
APP_DIRS = (Path.home() / ".local/share/applications",
            Path.home() / ".local/share/flatpak/exports/share/applications",
            Path("/usr/share/applications"), Path("/usr/local/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"))


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def resolve(utterance: str) -> dict:
    """Recognize ONLY bounded user actions. Unknown targets never reach a shell."""
    if not isinstance(utterance, str) or not 1 <= len(utterance) <= 250:
        return {"state": "denied", "reason": "invalid utterance"}
    text = normalize(utterance)
    stem = VERBS.sub("", text, count=1)
    if stem == text:
        return {"state": "not_supported", "reason": "not a known fast-path command"}
    stem = re.sub(r"^(?:(?:the|die|das|den|app|application)\s+){1,2}", "", stem)
    m = PAGE.fullmatch(stem)
    if m:
        try:
            page_id = str(UUID(m.group(1)))
        except (ValueError, AttributeError):
            return {"state": "denied", "reason": "invalid SUMI page identifier"}
        return {"kind": "sumi_page", "target": page_id}
    alias = ALIASES.get(stem)
    if alias:
        return {"kind": "app", "target": alias}
    return {"state": "not_supported", "reason": "unknown or ambiguous target"}


def _run(argv: list[str], timeout: float = 2.0) -> subprocess.CompletedProcess:
    """Resolve the one active graphical compositor without trusting shell env.

    The per-user MCP service often lacks HYPRLAND_INSTANCE_SIGNATURE even
    though Hyprland is live. Never guess between multiple sessions.
    """
    env = os.environ.copy()
    if not env.get("HYPRLAND_INSTANCE_SIGNATURE") or not env.get("WAYLAND_DISPLAY"):
        check = subprocess.run(["hyprctl", "-j", "instances"], check=False,
                               capture_output=True, text=True, timeout=1.0)
        if check.returncode:
            raise OSError("cannot establish active Hyprland instance")
        instances = json.loads(check.stdout)
        if not isinstance(instances, list) or len(instances) != 1:
            raise OSError("ambiguous or absent Hyprland session")
        instance = instances[0]
        if not isinstance(instance, dict) or not instance.get("instance") or not instance.get("wl_socket"):
            raise OSError("invalid Hyprland instance")
        env.update(HYPRLAND_INSTANCE_SIGNATURE=str(instance["instance"]),
                   WAYLAND_DISPLAY=str(instance["wl_socket"]),
                   XDG_RUNTIME_DIR=env.get("XDG_RUNTIME_DIR", "/run/user/" + str(os.getuid())))
    return subprocess.run(argv, check=False, capture_output=True, text=True,
                          timeout=timeout, env=env)


def _clients(run: Callable) -> list[dict]:
    try:
        reply = run(["hyprctl", "-j", "clients"], timeout=1.0)
        data = json.loads(reply.stdout) if reply.returncode == 0 else []
        return data if isinstance(data, list) else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def _active(run: Callable) -> dict:
    try:
        reply = run(["hyprctl", "-j", "activewindow"], timeout=1.0)
        data = json.loads(reply.stdout) if reply.returncode == 0 else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _matches(window: dict, app: App) -> bool:
    cls = normalize(str(window.get("class") or window.get("initialClass") or ""))
    return cls in app.window_classes


def _desktop_file(app: App, paths: tuple[Path, ...] = APP_DIRS) -> Path | None:
    for name in app.desktop_ids:
        for directory in paths:
            path = directory / name
            try:
                if path.is_file() and path.resolve().is_relative_to(directory.resolve()):
                    return path
            except (OSError, RuntimeError):
                continue
    return None


def execute(action: dict, *, run: Callable = _run, clock: Callable = time.monotonic,
            sleep: Callable = time.sleep, paths: tuple[Path, ...] = APP_DIRS) -> dict:
    """Return verified only after observing actual active-window state."""
    started = clock()
    kind, target = action["kind"], action["target"]
    if kind == "sumi_page":
        uri = f"sumi://page/{target}"
        try:
            reply = run(["gio", "open", uri], timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return {"state": "failed", "reason": "SUMI URI handler unavailable"}
        if reply.returncode:
            return {"state": "failed", "reason": "SUMI URI dispatch rejected"}
        return {"state": "dispatched_unverified", "reason": "exact SUMI page not yet independently observed",
                "kind": kind, "target": target, "elapsed_ms": round((clock() - started) * 1000)}
    if kind != "app" or target not in APPS:
        return {"state": "denied", "reason": "unallowlisted action"}
    app = APPS[target]
    existing = next((w for w in _clients(run) if _matches(w, app) and ADDRESS.fullmatch(str(w.get("address", "")))), None)
    if existing is not None:
        address = existing["address"]
        try:
            # Hyprland 0.55+ uses Lua dispatcher objects, not legacy text.
            # ADDRESS is constrained to hex by the existing-window filter.
            lua = f'hl.dsp.focus({{ window = "address:{address}" }})'
            outcome = run(["hyprctl", "dispatch", lua], timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            return {"state": "failed", "reason": "window focus request failed"}
        if outcome.returncode:
            return {"state": "failed", "reason": "window focus request rejected"}
    else:
        launch_file = _desktop_file(app, paths)
        if launch_file is None:
            return {"state": "not_found", "reason": "allowlisted application not installed"}
        try:
            outcome = run(["gio", "launch", str(launch_file)], timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return {"state": "failed", "reason": "launcher unavailable"}
        if outcome.returncode:
            return {"state": "failed", "reason": "desktop launch rejected"}
    # Check observable compositor state, not process exit. A delayed GUI is pending.
    for attempt in range(4):
        if _matches(_active(run), app):
            return {"state": "verified", "kind": kind, "target": target,
                    "elapsed_ms": round((clock() - started) * 1000)}
        if attempt < 3:
            sleep(.12)
    return {"state": "dispatched_unverified", "kind": kind, "target": target,
            "reason": "target was not observed as focused",
            "elapsed_ms": round((clock() - started) * 1000)}


class FastPath:
    """Idempotent side-effect admission. Unknown outcomes are never replayed."""
    def __init__(self, db_path: Path, *, hostname: Callable[[], str] = socket.gethostname):
        self.db_path = Path(db_path)
        self.hostname = hostname
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path.touch(mode=0o600, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS receipts (
                request_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT NOT NULL)""")

    def run(self, request_id: str, utterance: str, device: str, *,
            executor: Callable = execute) -> dict:
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9:._-]{6,160}", request_id):
            return {"state": "denied", "reason": "stable request_id required"}
        action = resolve(utterance)
        if "kind" not in action:
            return action
        local = normalize(self.hostname())
        if device not in ("active", local):
            return {"state": "device_unavailable", "reason": "requested device is not this client"}
        # No guessing of "active" on a headless server.
        if local not in ("mirai", "tachibana"):
            return {"state": "device_unavailable", "reason": "no trusted active desktop"}
        digest = hashlib.sha256(json.dumps((action, local), sort_keys=True).encode()).hexdigest()
        with closing(sqlite3.connect(self.db_path, timeout=3)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest,state,result FROM receipts WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if row[0] != digest:
                    return {"state": "denied", "reason": "request_id reused with different action"}
                if row[1] == "started":
                    return {"state": "uncertain_outcome", "reason": "prior attempt may have acted; do not replay"}
                return dict(json.loads(row[2]), replayed=True)
            db.execute("INSERT INTO receipts(request_id,digest,state,result) VALUES(?,?,?,?)",
                       (request_id, digest, "started", "{}"))
        try:
            result = executor(action)
        except Exception:
            result = {"state": "uncertain_outcome", "reason": "executor failed; reconcile before retry"}
        if not isinstance(result, dict) or not isinstance(result.get("state"), str):
            result = {"state": "uncertain_outcome", "reason": "invalid executor receipt"}
        result = dict(result, device=local, request_id=request_id)
        with closing(sqlite3.connect(self.db_path, timeout=3)) as db, db:
            db.execute("UPDATE receipts SET state=?,result=? WHERE request_id=?",
                       (result["state"], json.dumps(result, sort_keys=True), request_id))
        return result


def local_request(args: dict) -> dict:
    """An explicit per-client call; never infer a desktop from Philipedia."""
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    router = FastPath(state / "loom" / "fastpath-receipts.sqlite3")
    return router.run(args.get("request_id"), args.get("utterance"), args.get("device", "active"))
