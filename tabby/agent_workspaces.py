"""Loom Agent Input / Workspace Service core.

Every graphical AI worker gets an isolated *agent workspace*: a private Xvfb
display (own seat, pointer, keyboard focus, clipboard) with no Wayland,
Hyprland, PipeWire/Pulse or session-bus connection back to the user's desktop.
Input is injected with XTEST into that private display only, so it can never
move the user's physical cursor, take their keyboard focus, switch their
workspace or touch a running Zen/Voice session.

Kinds:
  desktop  a private display for arbitrary X11-capable apps (``launch``)
  browser  the same, running a private Firefox profile with WebDriver BiDi on
           loopback so agents can act on CSS selectors / visible text instead
           of raw pixels

Anything that cannot be isolated this way (for example injecting into the
user's real Hyprland session) is reported as unavailable: the service fails
closed rather than falling back to the shared seat.

The class is transport-free; ``loom_agent_input.py`` serves it on a mode-0600
Unix socket and ``loom_mcp.py`` exposes it as ``loom_gui_*`` MCP tools.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from tabby import bidi

REPO_ROOT = Path(__file__).resolve().parent.parent

# Soft pastels, chosen to read on both light and dark wallpapers.
PALETTE: list[tuple[str, str]] = [
    ("lavender", "#C9B8FF"), ("mint", "#A8E6CF"), ("peach", "#FFCBA4"), ("rose", "#F7B2C8"),
    ("sky", "#A7D8F5"), ("butter", "#FCE9A0"), ("lilac", "#E3B9E8"), ("sage", "#C5DDB0"),
    ("coral", "#FFB3A7"), ("aqua", "#9FE5E0"),
]
PALETTE_BY_NAME = dict(PALETTE)

KINDS = ("desktop", "browser")
ACTIVE_STATES = {"starting", "ready", "paused"}
MAX_TEXT = 8000
MAX_LAUNCH_ARGS = 64


class GuiError(Exception):
    """Predictable failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "error": str(self)}


# --------------------------------------------------------------------- paths

def _env_path(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else default


def runtime_dir() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    return _env_path("LOOM_AGENT_INPUT_RUNTIME", base / "loom" / "agent-input")


def state_dir() -> Path:
    return _env_path("LOOM_AGENT_INPUT_STATE", Path.home() / ".local/state/loom/agent-input")


def config_path() -> Path:
    return _env_path("LOOM_AGENT_INPUT_CONFIG", Path.home() / ".config/loom/agent-input.json")


def overlay_path() -> Path:
    return runtime_dir().parent / "agent-cursors.json"


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "allow_remote_mcp": False,       # ChatGPT/HTTP MCP clients get no GUI tools unless enabled
    "max_workspaces": 6,
    "default_size": "1280x800",
    "owner_grace_seconds": 90,       # release after the owning agent process is gone this long
    "idle_release_minutes": 45,      # release workspaces nobody has touched (no owner pid)
    "cursor_idle_seconds": 8,        # overlay hides an agent's pointer after this much inactivity
    "browser_command": "firefox",
    "agents": {},                    # agent_id -> {color, name, chosen}
    "hidden_agents": [],             # agent ids the user hid from the overlay
}


def load_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy: nested dicts must not be shared
    data = _read_json(config_path(), {})
    if isinstance(data, dict):
        cfg.update(data)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    _atomic_write(config_path(), json.dumps(cfg, indent=2, sort_keys=True) + "\n")


def parse_size(value: Any, fallback: str = "1280x800") -> tuple[int, int]:
    try:
        w, h = (int(p) for p in str(value or fallback).lower().split("x", 1))
    except ValueError:
        w, h = (int(p) for p in fallback.split("x", 1))
    return max(640, min(3840, w)), max(400, min(2160, h))


def normalize_color(value: str) -> str:
    raw = str(value or "").strip()
    if raw.lower() in PALETTE_BY_NAME:
        return PALETTE_BY_NAME[raw.lower()]
    hexpart = raw.lstrip("#")
    if len(hexpart) == 6 and all(c in "0123456789abcdefABCDEF" for c in hexpart):
        return "#" + hexpart.upper()
    raise GuiError("invalid", f"color must be a palette name ({', '.join(PALETTE_BY_NAME)}) or #RRGGBB")


def assign_color(agent_id: str, taken: list[str]) -> str:
    """Stable first choice per agent id, moving to the next free pastel on collision."""
    start = int(hashlib.sha1(agent_id.encode()).hexdigest(), 16) % len(PALETTE)
    for i in range(len(PALETTE)):
        color = PALETTE[(start + i) % len(PALETTE)][1]
        if color not in taken:
            return color
    return PALETTE[start][1]


def display_name(agent_id: str) -> str:
    low = agent_id.lower()
    for key, name in (("claude", "Claude"), ("codex", "Codex"), ("chatgpt", "ChatGPT"),
                      ("agy", "Agy"), ("antigravity", "Agy"), ("loom", "Loom")):
        if key in low:
            suffix = agent_id.rsplit("-", 1)[-1] if "-" in agent_id else ""
            return f"{name} {suffix[:4]}".strip()
    return agent_id[:18]


# --------------------------------------------------------------------- runtime

def pid_alive(pid: int) -> bool:
    if not pid or pid <= 1:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def pid_cmdline(pid: int) -> list[str]:
    try:
        return [p.decode(errors="replace") for p in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if p]
    except OSError:
        return []


def write_xauthority(path: Path, cookie: bytes) -> None:
    """FamilyWild entry: matches any host/display number, so the same file works
    for the server (-auth) and every client of this one private display."""
    def field(b: bytes) -> bytes:
        return len(b).to_bytes(2, "big") + b
    entry = (0xFFFF).to_bytes(2, "big") + field(b"") + field(b"") + field(b"MIT-MAGIC-COOKIE-1") + field(cookie)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(entry)


class _Children:
    """Detached children (displays, brokers, apps) must still be reaped by the
    long-running service, or every exited one lingers as a zombie."""

    def __init__(self) -> None:
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()

    def track(self, proc: subprocess.Popen) -> subprocess.Popen:
        with self._lock:
            self._procs.append(proc)
        return proc

    def reap(self) -> None:
        with self._lock:
            self._procs = [p for p in self._procs if p.poll() is None]


CHILDREN = _Children()


class Helper:
    """JSON-lines client for one workspace broker (``tabby.xinput``) over its socket.

    The broker is a separate long-lived process: it survives service restarts
    (so the browser's BiDi session does too) and an X I/O error kills only it."""

    def __init__(self, sock_path: Path, env: dict[str, str] | None = None):
        self.path = sock_path
        self.pid = 0
        if env is not None and not self._connect(quiet=True):
            self._spawn(env)
        if not self._connect():
            raise GuiError("crashed", "agent display broker is not reachable")
        hello = self.call("hello", timeout=5)
        self.width, self.height = int(hello["width"]), int(hello["height"])

    def _spawn(self, env: dict[str, str]) -> None:
        with open(self.path.with_name("broker.log"), "ab") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "tabby.xinput", "serve", str(self.path)], cwd=str(REPO_ROOT), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                text=True, start_new_session=True)
        assert proc.stdout is not None
        ready, _, _ = select.select([proc.stdout], [], [], 8.0)
        line = proc.stdout.readline() if ready else ""
        proc.stdout.close()
        try:
            hello = json.loads(line or "{}")
        except ValueError:
            hello = {}
        if not hello.get("ok"):
            try:
                proc.kill()
            except OSError:
                pass
            raise GuiError("unavailable", f"agent display broker failed: {hello.get('error', 'no reply')}")
        self.pid = proc.pid
        CHILDREN.track(proc)

    def _connect(self, quiet: bool = False) -> bool:
        import socket
        self.sock = None
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(3)
            s.connect(str(self.path))
        except OSError:
            s.close()
            return False
        self.sock = s
        self.file = s.makefile("rw", encoding="utf-8", newline="\n")
        return True

    def call(self, op: str, timeout: float = 10.0, **args: Any) -> dict[str, Any]:
        import socket
        if self.sock is None:
            raise GuiError("crashed", "agent display connection was lost")
        try:
            self.sock.settimeout(timeout)
            self.file.write(json.dumps({"op": op, **args}) + "\n")
            self.file.flush()
            line = self.file.readline()
        except socket.timeout as exc:
            # The broker may still be executing the request; drop the connection
            # so a late reply can never be mistaken for the next request's.
            self.close()
            raise GuiError("timeout", f"{op} did not finish within {timeout:.0f}s") from exc
        except OSError as exc:
            self.close()
            raise GuiError("crashed", "agent display connection was lost") from exc
        if not line:
            self.close()
            raise GuiError("crashed", "agent display connection was lost")
        result = json.loads(line)
        if not result.get("ok"):
            raise GuiError("failed", str(result.get("error") or op + " failed"))
        return result

    def alive(self) -> bool:
        return self.sock is not None

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.file.close()
                self.sock.close()
            except OSError:
                pass
        self.sock = None


class ProcessRuntime:
    """Real processes. Tests substitute a fake with the same methods."""

    def __init__(self) -> None:
        self.xvfb = shutil.which("Xvfb")
        self.dbus_run = shutil.which("dbus-run-session")

    def capabilities(self, cfg: dict[str, Any]) -> dict[str, Any]:
        browser = shutil.which(str(cfg.get("browser_command") or "firefox"))
        return {
            "desktop": {"available": bool(self.xvfb), "isolation": "private Xvfb display + XTEST",
                        "reason": "" if self.xvfb else "Xvfb is not installed"},
            "browser": {"available": bool(self.xvfb and browser), "engine": "firefox (WebDriver BiDi, loopback)",
                        "reason": "" if (self.xvfb and browser) else "Xvfb or Firefox is not installed"},
            "user_desktop_input": {"available": False,
                                   "reason": "Hyprland has a single seat: injected input would move the user's "
                                             "pointer and focus. Not offered (fail closed)."},
            "accessibility_tree": {"available": False,
                                   "reason": "AT-SPI is not wired into private displays yet; use browser selectors "
                                             "or screenshots + coordinates."},
        }

    def ensure_dirs(self, ws: dict[str, Any]) -> dict[str, Path]:
        root = runtime_dir() / ws["id"]
        run = root / "run"
        for p in (root, run):
            p.mkdir(parents=True, exist_ok=True)
            os.chmod(p, 0o700)
        prof = state_dir() / "profiles" / ws["id"]
        return {"root": root, "run": run, "xauth": root / "Xauthority", "profile": prof}

    def start_display(self, ws: dict[str, Any]) -> dict[str, Any]:
        if not self.xvfb:
            raise GuiError("unavailable", "Xvfb is not installed")
        dirs = self.ensure_dirs(ws)
        write_xauthority(dirs["xauth"], secrets.token_bytes(16))
        r, w = os.pipe()
        cmd = [self.xvfb, "-displayfd", str(w), "-screen", "0", f"{ws['width']}x{ws['height']}x24",
               "-nolisten", "tcp", "-auth", str(dirs["xauth"]),
               "-noreset", "+extension", "XTEST"]
        proc = CHILDREN.track(subprocess.Popen(
            cmd, pass_fds=(w,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, env={**self.app_env(ws, dirs, display=""), "LOOM_AGENT_WORKSPACE": ws["id"]}))
        os.close(w)
        number = b""
        deadline = time.monotonic() + 8
        try:
            while time.monotonic() < deadline and not number.endswith(b"\n"):
                ready, _, _ = select.select([r], [], [], 0.2)
                if ready:
                    chunk = os.read(r, 32)
                    if not chunk:
                        break
                    number += chunk
                if proc.poll() is not None:
                    break
        finally:
            os.close(r)
        if not number.strip().isdigit():
            try:
                proc.kill()
            except OSError:
                pass
            raise GuiError("unavailable", "the private display did not start")
        return {"display": f":{number.strip().decode()}", "xvfb_pid": proc.pid, "xauth": str(dirs["xauth"])}

    def app_env(self, ws: dict[str, Any], dirs: dict[str, Path], display: str | None = None) -> dict[str, str]:
        """A deliberately small environment. No WAYLAND_DISPLAY, no Hyprland
        signature, and a private XDG_RUNTIME_DIR, so apps cannot reach the user's
        compositor, PipeWire/Pulse (audio, microphone, Voice) or portals."""
        keep = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "SHELL", "TZ")
        env = {k: os.environ[k] for k in keep if k in os.environ}
        env.update({
            "DISPLAY": ws.get("display", "") if display is None else display,
            "XAUTHORITY": str(dirs["xauth"]),
            "XDG_RUNTIME_DIR": str(dirs["run"]),
            "XDG_SESSION_TYPE": "x11",
            "GDK_BACKEND": "x11", "QT_QPA_PLATFORM": "xcb", "SDL_VIDEODRIVER": "x11",
            "MOZ_ENABLE_WAYLAND": "0", "ELECTRON_OZONE_PLATFORM_HINT": "x11",
            "PULSE_SERVER": "unix:/nonexistent/loom-agent-no-audio",
            "PIPEWIRE_REMOTE": "loom-agent-no-audio",
            "NO_AT_BRIDGE": "1",
            "LOOM_AGENT_WORKSPACE": ws["id"],
            "LOOM_AGENT_ID": str(ws.get("owner", "")),
        })
        return env

    def spawn_app(self, ws: dict[str, Any], argv: list[str]) -> int:
        exe = shutil.which(argv[0]) if argv else None
        if not exe:
            raise GuiError("invalid", f"command not found: {argv[0] if argv else ''}")
        dirs = self.ensure_dirs(ws)
        full = ([self.dbus_run, "--"] if self.dbus_run else []) + [exe, *argv[1:]]
        log = open(dirs["root"] / "apps.log", "ab")
        try:
            proc = CHILDREN.track(subprocess.Popen(
                full, env=self.app_env(ws, dirs), cwd=str(Path.home()),
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True))
        finally:
            log.close()
        return proc.pid

    def spawn_browser(self, ws: dict[str, Any], cfg: dict[str, Any], url: str) -> dict[str, Any]:
        exe = shutil.which(str(cfg.get("browser_command") or "firefox"))
        if not exe:
            raise GuiError("unavailable", "Firefox is not installed")
        dirs = self.ensure_dirs(ws)
        dirs["profile"].mkdir(parents=True, exist_ok=True)
        os.chmod(dirs["profile"], 0o700)
        prefs = {
            "browser.shell.checkDefaultBrowser": False, "browser.aboutwelcome.enabled": False,
            "datareporting.policy.dataSubmissionEnabled": False, "toolkit.telemetry.reportingpolicy.firstRun": False,
            "browser.startup.homepage_override.mstone": "ignore", "browser.sessionstore.resume_from_crash": False,
            "browser.tabs.warnOnClose": False, "browser.warnOnQuit": False, "media.autoplay.default": 5,
            "app.update.disabledForTesting": True, "remote.prefs.recommended": True,
            "browser.startup.page": 0, "startup.homepage_welcome_url": "",
        }
        (dirs["profile"] / "user.js").write_text(
            "".join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        port = _free_port()
        argv = [exe, "--no-remote", "--new-instance", "--profile", str(dirs["profile"]),
                "--remote-debugging-port", str(port), "--width", str(ws["width"]),
                "--height", str(ws["height"]), url or "about:blank"]
        full = ([self.dbus_run, "--"] if self.dbus_run else []) + argv
        log = open(dirs["root"] / "browser.log", "ab")
        try:
            proc = CHILDREN.track(subprocess.Popen(
                full, env=self.app_env(ws, dirs), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True))
        finally:
            log.close()
        return {"browser_pid": proc.pid, "bidi_port": port}

    def helper(self, ws: dict[str, Any], spawn: bool = True) -> Helper:
        dirs = self.ensure_dirs(ws)
        env = dict(os.environ)
        for k in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"):
            env.pop(k, None)
        env.update(DISPLAY=ws["display"], XAUTHORITY=str(dirs["xauth"]),
                   PYTHONPATH=str(REPO_ROOT), LOOM_AGENT_WORKSPACE=ws["id"])
        return Helper(dirs["root"] / "broker.sock", env if spawn else None)

    def display_alive(self, ws: dict[str, Any]) -> bool:
        pid = int(ws.get("xvfb_pid") or 0)
        cmd = pid_cmdline(pid)
        return pid_alive(pid) and bool(cmd) and "Xvfb" in cmd[0] and str(ws.get("xauth", "")) in cmd

    def kill_pid_group(self, pid: int) -> None:
        if not pid:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass

    def sweep(self, ws_id: str, grace: float = 2.0) -> list[int]:
        """Find every process tagged with this workspace (apps, browser content
        processes, Xvfb) and terminate it. Returns the pids that were still alive."""
        tag = f"LOOM_AGENT_WORKSPACE={ws_id}".encode()
        me = os.getpid()

        def tagged() -> list[int]:
            out = []
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit() or int(entry.name) == me:
                    continue
                try:
                    if tag in (entry / "environ").read_bytes().split(b"\0"):
                        out.append(int(entry.name))
                except OSError:
                    continue
            return out

        pids = tagged()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and any(pid_alive(p) for p in pids):
            time.sleep(0.1)
        left = [p for p in tagged() if pid_alive(p)]
        for pid in left:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        return left

    def remove_dirs(self, ws: dict[str, Any], keep_profile: bool) -> None:
        shutil.rmtree(runtime_dir() / ws["id"], ignore_errors=True)
        if not keep_profile:
            shutil.rmtree(state_dir() / "profiles" / ws["id"], ignore_errors=True)


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------- service

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AgentWorkspaceService:
    def __init__(self, runtime: Any | None = None, clock: Callable[[], float] = time.time):
        self.runtime = runtime or ProcessRuntime()
        self.clock = clock
        self._lock = threading.RLock()            # registry / persistence
        self._ws_locks: dict[str, threading.RLock] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._helpers: dict[str, Any] = {}
        self._overlay_cache = ""
        self.cfg = load_config()
        state = _read_json(state_dir() / "workspaces.json", {})
        self.workspaces: dict[str, dict[str, Any]] = state.get("workspaces", {}) if isinstance(state, dict) else {}

    # -- persistence
    def _save(self) -> None:
        with self._lock:
            _atomic_write(state_dir() / "workspaces.json",
                          json.dumps({"version": 1, "workspaces": self.workspaces}, indent=1) + "\n")

    def audit(self, actor: str, agent: str, ws_id: str, op: str, ok: bool, detail: str = "") -> None:
        path = state_dir() / "audit.jsonl"
        line = json.dumps({"ts": round(self.clock(), 3), "actor": actor, "agent": agent, "workspace": ws_id,
                           "op": op, "ok": ok, "detail": detail[:300]}, ensure_ascii=False)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                    path.replace(path.with_suffix(".jsonl.1"))
            except OSError:
                pass
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(line + "\n")

    def ws_lock(self, ws_id: str) -> threading.RLock:
        with self._lock:
            return self._ws_locks.setdefault(ws_id, threading.RLock())

    # -- identity / appearance
    def agent_profile(self, agent_id: str, name: str = "") -> dict[str, Any]:
        with self._lock:
            agents = self.cfg.setdefault("agents", {})
            prof = agents.get(agent_id)
            if not prof:
                taken = [a.get("color") for k, a in agents.items() if k != agent_id]
                prof = {"color": assign_color(agent_id, taken), "name": name or display_name(agent_id),
                        "chosen": False}
                agents[agent_id] = prof
                save_config(self.cfg)
            elif name and not prof.get("name_chosen") and prof.get("name") != name:
                prof["name"] = name
                save_config(self.cfg)
            return dict(prof)

    def set_appearance(self, agent_id: str, color: str = "", name: str = "") -> dict[str, Any]:
        if not agent_id:
            raise GuiError("invalid", "agent_id is required")
        self.agent_profile(agent_id)
        with self._lock:
            prof = self.cfg["agents"][agent_id]
            if color:
                prof["color"], prof["chosen"] = normalize_color(color), True
            if name:
                prof["name"], prof["name_chosen"] = str(name).strip()[:24], True
            save_config(self.cfg)
        self.write_overlay(force=True)
        return {"ok": True, "agent": {"id": agent_id, **prof}}

    # -- views
    def _public(self, ws: dict[str, Any]) -> dict[str, Any]:
        hidden = {"token_hashes", "actions", "xauth"}
        out = {k: v for k, v in ws.items() if k not in hidden}
        out["agents"] = sorted(ws.get("token_hashes", {}).keys())
        return out

    def _get(self, ws_id: str) -> dict[str, Any]:
        ws = self.workspaces.get(str(ws_id or ""))
        if not ws:
            raise GuiError("not_found", f"unknown graphical workspace {ws_id!r}")
        return ws

    def _authorize(self, ws_id: str, agent_id: str, token: str, *, need_input: bool = True) -> dict[str, Any]:
        ws = self._get(ws_id)
        expected = ws.get("token_hashes", {}).get(agent_id)
        if not agent_id or not token or not expected or not secrets.compare_digest(expected, _hash_token(token)):
            self.audit("agent", agent_id, ws_id, "authorize", False, "bad token")
            raise GuiError("forbidden", "this agent is not authorized for that workspace")
        if ws["state"] in {"released", "lost"}:
            raise GuiError("released", f"workspace is {ws['state']}; acquire it again with the same request_key")
        if ws["state"] == "crashed":
            raise GuiError("crashed", "workspace crashed; acquire it again with the same request_key to recover")
        if need_input:
            if ws["state"] == "paused":
                raise GuiError("paused", "the user paused this workspace")
            if ws.get("controller") and ws["controller"] != agent_id:
                raise GuiError("busy", f"input is controlled by {ws['controller']}; call take_control first")
        return ws

    def capabilities(self) -> dict[str, Any]:
        return {"ok": True, "enabled": bool(self.cfg.get("enabled", True)),
                "capabilities": self.runtime.capabilities(self.cfg),
                "kinds": list(KINDS), "palette": [{"name": n, "color": c} for n, c in PALETTE]}

    def list(self, agent_id: str = "", include_released: bool = False) -> dict[str, Any]:
        with self._lock:
            items = [self._public(ws) for ws in self.workspaces.values()
                     if include_released or ws["state"] not in {"released"}]
        if agent_id:
            items = [w for w in items if agent_id in w["agents"]]
        return {"ok": True, "workspaces": sorted(items, key=lambda w: w.get("created", 0))}

    # -- lifecycle
    def acquire(self, *, agent_id: str, request_key: str, kind: str = "desktop", task_id: str = "",
                ai_session: str = "", label: str = "", size: str = "", url: str = "", owner_pid: int = 0,
                agent_name: str = "", actor: str = "agent") -> dict[str, Any]:
        if not self.cfg.get("enabled", True):
            raise GuiError("unavailable", "Loom graphical workspaces are disabled in settings")
        if not agent_id or not request_key:
            raise GuiError("invalid", "agent_id and request_key are required")
        if kind not in KINDS:
            raise GuiError("invalid", f"kind must be one of {', '.join(KINDS)}")
        cap = self.runtime.capabilities(self.cfg)[kind]
        if not cap["available"]:
            raise GuiError("unavailable", cap["reason"])
        ws_id = "gws-" + hashlib.sha256(f"{agent_id}\0{request_key}".encode()).hexdigest()[:12]
        lock = self.ws_lock(ws_id)
        with lock:
            with self._lock:
                existing = self.workspaces.get(ws_id)
                if existing and existing.get("kind") != kind:
                    raise GuiError("invalid", f"request_key already names a {existing['kind']} workspace")
                if not existing or existing["state"] not in ACTIVE_STATES:
                    active = [w for w in self.workspaces.values() if w["state"] in ACTIVE_STATES]
                    if len(active) >= int(self.cfg.get("max_workspaces", 6)):
                        raise GuiError("limit", f"maximum graphical workspaces reached ({len(active)})")
            prof = self.agent_profile(agent_id, agent_name)
            token = secrets.token_urlsafe(24)
            if existing and existing["state"] in ACTIVE_STATES and self._resources_alive(existing):
                with self._lock:
                    existing["token_hashes"][agent_id] = _hash_token(token)
                    if owner_pid:
                        existing["owner_pid"] = int(owner_pid)
                    existing["owner_seen"] = self.clock()
                    self._save()
                self.audit(actor, agent_id, ws_id, "acquire", True, "existing")
                return {"ok": True, "result": "existing", "token": token, "workspace": self._public(existing)}
            w, h = parse_size(size, str(self.cfg.get("default_size", "1280x800")))
            ws = existing or {"id": ws_id, "created": self.clock(), "recovered": 0, "actions": {}}
            if existing:
                ws["recovered"] = int(existing.get("recovered", 0)) + 1
                self._teardown(ws, keep_profile=True)
            ws.update({
                "kind": kind, "owner": agent_id, "owner_name": prof["name"], "request_key": request_key,
                "task_id": task_id, "ai_session": ai_session, "label": (label or prof["name"])[:60],
                "width": w, "height": h, "state": "starting", "controller": agent_id,
                "owner_pid": int(owner_pid or 0), "owner_seen": self.clock(), "last_active": self.clock(),
                "pointer": [w // 2, h // 2], "click_seq": int(ws.get("click_seq", 0)), "error": "",
                "token_hashes": {agent_id: _hash_token(token)}, "apps": [],
                "url": url if kind == "browser" else "",
            })
            with self._lock:
                self.workspaces[ws_id] = ws
                self._save()
            try:
                ws.update(self.runtime.start_display(ws))
                helper = self.runtime.helper(ws)
                self._helpers[ws_id] = helper
                if kind == "browser":
                    ws.update(self.runtime.spawn_browser(ws, self.cfg, "about:blank"))
                    helper.call("bidi-connect", timeout=35, port=int(ws["bidi_port"]), op_timeout=30)
                    self._fit(ws_id)
                    if url:  # BiDi navigation is browser-initiated, so data:/file: work too
                        ws["url"] = helper.call("bidi-navigate", timeout=35, url=url)["url"]
                ws["state"] = "ready"
            except GuiError as exc:
                ws["state"], ws["error"] = "crashed", str(exc)
                self._teardown(ws, keep_profile=True)
                with self._lock:
                    self._save()
                self.audit(actor, agent_id, ws_id, "acquire", False, str(exc))
                raise
            with self._lock:
                self._save()
            self.audit(actor, agent_id, ws_id, "acquire", True, f"{kind} {ws['display']}"
                       + (f" recovered#{ws['recovered']}" if ws["recovered"] else ""))
            self._touch(ws, preview=True)
            return {"ok": True, "result": "recovered" if existing else "created", "token": token,
                    "workspace": self._public(ws)}

    def attach(self, *, workspace_id: str, agent_id: str, agent_name: str = "") -> dict[str, Any]:
        """Re-issue a token to the owner (reconnect) or to an agent the owner granted."""
        with self.ws_lock(workspace_id):
            ws = self._get(workspace_id)
            if ws["state"] not in ACTIVE_STATES:
                raise GuiError("released", f"workspace is {ws['state']}")
            allowed = {ws["owner"], *ws.get("granted", [])}
            if agent_id not in allowed:
                self.audit("agent", agent_id, workspace_id, "attach", False, "not granted")
                raise GuiError("forbidden", "only the owner or an agent it granted may attach")
            self.agent_profile(agent_id, agent_name)
            token = secrets.token_urlsafe(24)
            with self._lock:
                ws["token_hashes"][agent_id] = _hash_token(token)
                self._save()
            self.audit("agent", agent_id, workspace_id, "attach", True)
            return {"ok": True, "token": token, "workspace": self._public(ws)}

    def grant(self, *, workspace_id: str, agent_id: str, token: str, grantee: str) -> dict[str, Any]:
        with self.ws_lock(workspace_id):
            ws = self._authorize(workspace_id, agent_id, token, need_input=False)
            if agent_id != ws["owner"]:
                raise GuiError("forbidden", "only the owner can grant access")
            with self._lock:
                ws.setdefault("granted", [])
                if grantee and grantee not in ws["granted"]:
                    ws["granted"].append(grantee)
                self._save()
            self.audit("agent", agent_id, workspace_id, "grant", True, grantee)
            return {"ok": True, "workspace": self._public(ws)}

    def take_control(self, *, workspace_id: str, agent_id: str, token: str) -> dict[str, Any]:
        """One X server has one core pointer: attached agents take turns."""
        with self.ws_lock(workspace_id):
            ws = self._authorize(workspace_id, agent_id, token, need_input=False)
            with self._lock:
                ws["controller"] = agent_id
                self._save()
            self.audit("agent", agent_id, workspace_id, "take_control", True)
            self._touch(ws)
            return {"ok": True, "workspace": self._public(ws)}

    def release(self, *, workspace_id: str, agent_id: str = "", token: str = "", actor: str = "agent",
                keep_profile: bool = True, reason: str = "") -> dict[str, Any]:
        self._cancel_event(workspace_id).set()
        with self.ws_lock(workspace_id):
            ws = self._get(workspace_id)
            if actor == "agent":
                self._authorize(workspace_id, agent_id, token, need_input=False)
            left = self._teardown(ws, keep_profile=keep_profile)
            with self._lock:
                ws["state"], ws["controller"] = "released", ""
                ws["token_hashes"] = {}
                ws["released_at"], ws["release_reason"] = self.clock(), reason or actor
                self._save()
            self._cancel_event(workspace_id).clear()
            self.audit(actor, agent_id, workspace_id, "release", True, reason)
            self.write_overlay(force=True)
            return {"ok": True, "workspace": self._public(ws), "leftover_pids": left}

    def release_owner(self, *, ai_session: str = "", agent_id: str = "", actor: str = "system") -> dict[str, Any]:
        released = []
        for ws in list(self.workspaces.values()):
            if ws["state"] not in ACTIVE_STATES and ws["state"] != "crashed":
                continue
            if (ai_session and ws.get("ai_session") == ai_session) or (agent_id and ws.get("owner") == agent_id):
                self.release(workspace_id=ws["id"], actor=actor, reason=f"owner ended ({ai_session or agent_id})")
                released.append(ws["id"])
        return {"ok": True, "released": released}

    def set_paused(self, *, workspace_id: str, paused: bool, agent_id: str = "", token: str = "",
                   actor: str = "user") -> dict[str, Any]:
        if paused:
            self._cancel_event(workspace_id).set()  # interrupt a running type/move promptly
        with self.ws_lock(workspace_id):
            ws = self._get(workspace_id)
            if actor == "agent":
                self._authorize(workspace_id, agent_id, token, need_input=False)
                if not paused and ws.get("paused_by") == "user":
                    raise GuiError("forbidden", "the user paused this workspace; only the user can resume it")
            if ws["state"] not in {"ready", "paused"}:
                raise GuiError("released", f"workspace is {ws['state']}")
            with self._lock:
                ws["state"] = "paused" if paused else "ready"
                ws["paused_by"] = actor if paused else ""
                self._save()
            self._cancel_event(workspace_id).clear()
            self.audit(actor, agent_id, workspace_id, "pause" if paused else "resume", True)
            self.write_overlay(force=True)
            return {"ok": True, "workspace": self._public(ws)}

    def cancel(self, *, workspace_id: str, agent_id: str, token: str) -> dict[str, Any]:
        self._authorize(workspace_id, agent_id, token, need_input=False)
        self._cancel_event(workspace_id).set()
        self.audit("agent", agent_id, workspace_id, "cancel", True)
        return {"ok": True}

    def _cancel_event(self, ws_id: str) -> threading.Event:
        with self._lock:
            return self._cancel.setdefault(ws_id, threading.Event())

    def _teardown(self, ws: dict[str, Any], keep_profile: bool) -> list[int]:
        helper = self._helpers.pop(ws["id"], None)
        if helper:
            helper.close()
        for pid in [*(a.get("pid", 0) for a in ws.get("apps", [])), ws.get("browser_pid", 0), ws.get("xvfb_pid", 0)]:
            self.runtime.kill_pid_group(int(pid or 0))
        left = self.runtime.sweep(ws["id"])
        self.runtime.remove_dirs(ws, keep_profile=keep_profile)
        for key in ("xvfb_pid", "browser_pid", "bidi_port", "display"):
            ws.pop(key, None)
        ws["apps"] = []
        try:
            (runtime_dir().parent / "agent-previews" / f"{ws['id']}.png").unlink()
        except OSError:
            pass
        return left

    def _resources_alive(self, ws: dict[str, Any]) -> bool:
        if not self.runtime.display_alive(ws):
            return False
        if ws["kind"] == "browser":
            if not pid_alive(int(ws.get("browser_pid") or 0)):
                return False
            if ws["id"] not in self._helpers:
                # The BiDi session lives in the broker: without it the browser is unusable.
                try:
                    self._helpers[ws["id"]] = self.runtime.helper(ws, spawn=False)
                except GuiError:
                    return False
        return True

    # -- connections (lazily re-created after a service restart)
    def _helper(self, ws: dict[str, Any]) -> Any:
        helper = self._helpers.get(ws["id"])
        if helper is None or not helper.alive():
            if not self.runtime.display_alive(ws):
                self._mark_crashed(ws, "the private display exited")
                raise GuiError("crashed", "workspace crashed; acquire it again with the same request_key")
            helper = self.runtime.helper(ws)
            self._helpers[ws["id"]] = helper
        return helper

    def _page(self, ws: dict[str, Any], expression: str, timeout: float = 10.0) -> Any:
        if ws["kind"] != "browser":
            raise GuiError("invalid", "this operation needs a browser workspace")
        if not pid_alive(int(ws.get("browser_pid") or 0)):
            self._mark_crashed(ws, "the private browser exited")
            raise GuiError("crashed", "browser crashed; acquire it again with the same request_key")
        try:
            return self._helper(ws).call("bidi-eval", timeout=timeout + 2, expression=expression,
                                         op_timeout=timeout)["value"]
        except GuiError as exc:
            if "no browser automation session" not in str(exc):
                raise
            # The broker was replaced while Firefox lived on. Firefox cannot hand an
            # existing BiDi session to a new connection, so the browser must restart.
            self._mark_crashed(ws, "browser automation session was lost")
            raise GuiError("crashed", "browser session lost; acquire it again with the same request_key "
                                      "(profile and cookies are kept)") from exc

    def _mark_crashed(self, ws: dict[str, Any], why: str) -> None:
        with self._lock:
            if ws["state"] in ACTIVE_STATES:
                ws["state"], ws["error"] = "crashed", why
                self._save()
        self.audit("system", ws.get("owner", ""), ws["id"], "crash", False, why)
        self.write_overlay(force=True)

    def _fit(self, ws_id: str) -> None:
        # Browser windows map a moment after BiDi is up; give them the full screen.
        helper = self._helpers.get(ws_id)
        if not helper:
            return
        for _ in range(20):
            if helper.call("windows")["windows"]:
                helper.call("fit")
                return
            time.sleep(0.15)

    # -- idempotent actions
    def _action(self, ws_id: str, agent_id: str, token: str, op: str, action_id: str,
                fn: Callable[[dict[str, Any]], dict[str, Any]], *, need_input: bool = True,
                detail: str = "") -> dict[str, Any]:
        with self.ws_lock(ws_id):
            ws = self._authorize(ws_id, agent_id, token, need_input=need_input)
            if action_id:
                cached = ws.get("actions", {}).get(action_id)
                if cached:
                    return {**cached, "replayed": True}
            self._cancel_event(ws_id).clear()
            try:
                result = {"ok": True, **fn(ws)}
            except GuiError as exc:
                self.audit("agent", agent_id, ws_id, op, False, f"{exc.code}: {exc}")
                raise
            except Exception as exc:  # helper/browser failures become predictable errors
                self.audit("agent", agent_id, ws_id, op, False, str(exc))
                raise GuiError("failed", f"{op} failed: {exc}") from exc
            if action_id:
                with self._lock:
                    actions = ws.setdefault("actions", {})
                    actions[action_id] = result
                    while len(actions) > 100:
                        actions.pop(next(iter(actions)))
            self.audit("agent", agent_id, ws_id, op, True, detail)
            self._touch(ws, preview=need_input)
            return result

    def _touch(self, ws: dict[str, Any], preview: bool = False) -> None:
        with self._lock:
            ws["last_active"] = self.clock()
            if preview:
                ws["preview_due"] = self.clock() + 0.25
            self._save()
        self.write_overlay(force=True)

    def _move(self, ws: dict[str, Any], x: float, y: float, duration_ms: int) -> None:
        x, y = int(x), int(y)
        if not (0 <= x < ws["width"] and 0 <= y < ws["height"]):
            raise GuiError("invalid", f"point ({x},{y}) is outside the {ws['width']}x{ws['height']} workspace")
        # Publish the target first so the overlay animates alongside the real motion.
        with self._lock:
            ws["pointer"], ws["move_ms"], ws["last_active"] = [x, y], int(duration_ms), self.clock()
        self.write_overlay(force=True)
        res = self._helper(ws).call("move", timeout=5 + duration_ms / 1000, x=x, y=y, duration_ms=duration_ms)
        ws["pointer"] = list(res["pointer"])

    def _locate(self, ws: dict[str, Any], selector: str, text: str) -> dict[str, Any]:
        found = self._page(ws, bidi.locate_expression(selector, text))
        if not isinstance(found, dict) or not found.get("found"):
            raise GuiError("not_found", f"no visible element matches {selector or text!r}")
        if found.get("obscured"):
            raise GuiError("obscured", f"{selector or text!r} is covered by another element")
        return found

    def move_pointer(self, *, workspace_id: str, agent_id: str, token: str, x: float, y: float,
                     duration_ms: int = 220, action_id: str = "") -> dict[str, Any]:
        duration_ms = max(0, min(2000, int(duration_ms)))
        return self._action(workspace_id, agent_id, token, "move", action_id,
                            lambda ws: (self._move(ws, x, y, duration_ms), {"pointer": ws["pointer"]})[1],
                            detail=f"{int(x)},{int(y)}")

    def click(self, *, workspace_id: str, agent_id: str, token: str, x: float | None = None,
              y: float | None = None, selector: str = "", text: str = "", button: str = "left",
              count: int = 1, action_id: str = "") -> dict[str, Any]:
        btn = {"left": 1, "middle": 2, "right": 3}.get(str(button), 0)
        if not btn:
            raise GuiError("invalid", "button must be left, middle or right")

        def run(ws: dict[str, Any]) -> dict[str, Any]:
            target: dict[str, Any] = {}
            if selector or text:
                target = self._locate(ws, selector, text)
                px, py = target["x"], target["y"]
            elif x is not None and y is not None:
                px, py = x, y
            else:
                px, py = ws["pointer"]
            self._move(ws, px, py, 220)
            self._helper(ws).call("click", button=btn, count=max(1, min(3, int(count))))
            with self._lock:
                ws["click_seq"] = int(ws.get("click_seq", 0)) + 1
                ws["click_button"] = button
            return {"pointer": ws["pointer"], "target": target or None}
        return self._action(workspace_id, agent_id, token, "click", action_id, run,
                            detail=selector or text or f"{x},{y}")

    def scroll(self, *, workspace_id: str, agent_id: str, token: str, dx: int = 0, dy: int = 3,
               x: float | None = None, y: float | None = None, selector: str = "",
               action_id: str = "") -> dict[str, Any]:
        def run(ws: dict[str, Any]) -> dict[str, Any]:
            if selector:
                t = self._locate(ws, selector, "")
                self._move(ws, t["x"], t["y"], 160)
            elif x is not None and y is not None:
                self._move(ws, x, y, 160)
            self._helper(ws).call("scroll", dx=max(-50, min(50, int(dx))), dy=max(-50, min(50, int(dy))))
            return {"pointer": ws["pointer"]}
        return self._action(workspace_id, agent_id, token, "scroll", action_id, run, detail=f"{dx},{dy}")

    def type_text(self, *, workspace_id: str, agent_id: str, token: str, text: str, selector: str = "",
                  action_id: str = "") -> dict[str, Any]:
        text = str(text or "")
        if len(text) > MAX_TEXT:
            raise GuiError("invalid", f"text is longer than {MAX_TEXT} characters; split it")

        def run(ws: dict[str, Any]) -> dict[str, Any]:
            if selector:
                t = self._locate(ws, selector, "")
                self._move(ws, t["x"], t["y"], 200)
                self._helper(ws).call("click", button=1, count=1)
                ws["click_seq"] = int(ws.get("click_seq", 0)) + 1
            stop = self._cancel_event(ws["id"])
            typed = 0
            # Chunked so pause/cancel/release interrupts long text between chunks.
            for i in range(0, len(text), 64):
                if stop.is_set():
                    raise GuiError("cancelled", f"typing cancelled after {typed} characters")
                chunk = text[i:i + 64]
                typed += int(self._helper(ws).call("type", timeout=5 + len(chunk) * 0.05, text=chunk)["typed"])
                ws["last_active"] = self.clock()
            return {"typed": typed}
        # Typed text is never written to the audit log: only its length.
        return self._action(workspace_id, agent_id, token, "type", action_id, run, detail=f"{len(text)} chars")

    def key(self, *, workspace_id: str, agent_id: str, token: str, keys: str, action_id: str = "") -> dict[str, Any]:
        from tabby.xinput import parse_combo
        try:
            parse_combo(keys)
        except ValueError as exc:
            raise GuiError("invalid", str(exc)) from exc
        return self._action(workspace_id, agent_id, token, "key", action_id,
                            lambda ws: (self._helper(ws).call("combo", keys=keys), {})[1], detail=keys)

    def launch(self, *, workspace_id: str, agent_id: str, token: str, argv: list[str],
               action_id: str = "") -> dict[str, Any]:
        command = argv
        if not isinstance(command, list) or not command or len(command) > MAX_LAUNCH_ARGS:
            raise GuiError("invalid", "command must be a non-empty argv list")

        def run(ws: dict[str, Any]) -> dict[str, Any]:
            if ws["kind"] != "desktop":
                raise GuiError("invalid", "launch apps in a desktop workspace; browser workspaces use navigate")
            pid = self.runtime.spawn_app(ws, [str(c) for c in command])
            with self._lock:
                ws["apps"].append({"pid": pid, "argv": [str(c) for c in command][:8], "started": self.clock()})
            for _ in range(30):  # wait briefly for a window so the agent can act right away
                time.sleep(0.15)
                if self._helper(ws).call("windows")["windows"]:
                    self._helper(ws).call("fit")
                    time.sleep(0.4)  # first paint after the resize
                    break
            return {"pid": pid}
        return self._action(workspace_id, agent_id, token, "launch", action_id, run, detail=" ".join(command)[:200])

    def navigate(self, *, workspace_id: str, agent_id: str, token: str, url: str,
                 action_id: str = "") -> dict[str, Any]:
        if not str(url).startswith(("http://", "https://", "about:", "file://", "data:")):
            raise GuiError("invalid", "url must be http(s), about:, file: or data:")

        def run(ws: dict[str, Any]) -> dict[str, Any]:
            if ws["kind"] != "browser":
                raise GuiError("invalid", "navigate needs a browser workspace")
            ws["url"] = self._helper(ws).call("bidi-navigate", timeout=35, url=url)["url"]
            return {"url": ws["url"]}
        return self._action(workspace_id, agent_id, token, "navigate", action_id, run, detail=url[:200])

    def screenshot(self, *, workspace_id: str, agent_id: str, token: str, max_width: int = 1280) -> dict[str, Any]:
        def run(ws: dict[str, Any]) -> dict[str, Any]:
            shots = runtime_dir() / ws["id"] / "shots"
            shots.mkdir(parents=True, exist_ok=True)
            path = shots / f"{int(self.clock() * 1000)}.png"
            for old in sorted(shots.glob("*.png"))[:-4]:
                old.unlink(missing_ok=True)
            res = self._helper(ws).call("capture", path=str(path), max_width=max(320, min(3840, int(max_width))),
                                        preview=str(self._preview_path(ws)))
            return {"path": res["path"], "width": ws["width"], "height": ws["height"],
                    "image_width": res["imageWidth"], "image_height": res["imageHeight"],
                    "pointer": ws["pointer"]}
        return self._action(workspace_id, agent_id, token, "screenshot", "", run, need_input=False)

    def interaction_state(self, *, workspace_id: str, agent_id: str, token: str, include_text: bool = False,
                          selector: str = "") -> dict[str, Any]:
        def run(ws: dict[str, Any]) -> dict[str, Any]:
            helper = self._helper(ws)
            out: dict[str, Any] = {"workspace": self._public(ws), "pointer": helper.call("pointer")["pointer"],
                                   "windows": helper.call("windows")["windows"]}
            if ws["kind"] == "browser":
                out["page"] = self._page(ws, bidi.state_expression(include_text))
                if selector:
                    out["elements"] = self._page(ws, bidi.read_expression(selector))
            return out
        return self._action(workspace_id, agent_id, token, "state", "", run, need_input=False)

    # -- monitoring, recovery and overlay
    def _preview_path(self, ws: dict[str, Any]) -> Path:
        p = runtime_dir().parent / "agent-previews"
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{ws['id']}.png"

    def reconcile(self) -> dict[str, Any]:
        """After a service restart or reboot: adopt live displays, mark the rest lost
        and sweep their processes. Workspace identity (id, owner, request_key,
        browser profile) survives so the same acquire call recovers it."""
        adopted, lost = [], []
        for ws in list(self.workspaces.values()):
            if ws["state"] not in ACTIVE_STATES:
                continue
            if self._resources_alive(ws):
                adopted.append(ws["id"])
                continue
            self._teardown(ws, keep_profile=True)
            with self._lock:
                ws["state"], ws["error"] = "lost", "resources did not survive a restart"
            lost.append(ws["id"])
            self.audit("system", ws.get("owner", ""), ws["id"], "reconcile", False, "lost")
        with self._lock:
            self._save()
        self.write_overlay(force=True)
        return {"ok": True, "adopted": adopted, "lost": lost}

    def tick(self) -> None:
        """Called about once a second by the service loop. Cheap when idle."""
        CHILDREN.reap()
        now = self.clock()
        grace = float(self.cfg.get("owner_grace_seconds", 90))
        idle = float(self.cfg.get("idle_release_minutes", 45)) * 60
        for ws in list(self.workspaces.values()):
            if ws["state"] not in ACTIVE_STATES:
                continue
            lock = self.ws_lock(ws["id"])
            if not lock.acquire(blocking=False):
                continue  # an action is running; check next tick
            try:
                if not self.runtime.display_alive(ws):
                    self._mark_crashed(ws, "the private display exited")
                    self._teardown(ws, keep_profile=True)
                    continue
                if ws["kind"] == "browser" and not pid_alive(int(ws.get("browser_pid") or 0)):
                    self._mark_crashed(ws, "the private browser exited")
                    self._teardown(ws, keep_profile=True)
                    continue
                owner_pid = int(ws.get("owner_pid") or 0)
                if owner_pid:
                    if pid_alive(owner_pid):
                        ws["owner_seen"] = now
                    elif now - float(ws.get("owner_seen", now)) > grace:
                        self.release(workspace_id=ws["id"], actor="system", reason="owner process exited")
                        continue
                elif now - float(ws.get("last_active", now)) > idle:
                    self.release(workspace_id=ws["id"], actor="system", reason="idle")
                    continue
                active_recently = now - float(ws.get("last_active", 0)) < float(self.cfg.get("cursor_idle_seconds", 8))
                due = float(ws.get("preview_due") or 0)
                if (due and now >= due) or (active_recently and now - float(ws.get("preview_at", 0)) > 1.5):
                    try:
                        if ws["kind"] == "desktop" and ws.get("apps"):
                            self._helper(ws).call("fit")
                        self._helper(ws).call("capture", path="", preview=str(self._preview_path(ws)))
                        ws["preview_at"], ws["preview_due"] = now, 0
                        ws["preview_seq"] = int(ws.get("preview_seq", 0)) + 1
                    except GuiError:
                        pass
            finally:
                lock.release()
        self.write_overlay()

    def overlay_state(self) -> dict[str, Any]:
        now = self.clock()
        idle = float(self.cfg.get("cursor_idle_seconds", 8))
        hidden = set(self.cfg.get("hidden_agents") or [])
        agents = []
        for ws in sorted(self.workspaces.values(), key=lambda w: w.get("created", 0)):
            if ws["state"] not in ACTIVE_STATES and ws["state"] != "crashed":
                continue
            who = ws.get("controller") or ws.get("owner", "")
            prof = (self.cfg.get("agents") or {}).get(who) or {"color": PALETTE[0][1], "name": display_name(who)}
            preview = runtime_dir().parent / "agent-previews" / f"{ws['id']}.png"
            agents.append({
                "workspace": ws["id"], "agent": who, "name": prof.get("name", who), "color": prof.get("color"),
                "kind": ws["kind"], "label": ws.get("label", ""), "state": ws["state"],
                "x": ws.get("pointer", [0, 0])[0], "y": ws.get("pointer", [0, 0])[1],
                "width": ws["width"], "height": ws["height"], "moveMs": int(ws.get("move_ms", 220)),
                "clickSeq": int(ws.get("click_seq", 0)), "clickButton": ws.get("click_button", "left"),
                "active": now - float(ws.get("last_active", 0)) < idle or ws["state"] == "paused",
                "hidden": who in hidden, "preview": str(preview) if preview.exists() else "",
                "previewSeq": int(ws.get("preview_seq", 0)), "taskId": ws.get("task_id", ""),
            })
        return {"version": 1, "agents": agents}

    def write_overlay(self, force: bool = False) -> None:
        text = json.dumps(self.overlay_state(), separators=(",", ":"))
        if text == self._overlay_cache and not force:
            return
        self._overlay_cache = text
        try:
            _atomic_write(overlay_path(), text + "\n", 0o644)
        except OSError:
            pass

    def user_hide_agent(self, agent_id: str, hidden: bool) -> dict[str, Any]:
        with self._lock:
            items = set(self.cfg.get("hidden_agents") or [])
            (items.add if hidden else items.discard)(agent_id)
            self.cfg["hidden_agents"] = sorted(items)
            save_config(self.cfg)
        self.write_overlay(force=True)
        return {"ok": True, "hidden_agents": self.cfg["hidden_agents"]}

    def audit_tail(self, workspace_id: str = "", limit: int = 50) -> dict[str, Any]:
        path = state_dir() / "audit.jsonl"
        try:
            lines = path.read_text().splitlines()[-2000:]
        except OSError:
            lines = []
        rows = []
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if workspace_id and row.get("workspace") != workspace_id:
                continue
            rows.append(row)
            if len(rows) >= max(1, min(500, limit)):
                break
        return {"ok": True, "entries": rows}

    def shutdown(self) -> None:
        """Service stop: leave displays running so a restart re-adopts them."""
        for helper in list(self._helpers.values()):
            helper.close()
        self._helpers.clear()
