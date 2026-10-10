"""Per-workspace X11 input/capture helper for Loom agent workspaces.

This module runs as its own process (``python3 -m tabby.xinput serve <socket>``),
one per isolated agent display.  libX11 terminates the whole process on an I/O error
(for example when an agent's Xvfb crashes), so the connection must never live
inside the long-running Loom agent-input service.

The broker serves newline-delimited JSON on a mode-0600 Unix socket.  It only ever opens
the display named by its own ``DISPLAY``/``XAUTHORITY`` environment, which is a
private Xvfb server: nothing here can reach the user's Hyprland session, its
pointer or its keyboard focus.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import sys
import time
from typing import Any

# ----------------------------------------------------------------- key names

MODIFIERS = {
    "ctrl": "Control_L", "control": "Control_L", "shift": "Shift_L",
    "alt": "Alt_L", "option": "Alt_L", "super": "Super_L", "meta": "Super_L",
    "cmd": "Super_L", "win": "Super_L",
}
KEY_ALIASES = {
    "enter": "Return", "return": "Return", "tab": "Tab", "esc": "Escape",
    "escape": "Escape", "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "insert": "Insert", "space": "space", "up": "Up", "down": "Down", "left": "Left",
    "right": "Right", "home": "Home", "end": "End", "pageup": "Prior", "pgup": "Prior",
    "pagedown": "Next", "pgdn": "Next", "menu": "Menu", "printscreen": "Print",
    "plus": "plus", "minus": "minus", "comma": "comma", "period": "period",
    "slash": "slash", "semicolon": "semicolon",
}
SPECIAL_CHARS = {"\n": 0xFF0D, "\r": 0xFF0D, "\t": 0xFF09}


def parse_combo(combo: str) -> tuple[list[str], str]:
    """'ctrl+shift+t' -> (['Control_L','Shift_L'], 't'). Raises ValueError."""
    parts = [p.strip() for p in str(combo or "").replace(" ", "").split("+")]
    if not parts or not parts[-1]:
        # 'ctrl++' means ctrl and the plus key
        if str(combo).endswith("++"):
            parts = [p for p in parts if p] + ["plus"]
        else:
            raise ValueError(f"invalid key combination: {combo!r}")
    mods, key = parts[:-1], parts[-1]
    out = []
    for mod in mods:
        name = MODIFIERS.get(mod.lower())
        if not name:
            raise ValueError(f"unknown modifier {mod!r} in {combo!r}")
        out.append(name)
    low = key.lower()
    if low in MODIFIERS and not mods:
        return [], MODIFIERS[low]
    if low in KEY_ALIASES:
        key = KEY_ALIASES[low]
    elif len(low) > 1 and low[0] == "f" and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        key = "F" + low[1:]
    elif len(key) == 1:
        key = key.lower() if key.isalpha() else key
    return out, key


def char_keysym(ch: str) -> int:
    if ch in SPECIAL_CHARS:
        return SPECIAL_CHARS[ch]
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp  # Latin-1 keysyms equal their code points
    return 0x01000000 + cp


# ----------------------------------------------------------------- libX11 FFI

class _XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
                ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int),
                ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
                ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
                ("blue_mask", ctypes.c_ulong)]


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int),
                ("height", ctypes.c_int), ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
                ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong), ("class_", ctypes.c_int),
                ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
                ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
                ("colormap", ctypes.c_ulong), ("map_installed", ctypes.c_int),
                ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
                ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p)]


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_IGNORE_ERRORS = _ERROR_HANDLER(lambda _d, _e: 0)  # BadWindow on a vanished window is normal


class XSession:
    """One connection to one private X server. Not thread-safe by design."""

    SCRATCH_POOL = 6

    def __init__(self, display: str | None = None):
        self.x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self.xt = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        x, xt = self.x, self.xt
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XSetErrorHandler(_IGNORE_ERRORS)
        self.d = x.XOpenDisplay(display.encode() if display else None)
        if not self.d:
            raise RuntimeError("cannot open the agent display")
        vp, ul, ci, ui = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_uint
        for name, res, args in [
            ("XDefaultRootWindow", ul, [vp]), ("XDefaultScreen", ci, [vp]),
            ("XDisplayWidth", ci, [vp, ci]), ("XDisplayHeight", ci, [vp, ci]),
            ("XFlush", ci, [vp]), ("XSync", ci, [vp, ci]),
            ("XKeysymToKeycode", ctypes.c_ubyte, [vp, ul]),
            ("XStringToKeysym", ul, [ctypes.c_char_p]),
            ("XDisplayKeycodes", ci, [vp, ctypes.POINTER(ci), ctypes.POINTER(ci)]),
            ("XGetKeyboardMapping", ctypes.POINTER(ul), [vp, ctypes.c_ubyte, ci, ctypes.POINTER(ci)]),
            ("XChangeKeyboardMapping", ci, [vp, ci, ci, ctypes.POINTER(ul), ci]),
            ("XFree", ci, [vp]),
            ("XQueryPointer", ci, [vp, ul] + [vp] * 7),
            ("XGetImage", ctypes.POINTER(_XImage), [vp, ul, ci, ci, ui, ui, ul, ci]),
            ("XQueryTree", ci, [vp, ul, ctypes.POINTER(ul), ctypes.POINTER(ul),
                                ctypes.POINTER(ctypes.POINTER(ul)), ctypes.POINTER(ui)]),
            ("XGetWindowAttributes", ci, [vp, ul, ctypes.POINTER(_XWindowAttributes)]),
            ("XFetchName", ci, [vp, ul, ctypes.POINTER(ctypes.c_char_p)]),
            ("XMoveResizeWindow", ci, [vp, ul, ci, ci, ui, ui]),
            ("XRaiseWindow", ci, [vp, ul]), ("XSetInputFocus", ci, [vp, ul, ci, ul]),
            ("XGetInputFocus", ci, [vp, ctypes.POINTER(ul), ctypes.POINTER(ci)]),
        ]:
            fn = getattr(x, name)
            fn.restype, fn.argtypes = res, args
        xt.XTestFakeMotionEvent.argtypes = [vp, ci, ci, ci, ul]
        xt.XTestFakeButtonEvent.argtypes = [vp, ui, ci, ul]
        xt.XTestFakeKeyEvent.argtypes = [vp, ui, ci, ul]
        xt.XTestQueryExtension.argtypes = [vp] + [ctypes.POINTER(ci)] * 4
        a, b, c, e = ci(), ci(), ci(), ci()
        if not xt.XTestQueryExtension(self.d, *(ctypes.byref(v) for v in (a, b, c, e))):
            raise RuntimeError("the agent display has no XTEST extension")
        self.root = x.XDefaultRootWindow(self.d)
        scr = x.XDefaultScreen(self.d)
        self.width, self.height = x.XDisplayWidth(self.d, scr), x.XDisplayHeight(self.d, scr)
        lo, hi = ci(), ci()
        x.XDisplayKeycodes(self.d, ctypes.byref(lo), ctypes.byref(hi))
        self.min_kc, self.max_kc = lo.value, hi.value
        self._scratch_used: list[int] = []
        self._load_keymap()

    # -- keyboard map
    def _load_keymap(self) -> None:
        per = ctypes.c_int()
        count = self.max_kc - self.min_kc + 1
        ptr = self.x.XGetKeyboardMapping(self.d, self.min_kc, count, ctypes.byref(per))
        self.per = per.value
        self.keymap = [ptr[i] for i in range(count * self.per)]
        self.x.XFree(ptr)
        self.scratch = [kc for kc in range(self.max_kc, self.min_kc - 1, -1)
                        if not any(self.keymap[(kc - self.min_kc) * self.per + j] for j in range(self.per))
                        ][: self.SCRATCH_POOL]

    def _lookup(self, keysym: int) -> tuple[int, bool] | None:
        for kc in range(self.min_kc, self.max_kc + 1):
            base = (kc - self.min_kc) * self.per
            if self.keymap[base] == keysym:
                return kc, False
            if self.per > 1 and self.keymap[base + 1] == keysym:
                return kc, True
        return None

    def _remap(self, keysym: int) -> int:
        if not self.scratch:
            raise RuntimeError("no spare keycode to type this character")
        kc = self.scratch[len(self._scratch_used) % len(self.scratch)]
        arr = (ctypes.c_ulong * 1)(keysym)
        self.x.XChangeKeyboardMapping(self.d, kc, 1, arr, 1)
        self.x.XSync(self.d, 0)
        self._scratch_used.append(kc)
        time.sleep(0.06)  # let clients (GTK4/XKB is the slowest) process MappingNotify first
        return kc

    def _restore_scratch(self) -> None:
        if not self._scratch_used:
            return
        time.sleep(0.15)  # the last remapped key must be translated before it disappears
        zero = (ctypes.c_ulong * 1)(0)
        for kc in set(self._scratch_used):
            self.x.XChangeKeyboardMapping(self.d, kc, 1, zero, 1)
        self._scratch_used.clear()
        self.x.XSync(self.d, 0)

    def _key(self, keycode: int, down: bool) -> None:
        self.xt.XTestFakeKeyEvent(self.d, keycode, 1 if down else 0, 0)

    def _keysym_code(self, name: str) -> int:
        ks = self.x.XStringToKeysym(name.encode())
        if not ks:
            raise ValueError(f"unknown key {name!r}")
        kc = self.x.XKeysymToKeycode(self.d, ks)
        return kc or self._remap(ks)

    # -- operations
    def pointer(self) -> tuple[int, int]:
        rx, ry, wx, wy = (ctypes.c_int() for _ in range(4))
        r, c, m = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_uint()
        self.x.XQueryPointer(self.d, self.root, ctypes.byref(r), ctypes.byref(c), ctypes.byref(rx),
                             ctypes.byref(ry), ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(m))
        return rx.value, ry.value

    def move(self, x: int, y: int, duration_ms: int = 0) -> tuple[int, int]:
        x = max(0, min(self.width - 1, int(x)))
        y = max(0, min(self.height - 1, int(y)))
        steps = max(1, min(60, int(duration_ms / 16)))
        sx, sy = self.pointer()
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)  # smoothstep, matches the overlay's easing
            self.xt.XTestFakeMotionEvent(self.d, -1, round(sx + (x - sx) * t), round(sy + (y - sy) * t), 0)
            self.x.XFlush(self.d)
            if steps > 1:
                time.sleep(duration_ms / 1000 / steps)
        self.x.XSync(self.d, 0)
        return x, y

    def click(self, button: int = 1, count: int = 1) -> None:
        for i in range(max(1, min(3, count))):
            self.xt.XTestFakeButtonEvent(self.d, button, 1, 0)
            self.xt.XTestFakeButtonEvent(self.d, button, 0, 0)
            self.x.XFlush(self.d)
            if i + 1 < count:
                time.sleep(0.06)
        self.x.XSync(self.d, 0)

    def button(self, button: int, down: bool) -> None:
        self.xt.XTestFakeButtonEvent(self.d, button, 1 if down else 0, 0)
        self.x.XSync(self.d, 0)

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        for amount, neg, pos in ((dy, 4, 5), (dx, 6, 7)):
            btn = neg if amount < 0 else pos
            for _ in range(min(50, abs(int(amount)))):
                self.xt.XTestFakeButtonEvent(self.d, btn, 1, 0)
                self.xt.XTestFakeButtonEvent(self.d, btn, 0, 0)
                self.x.XFlush(self.d)
                time.sleep(0.012)
        self.x.XSync(self.d, 0)

    def combo(self, combo: str) -> None:
        mods, key = parse_combo(combo)
        mod_codes = [self._keysym_code(m) for m in mods]
        kc = self._keysym_code(key)
        try:
            for m in mod_codes:
                self._key(m, True)
            self._key(kc, True)
            self._key(kc, False)
        finally:
            for m in reversed(mod_codes):
                self._key(m, False)
            self.x.XSync(self.d, 0)
            self._restore_scratch()

    def type_text(self, text: str, delay_ms: int = 8, should_stop=lambda: False) -> int:
        shift = self._keysym_code("Shift_L")
        typed = 0
        try:
            for ch in text:
                if should_stop():
                    break
                ks = char_keysym(ch)
                hit = self._lookup(ks)
                if hit:
                    kc, needs_shift = hit
                else:
                    kc, needs_shift = self._remap(ks), False
                if needs_shift:
                    self._key(shift, True)
                self._key(kc, True)
                self._key(kc, False)
                if needs_shift:
                    self._key(shift, False)
                self.x.XFlush(self.d)
                typed += 1
                if len(self._scratch_used) >= len(self.scratch) and self.scratch:
                    self.x.XSync(self.d, 0)
                    self._restore_scratch()
                time.sleep(max(0, delay_ms) / 1000)
        finally:
            self.x.XSync(self.d, 0)
            self._restore_scratch()
        return typed

    def windows(self) -> list[dict[str, Any]]:
        root_ret, parent = ctypes.c_ulong(), ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        n = ctypes.c_uint()
        if not self.x.XQueryTree(self.d, self.root, ctypes.byref(root_ret), ctypes.byref(parent),
                                 ctypes.byref(children), ctypes.byref(n)):
            return []
        ids = [children[i] for i in range(n.value)]
        if children:
            self.x.XFree(children)
        focus, revert = ctypes.c_ulong(), ctypes.c_int()
        self.x.XGetInputFocus(self.d, ctypes.byref(focus), ctypes.byref(revert))
        out = []
        for wid in ids:
            attrs = _XWindowAttributes()
            if not self.x.XGetWindowAttributes(self.d, wid, ctypes.byref(attrs)):
                continue
            if attrs.map_state != 2 or attrs.override_redirect or attrs.width < 40 or attrs.height < 40:
                continue
            name = ctypes.c_char_p()
            title = ""
            if self.x.XFetchName(self.d, wid, ctypes.byref(name)) and name.value is not None:
                title = name.value.decode("utf-8", "replace")
                self.x.XFree(name)
            out.append({"id": int(wid), "title": title[:200], "x": attrs.x, "y": attrs.y,
                        "width": attrs.width, "height": attrs.height, "focused": int(wid) == focus.value})
        return out

    def fit_windows(self) -> int:
        """There is no window manager: make new top-level windows fill the screen."""
        fitted = 0
        for w in self.windows():
            if (w["x"], w["y"], w["width"], w["height"]) != (0, 0, self.width, self.height):
                self.x.XMoveResizeWindow(self.d, w["id"], 0, 0, self.width, self.height)
                fitted += 1
        self.x.XSync(self.d, 0)
        return fitted

    def focus_window(self, wid: int) -> None:
        self.x.XRaiseWindow(self.d, wid)
        self.x.XSetInputFocus(self.d, wid, 1, 0)  # RevertToPointerRoot, CurrentTime
        self.x.XSync(self.d, 0)

    def capture(self, path: str, preview_path: str = "", preview_width: int = 360,
                max_width: int = 0, jpeg_path: str = "", jpeg_width: int = 0) -> dict[str, Any]:
        from PIL import Image  # local import keeps the helper fast to start
        img_p = self.x.XGetImage(self.d, self.root, 0, 0, self.width, self.height, 0xFFFFFFFF, 2)
        if not img_p:
            raise RuntimeError("XGetImage failed")
        img = img_p.contents
        try:
            if img.bits_per_pixel != 32:
                raise RuntimeError(f"unsupported depth {img.bits_per_pixel}")
            raw = ctypes.string_at(img.data, img.bytes_per_line * img.height)
            frame = Image.frombuffer("RGB", (img.width, img.height), raw, "raw", "BGRX",
                                     img.bytes_per_line, 1)
            frame = frame.copy()
        finally:
            self.x.XFree(img.data)
            self.x.XFree(img_p)
        out: dict[str, Any] = {"width": self.width, "height": self.height}
        if path:
            full = frame
            if max_width and frame.width > max_width:
                full = frame.resize((max_width, round(frame.height * max_width / frame.width)))
            full.save(path, "PNG", optimize=False)
            out.update(path=path, imageWidth=full.width, imageHeight=full.height)
        if jpeg_path:
            live = frame
            if jpeg_width and frame.width > jpeg_width:
                live = frame.resize((jpeg_width, round(frame.height * jpeg_width / frame.width)))
            import os
            tmp = jpeg_path + ".tmp.jpg"
            live.save(tmp, "JPEG", quality=80)
            os.replace(tmp, jpeg_path)
            out["jpeg"] = jpeg_path
        if preview_path:
            small = frame.resize((preview_width, max(1, round(frame.height * preview_width / frame.width))))
            tmp = preview_path + ".tmp.png"
            small.save(tmp, "PNG")
            import os
            os.replace(tmp, preview_path)
            out["preview"] = preview_path
        return out


# ----------------------------------------------------------------- broker loop

class Broker:
    """Owns one workspace's X connection and (for browsers) its BiDi session.

    It outlives restarts of the Loom service: Firefox cannot re-attach to a
    BiDi-only session once its WebSocket is gone, so the connection lives here
    and the service re-adopts the broker through its Unix socket."""

    def __init__(self, session: XSession):
        self.x = session
        self.browser: Any = None

    def bidi(self) -> Any:
        if self.browser is None:
            raise RuntimeError("no browser automation session")
        return self.browser

    def connect_bidi(self, port: int, timeout: float) -> dict[str, Any]:
        from tabby.bidi import BidiSession
        if self.browser is not None:
            return {"connected": True, "reused": True}
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.browser = BidiSession(int(port), timeout=8.0)
                self.browser.top_context()
                return {"connected": True}
            except Exception as exc:  # browser still starting
                last = exc
                self.browser = None
                time.sleep(0.4)
        raise RuntimeError(f"browser automation did not come up: {last}")


def _dispatch(broker: Broker, req: dict[str, Any]) -> dict[str, Any]:
    session = broker.x
    op = req.get("op")
    if op == "bidi-connect":
        return broker.connect_bidi(int(req["port"]), float(req.get("op_timeout", 25)))
    if op == "bidi-navigate":
        res = broker.bidi().navigate(str(req["url"]), timeout=float(req.get("op_timeout", 30)))
        return {"url": res.get("url", req["url"])}
    if op == "bidi-eval":
        return {"value": broker.bidi().evaluate_json(str(req["expression"]), timeout=float(req.get("op_timeout", 10)))}
    if op == "hello":
        return {"width": session.width, "height": session.height, "pointer": session.pointer()}
    if op == "pointer":
        return {"pointer": session.pointer()}
    if op == "move":
        return {"pointer": session.move(req["x"], req["y"], int(req.get("duration_ms", 0)))}
    if op == "click":
        session.click(int(req.get("button", 1)), int(req.get("count", 1)))
        return {"pointer": session.pointer()}
    if op == "button":
        session.button(int(req.get("button", 1)), bool(req.get("down")))
        return {}
    if op == "scroll":
        session.scroll(int(req.get("dx", 0)), int(req.get("dy", 0)))
        return {}
    if op == "combo":
        session.combo(str(req["keys"]))
        return {}
    if op == "type":
        return {"typed": session.type_text(str(req.get("text", "")), int(req.get("delay_ms", 8)))}
    if op == "windows":
        return {"windows": session.windows()}
    if op == "fit":
        return {"fitted": session.fit_windows()}
    if op == "focus":
        session.focus_window(int(req["window"]))
        return {}
    if op == "capture":
        return session.capture(str(req.get("path") or ""), str(req.get("preview") or ""),
                               int(req.get("preview_width", 360)), int(req.get("max_width", 0)),
                               str(req.get("jpeg") or ""), int(req.get("jpeg_width", 0)))
    raise ValueError(f"unknown op {op!r}")


def serve(sock_path: str) -> int:
    import os
    import socket
    try:
        broker = Broker(XSession())
    except Exception as exc:  # report and exit; the service marks the workspace unavailable
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        sys.stdout.flush()
        return 2
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        srv.bind(sock_path)
    except OSError as exc:  # e.g. a socket path longer than AF_UNIX allows
        sys.stdout.write(json.dumps({"ok": False, "error": f"cannot listen on {sock_path}: {exc}"}) + "\n")
        sys.stdout.flush()
        return 2
    finally:
        os.umask(old)
    srv.listen(2)
    sys.stdout.write(json.dumps({"ok": True, "ready": True, "width": broker.x.width,
                                 "height": broker.x.height}) + "\n")
    sys.stdout.flush()
    # The spawning service stops reading stdout after "ready"; never write to it again.
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    while True:
        conn, _ = srv.accept()  # one client (the service) at a time
        with conn, conn.makefile("rw", encoding="utf-8", newline="\n") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    result = {"ok": True, **_dispatch(broker, json.loads(line))}
                except Exception as exc:
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                try:
                    fh.write(json.dumps(result) + "\n")
                    fh.flush()
                except OSError:
                    break


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "serve":
        raise SystemExit(serve(sys.argv[2]))
    print("usage: python3 -m tabby.xinput serve <socket>", file=sys.stderr)
    raise SystemExit(2)
