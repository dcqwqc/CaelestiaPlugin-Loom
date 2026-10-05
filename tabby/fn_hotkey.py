from __future__ import annotations

import os
import select
import struct
import threading
import time
from pathlib import Path
from typing import Callable

EV_KEY = 0x01
KEY_FN = 0x1D0
_INPUT_EVENT = struct.Struct("llHHI")


class FnHotkeyMonitor:
    """Watch real physical evdev devices for a double-tap of KEY_FN.

    Lenovo often consumes bare Fn in firmware. In that case `available` stays
    false and callers can keep a normal keyboard fallback active.
    """

    _VIRTUAL_MARKERS = (
        "ydotool", "virtual", "libvirtualhid", "gesture", "uinput",
    )

    def __init__(self, callback: Callable[[], None], double_tap_ms: int = 350):
        self.callback = callback
        self.double_tap_ms = max(150, min(800, int(double_tap_ms)))
        self.available = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tap = 0.0
        self._fds: dict[int, str] = {}

    @staticmethod
    def _supports_key_fn(event_name: str) -> bool:
        path = Path("/sys/class/input") / event_name / "device/capabilities/key"
        try:
            words = [int(x, 16) for x in path.read_text().strip().split()[::-1]]
        except Exception:
            return False
        word, bit = divmod(KEY_FN, 64)
        return word < len(words) and bool(words[word] & (1 << bit))

    @classmethod
    def _physical_candidates(cls) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for ev in sorted(Path("/sys/class/input").glob("event*")):
            try:
                name = (ev / "device/name").read_text().strip()
            except Exception:
                continue
            low = name.lower()
            if any(marker in low for marker in cls._VIRTUAL_MARKERS):
                continue
            if cls._supports_key_fn(ev.name):
                out.append((f"/dev/input/{ev.name}", name))
        return out

    def _close_fds(self) -> None:
        for fd in list(self._fds):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()

    def _open_devices(self) -> None:
        self._close_fds()
        for path, name in self._physical_candidates():
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            self._fds[fd] = name
        self.available = bool(self._fds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # Probe synchronously so callers can expose truthful availability in
        # settings/state immediately after start(), not one poll later.
        self._open_devices()
        self._thread = threading.Thread(target=self._loop, name="tabby-fn-hotkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_fds()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _tap(self) -> None:
        now = time.monotonic()
        delta_ms = (now - self._last_tap) * 1000.0
        self._last_tap = now
        if 40 <= delta_ms <= self.double_tap_ms:
            self._last_tap = 0.0
            try:
                self.callback()
            except Exception:
                pass

    def _loop(self) -> None:
        next_scan = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_scan:
                self._open_devices()
                next_scan = now + 5.0

            if not self._fds:
                self._stop.wait(0.5)
                continue

            poller = select.poll()
            for fd in self._fds:
                try:
                    poller.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)
                except Exception:
                    pass

            try:
                events = poller.poll(500)
            except Exception:
                events = []

            rescan = False
            for fd, mask in events:
                if mask & (select.POLLERR | select.POLLHUP):
                    rescan = True
                    continue
                if not (mask & select.POLLIN):
                    continue
                try:
                    data = os.read(fd, _INPUT_EVENT.size * 64)
                except (BlockingIOError, OSError):
                    continue
                for off in range(0, len(data) - _INPUT_EVENT.size + 1, _INPUT_EVENT.size):
                    _, _, typ, code, value = _INPUT_EVENT.unpack_from(data, off)
                    if typ == EV_KEY and code == KEY_FN and value == 1:
                        self._tap()

            if rescan:
                self._open_devices()

        self.available = False
        self._close_fds()

KEY_LEFTALT = 56


class LeftAltHotkeyMonitor(FnHotkeyMonitor):
    """Detect two *standalone* Left-Alt taps without interfering with Alt shortcuts.

    A tap only counts after Left Alt is released, must be short, and is cancelled
    if any other key is pressed while Left Alt is held (so Alt+Tab/Alt+F4 etc.
    never become Tabby taps).
    """

    def __init__(self, callback: Callable[[], None], double_tap_ms: int = 350, max_tap_ms: int = 300):
        super().__init__(callback, double_tap_ms)
        self.max_tap_ms = max(100, min(500, int(max_tap_ms)))
        self._alt_down_at = 0.0
        self._alt_disqualified = False

    @staticmethod
    def _supports_left_alt(event_name: str) -> bool:
        path = Path("/sys/class/input") / event_name / "device/capabilities/key"
        try:
            words = [int(x, 16) for x in path.read_text().strip().split()[::-1]]
        except Exception:
            return False
        word, bit = divmod(KEY_LEFTALT, 64)
        return word < len(words) and bool(words[word] & (1 << bit))

    @classmethod
    def _physical_candidates(cls) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for ev in sorted(Path("/sys/class/input").glob("event*")):
            try:
                name = (ev / "device/name").read_text().strip()
            except Exception:
                continue
            low = name.lower()
            if any(marker in low for marker in cls._VIRTUAL_MARKERS):
                continue
            if cls._supports_left_alt(ev.name):
                out.append((f"/dev/input/{ev.name}", name))
        return out

    def _handle_key(self, code: int, value: int) -> None:
        now = time.monotonic()
        if code == KEY_LEFTALT:
            if value == 1:  # key down
                self._alt_down_at = now
                self._alt_disqualified = False
            elif value == 0 and self._alt_down_at > 0:  # key up
                duration_ms = (now - self._alt_down_at) * 1000.0
                qualified = (not self._alt_disqualified) and duration_ms <= self.max_tap_ms
                self._alt_down_at = 0.0
                self._alt_disqualified = False
                if qualified:
                    self._tap()
            return

        # Any non-Alt key press while Alt is held makes this a modifier chord,
        # not a standalone Alt tap.
        if self._alt_down_at > 0 and value == 1:
            self._alt_disqualified = True
            self._last_tap = 0.0

    def _loop(self) -> None:
        next_scan = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_scan:
                self._open_devices()
                next_scan = now + 5.0

            if not self._fds:
                self._stop.wait(0.5)
                continue

            poller = select.poll()
            for fd in self._fds:
                try:
                    poller.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)
                except Exception:
                    pass

            try:
                events = poller.poll(500)
            except Exception:
                events = []

            rescan = False
            for fd, mask in events:
                if mask & (select.POLLERR | select.POLLHUP):
                    rescan = True
                    continue
                if not (mask & select.POLLIN):
                    continue
                try:
                    data = os.read(fd, _INPUT_EVENT.size * 64)
                except (BlockingIOError, OSError):
                    continue
                for off in range(0, len(data) - _INPUT_EVENT.size + 1, _INPUT_EVENT.size):
                    _, _, typ, code, value = _INPUT_EVENT.unpack_from(data, off)
                    if typ == EV_KEY:
                        self._handle_key(code, value)

            if rescan:
                self._open_devices()

        self.available = False
        self._close_fds()
