from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import threading
import time
from pathlib import Path

from tabby.audio_meter import AudioMeter
from tabby.ipc import IPCServer
from tabby.state import TabbyState
from tabby.zen import ZenClient

CONFIG_PATH = Path.home() / ".config/tabby/config.json"
DEFAULTS = {
    "enabled": True,
    "debug_engine": False,
    "auto_hide_seconds": 5,
    "mouth_sensitivity": 1.8,
    "hover_text_input": True,
}


def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    except Exception:
        data = {}
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        out.update(data)
    return out


class TabbyBackend:
    def __init__(self):
        self.config = load_config()
        self.enabled = bool(self.config.get("enabled", True))
        self.debug = bool(self.config.get("debug_engine", False))
        self.auto_hide = max(2.0, min(30.0, float(self.config.get("auto_hide_seconds", 5))))
        self.state = TabbyState(self.enabled)
        self.voice = ZenClient(self.debug)
        self.ipc = IPCServer(self.handle)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._generation = 0
        self._voice_active = False
        self._text_session = False
        self._seen_voice_active = False
        self._audio_level = 0.0
        self._last_sound = 0.0
        self._hide_timer: threading.Timer | None = None
        self._end_done = threading.Event()
        self._end_done.set()
        sensitivity = max(.5, min(4.0, float(self.config.get("mouth_sensitivity", 1.8))))
        self.audio = AudioMeter(self._on_audio, sensitivity=sensitivity)
        self._monitor = threading.Thread(target=self._monitor_loop, name="tabby-monitor", daemon=True)

    def start(self):
        self.ipc.start()
        self.audio.start()
        self._monitor.start()
        threading.Thread(target=self.voice.set_debug, args=(self.debug,), name="tabby-debug-sync", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._cancel_hide()
        self.audio.stop()
        self.ipc.stop()
        try: self.voice.end()
        except Exception: pass

    def _cancel_hide(self):
        if self._hide_timer:
            self._hide_timer.cancel(); self._hide_timer = None

    def _schedule_hide(self, delay=None):
        self._cancel_hide()
        delay = self.auto_hide if delay is None else float(delay)
        gen = self._generation
        def run():
            with self._lock:
                if gen != self._generation or self._voice_active or self.state.snapshot().get("inputArmed"):
                    return
            self.close()
        self._hide_timer = threading.Timer(delay, run); self._hide_timer.daemon = True; self._hide_timer.start()

    def _valid(self, generation):
        with self._lock:
            return generation == self._generation and self.state.snapshot().get("summoned")

    def _new_generation(self):
        with self._lock:
            self._generation += 1
            return self._generation

    def wake(self):
        if not self.enabled:
            return {"ok": False, "error": "Tabby disabled"}
        with self._lock:
            if self.state.snapshot().get("summoned"):
                return {"ok": True, "result": "already-summoned"}
            generation = self._new_generation()
            self._voice_active = False; self._text_session = False; self._seen_voice_active = False
            self._cancel_hide()
            self.state.update(summoned=True, state="wake", inputArmed=False, attachmentPending=False, audioLevel=0.0)
        threading.Thread(target=self._start_voice, args=(generation,), name="tabby-start-voice", daemon=True).start()
        return {"ok": True, "result": "waking"}

    def _start_voice(self, generation):
        # Never let cleanup from the previous session race ahead of this new
        # chat and end it after activation.
        self._end_done.wait(timeout=10.0)
        if not self._valid(generation): return
        fresh = self.voice.new_chat()
        if not self._valid(generation): return
        if fresh.get("loggedOut") or fresh.get("result") == "needs-login":
            self.state.update(state="approval")
            self.voice.set_debug(True)
            return
        if not fresh.get("ok"):
            self.state.update(state="error"); self._schedule_hide(); return
        self.state.update(state="wake")
        result = self.voice.activate()
        if not self._valid(generation): return
        if result.get("ok") and result.get("active"):
            with self._lock:
                self._voice_active = True; self._seen_voice_active = True
            self.state.update(state="listening")
        elif result.get("loggedOut") or result.get("result") == "needs-login":
            self.state.update(state="approval"); self.voice.set_debug(True)
        else:
            self.state.update(state="error"); self._schedule_hide()

    def toggle_input(self):
        if not self.enabled: return {"ok": False, "error": "Tabby disabled"}
        with self._lock:
            snap = self.state.snapshot()
            if snap.get("summoned"):
                armed = not bool(snap.get("inputArmed"))
                self.state.update(inputArmed=armed)
                if armed: self._cancel_hide()
                elif not self._voice_active: self._schedule_hide()
                return {"ok": True, "result": "input-on" if armed else "input-off"}
            generation = self._new_generation()
            self._voice_active = False; self._text_session = True; self._seen_voice_active = False
            self._cancel_hide()
            self.state.update(summoned=True, state="wake", inputArmed=True, attachmentPending=False, audioLevel=0.0)
        threading.Thread(target=self._start_text_session, args=(generation,), name="tabby-start-text", daemon=True).start()
        return {"ok": True, "result": "opening-input"}

    def _start_text_session(self, generation):
        self._end_done.wait(timeout=10.0)
        if not self._valid(generation): return
        result = self.voice.new_chat()
        if not self._valid(generation): return
        if result.get("loggedOut") or result.get("result") == "needs-login":
            self.state.update(state="approval"); self.voice.set_debug(True); return
        if result.get("ok"):
            self.state.update(state="idle")
        else:
            self.state.update(state="error"); self._schedule_hide()

    def send_text(self, text):
        text = str(text or "").strip()
        if len(text) > 12000: text = text[:12000]
        if not text:
            # Empty enter still submits a previously attached image if ChatGPT
            # currently enables its send button.
            text = ""
        if not self.state.snapshot().get("summoned"):
            self.toggle_input()
            time.sleep(.1)
        generation = self._generation
        self._cancel_hide()
        self.state.update(state="thinking", inputArmed=True)
        def work():
            result = self.voice.send_text(text)
            if not self._valid(generation): return
            if result.get("ok"):
                self.state.update(state="thinking", attachmentPending=False)
                with self._lock: self._text_session = True
            else:
                self.state.update(state="error"); self._schedule_hide()
        threading.Thread(target=work, name="tabby-send-text", daemon=True).start()
        return {"ok": True, "result": "queued"}

    def paste_clipboard(self):
        generation = self._generation
        if not self.state.snapshot().get("summoned"):
            self.toggle_input(); generation = self._generation
        self._cancel_hide()
        self.state.update(attachmentPending=True)
        def work():
            try:
                types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=2).stdout.splitlines()
                preferred = next((x for x in ("image/png","image/jpeg","image/webp") if x in types), None)
                if not preferred:
                    # Ordinary text clipboard paste is handled natively by the
                    # QML text field. This helper only supplements Ctrl+V when
                    # the clipboard actually contains an image.
                    if self._valid(generation): self.state.update(attachmentPending=False)
                    return
                data = subprocess.run(["wl-paste", f"--type={preferred}"], capture_output=True, timeout=5).stdout
                if not data or len(data) > 16 * 1024 * 1024:
                    if self._valid(generation): self.state.update(attachmentPending=False, state="error")
                    return
                ext = mimetypes.guess_extension(preferred) or ".png"
                result = self.voice.paste_image(data, preferred, "tabby-clipboard" + ext)
                if not self._valid(generation): return
                if result.get("ok"):
                    self.state.update(attachmentPending=True, state="idle", inputArmed=True)
                else:
                    self.state.update(attachmentPending=False, state="error")
            except Exception:
                if self._valid(generation): self.state.update(attachmentPending=False, state="error")
        threading.Thread(target=work, name="tabby-paste-clipboard", daemon=True).start()
        return {"ok": True, "result": "queued"}

    def close(self):
        with self._lock:
            self._generation += 1
            self._voice_active = False; self._text_session = False; self._seen_voice_active = False
            self._cancel_hide()
            self.state.update(summoned=False, state="idle", inputArmed=False, audioLevel=0.0, attachmentPending=False, whiteboardVisible=False, items=[])
        self._end_done.clear()
        def finish_previous_session():
            try:
                self.voice.end()
                if not self.debug:
                    self.voice.hide()
            finally:
                self._end_done.set()
        threading.Thread(target=finish_previous_session, name="tabby-end", daemon=True).start()
        return {"ok": True, "result": "closed"}

    def _on_audio(self, level):
        with self._lock:
            self._audio_level = float(level)
            active = self._voice_active and self.state.snapshot().get("summoned")
            if level > .035: self._last_sound = time.monotonic()
        if not active:
            return
        self.state.update(audioLevel=round(float(level), 4))

    def _monitor_loop(self):
        inactive_since = None
        while not self._stop.wait(.4):
            snap = self.state.snapshot()
            if not snap.get("summoned"):
                inactive_since = None
                continue
            status = self.voice.status()
            if not status.get("ok"):
                continue
            # Debug visibility is a Tabby setting, not transient Zen state.
            # Re-assert it after a Zen/browser restart.
            if bool(status.get("debugVisible")) != self.debug:
                self.voice.set_debug(self.debug)
                status = self.voice.status()
                if not status.get("ok"):
                    continue
            # The status request may have been in flight while X/close changed
            # the generation. Re-read visibility before applying its result so
            # an old browser poll can never resurrect a closed face/state.
            if not self.state.snapshot().get("summoned"):
                inactive_since = None
                continue
            active = bool(status.get("active"))
            working = bool(status.get("working"))
            with self._lock:
                if active:
                    self._voice_active = True; self._seen_voice_active = True; inactive_since = None
                elif self._voice_active:
                    if inactive_since is None: inactive_since = time.monotonic()
                    elif time.monotonic() - inactive_since > 1.5:
                        self._voice_active = False
                        if self._seen_voice_active:
                            self.close(); continue
                level = self._audio_level
                voice_active = self._voice_active
                text_session = self._text_session

            if voice_active:
                if level > .035 or time.monotonic() - self._last_sound < .20:
                    desired = "speaking"
                elif working:
                    desired = "thinking"
                else:
                    desired = "listening"
                self.state.set_state(desired)
            elif text_session:
                if working:
                    self._cancel_hide(); self.state.set_state("thinking")
                elif snap.get("state") == "thinking":
                    self.state.set_state("success")
                    if not snap.get("inputArmed"): self._schedule_hide()
            elif working:
                self.state.set_state("thinking")

    def handle(self, request):
        command = str(request.get("command", "")).strip().lower()
        if command == "status": return {"ok": True, "state": self.state.snapshot()}
        if command == "wake": return self.wake()
        if command == "close": return self.close()
        if command in {"toggle-input", "toggle_input"}: return self.toggle_input()
        if command == "show-input": self.state.update(inputArmed=True, summoned=True); self._cancel_hide(); return {"ok":True}
        if command == "hide-input": self.state.update(inputArmed=False); self._schedule_hide(); return {"ok":True}
        if command == "send-text": return self.send_text(request.get("text", ""))
        if command == "paste-clipboard": return self.paste_clipboard()
        if command == "debug-on": self.debug=True; return self.voice.set_debug(True)
        if command == "debug-off": self.debug=False; return self.voice.set_debug(False)
        if command == "state": self.state.set_state(request.get("value","idle")); return {"ok":True}
        if command in {"show","hide","clear","text","progress","choice","shape"}: return self.state.whiteboard(request)
        return {"ok": False, "error": "unsupported command"}


def main():
    backend = TabbyBackend(); backend.start()
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        backend.stop()

if __name__ == "__main__": main()
