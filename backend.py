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
SESSION_PATH = Path.home() / ".local/state/tabby/session.json"
DEFAULT_STARTUP_PROMPT = (
    "You are Tabby, my desktop companion. Keep voice replies concise and natural. "
    "Use available tools when I ask you to act on my computer. Treat these as guidance "
    "for this conversation and do not explain them unless I ask."
)
DEFAULTS = {
    "enabled": True,
    "debug_engine": False,
    "auto_hide_seconds": 5,
    "mouth_sensitivity": 1.8,
    "hover_text_input": True,
    "session_mode": "smart",
    "smart_new_chat_minutes": 60,
    "startup_prompt_enabled": True,
    "startup_prompt": DEFAULT_STARTUP_PROMPT,
    "text_reply_mode": "text-only",
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
        self.session_mode = str(self.config.get("session_mode", "smart")).strip().lower()
        if self.session_mode not in {"smart", "continue", "new"}: self.session_mode = "smart"
        self.smart_new_chat_minutes = max(1, min(1440, int(self.config.get("smart_new_chat_minutes", 60))))
        self.startup_prompt_enabled = bool(self.config.get("startup_prompt_enabled", True))
        self.startup_prompt = str(self.config.get("startup_prompt", DEFAULT_STARTUP_PROMPT) or "").strip()[:12000]
        self.text_reply_mode = str(self.config.get("text_reply_mode", "text-only")).strip().lower()
        if self.text_reply_mode not in {"always", "text-only", "never"}: self.text_reply_mode = "text-only"
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
        self._engine_lock = threading.RLock()
        self._prewarm_lock = threading.RLock()
        self._prewarm_ready = threading.Event()
        self._prewarm_done = threading.Event(); self._prewarm_done.set()
        self._prewarm_inflight = False
        self._prewarm_new = False
        self._prewarm_session_stamp = 0.0
        self._next_hidden_prewarm_check = 0.0
        self._last_assistant_count = 0
        self._last_assistant_text = ""
        self._text_reply_pending = False
        self._text_reply_seen_change = False
        self._response_stable_ticks = 0
        self._next_response_poll = 0.0
        sensitivity = max(.5, min(4.0, float(self.config.get("mouth_sensitivity", 1.8))))
        self.audio = AudioMeter(self._on_audio, sensitivity=sensitivity)
        self._monitor = threading.Thread(target=self._monitor_loop, name="tabby-monitor", daemon=True)

    def start(self):
        self.ipc.start()
        self.audio.start()
        self._monitor.start()
        threading.Thread(target=self.voice.set_debug, args=(self.debug,), name="tabby-debug-sync", daemon=True).start()
        self._schedule_prewarm(.15)

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

    def _session_stamp(self):
        return float(self._read_session_meta().get("last_used") or 0)

    def _hidden_valid(self):
        return not self._stop.is_set() and not self.state.snapshot().get("summoned")

    def _schedule_prewarm(self, delay=0.0):
        if not self.enabled or self._stop.is_set():
            return
        with self._prewarm_lock:
            if self._prewarm_inflight:
                return
            self._prewarm_inflight = True
            self._prewarm_done.clear()

        def work():
            try:
                if delay > 0 and self._stop.wait(delay):
                    return
                if not self._hidden_valid():
                    return
                need_new = self._should_start_new()
                stamp = self._session_stamp()
                with self._engine_lock:
                    if not self._hidden_valid():
                        return
                    created_new = bool(need_new)
                    result = self.voice.new_chat() if need_new else self.voice.continue_chat()
                    if not self._hidden_valid():
                        return
                    if result.get("loggedOut") or result.get("result") == "needs-login" or not result.get("ok"):
                        return
                    href = str(result.get("href") or "")
                    if (not need_new) and "local-chatgpt" in href:
                        result = self.voice.new_chat()
                        created_new = True
                        if not result.get("ok"):
                            return
                    if created_new and self.startup_prompt_enabled and self.startup_prompt:
                        if not self._send_startup_prompt(valid_fn=self._hidden_valid):
                            return
                    status = self.voice.status()
                    if not self._hidden_valid() or not status.get("ok") or status.get("loggedOut") or not status.get("composerReady"):
                        return
                    with self._prewarm_lock:
                        self._prewarm_new = bool(need_new)
                        self._prewarm_session_stamp = stamp
                        self._prewarm_ready.set()
            finally:
                with self._prewarm_lock:
                    self._prewarm_inflight = False
                    self._prewarm_done.set()

        threading.Thread(target=work, name="tabby-prewarm", daemon=True).start()

    def _prewarm_matches(self, force_new=False):
        if not self._prewarm_ready.is_set():
            return False
        need_new = True if force_new else self._should_start_new()
        with self._prewarm_lock:
            if bool(self._prewarm_new) != bool(need_new):
                return False
            if self.session_mode != "new" and self._prewarm_session_stamp != self._session_stamp():
                return False
        return True

    def _consume_prewarm(self, force_new=False):
        if self._prewarm_inflight:
            self._prewarm_done.wait(timeout=1.5)
        if not self._prewarm_matches(force_new):
            return None
        with self._engine_lock:
            status = self.voice.status()
        href = str(status.get("href") or "")
        if (not status.get("ok") or status.get("loggedOut") or not status.get("composerReady")
                or "local-chatgpt" in href):
            self._prewarm_ready.clear()
            return None
        self._prewarm_ready.clear()
        status = dict(status)
        status["ok"] = True
        status["result"] = "prewarmed-ready"
        return status

    def _prime_response_cursor(self):
        try:
            result = self.voice.latest_response()
            if result.get("ok"):
                with self._lock:
                    self._last_assistant_count = int(result.get("assistantCount") or 0)
                    self._last_assistant_text = str(result.get("assistantText") or "")
        except Exception:
            pass

    def _show_assistant_text(self, text):
        text = str(text or "").strip()
        if not text:
            return
        snap = self.state.snapshot()
        items = [i for i in list(snap.get("items") or []) if not (isinstance(i, dict) and i.get("source") == "assistant-reply")]
        items.append({"type":"text", "source":"assistant-reply", "title":"Tabby", "text":text[:2400]})
        self.state.update(items=items[-32:], whiteboardVisible=True)

    def _monitor_text_reply(self, status):
        if self.text_reply_mode == "never":
            return
        with self._lock:
            pending = self._text_reply_pending
        if self.text_reply_mode == "text-only" and not pending:
            return
        now = time.monotonic()
        if now < self._next_response_poll:
            return
        self._next_response_poll = now + .55
        try:
            response = self.voice.latest_response()
        except Exception:
            return
        if not response.get("ok"):
            return
        count = int(response.get("assistantCount") or 0)
        text = str(response.get("assistantText") or "").strip()
        with self._lock:
            changed = bool(text) and (count != self._last_assistant_count or text != self._last_assistant_text)
            if changed:
                self._last_assistant_count = count
                self._last_assistant_text = text
                self._text_reply_seen_change = True
                self._response_stable_ticks = 0
            elif text:
                self._response_stable_ticks += 1
            stable = self._response_stable_ticks
            seen_change = self._text_reply_seen_change
        if changed:
            self._show_assistant_text(text)
        if pending and seen_change and not bool(status.get("working")) and text and stable >= 1:
            with self._lock:
                self._text_reply_pending = False
                self._response_stable_ticks = 0

    def _read_session_meta(self):
        try:
            data = json.loads(SESSION_PATH.read_text()) if SESSION_PATH.exists() else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _touch_session(self):
        try:
            SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = self._read_session_meta()
            data["last_used"] = time.time()
            tmp = SESSION_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n")
            os.chmod(tmp, 0o600)
            tmp.replace(SESSION_PATH)
        except Exception:
            pass

    def _should_start_new(self, force_new=False):
        if force_new or self.session_mode == "new":
            return True
        if self.session_mode == "continue":
            return False
        meta = self._read_session_meta()
        last = float(meta.get("last_used") or 0)
        if last > 0:
            return time.time() - last > self.smart_new_chat_minutes * 60
        # First run after upgrading: keep an already-open conversation if the
        # engine is visibly on a conversation route; otherwise start fresh.
        try:
            status = self.voice.status()
            href = str(status.get("href") or "")
            return "/c/" not in href
        except Exception:
            return True

    def _send_startup_prompt(self, generation=None, valid_fn=None):
        if not self.startup_prompt_enabled or not self.startup_prompt:
            return True
        if valid_fn is None:
            valid_fn = lambda: self._valid(generation)
        prompt = (
            "Startup instructions for this Tabby conversation:\n"
            + self.startup_prompt
            + "\nTreat this as guidance for the rest of this conversation."
            + "\nAcknowledge that these instructions are loaded by replying with exactly TABBY_READY."
        )
        result = self.voice.send_text(prompt)
        if not result.get("ok"):
            return False
        # ChatGPT first assigns an optimistic /c/local-chatgpt:... route. That
        # route can expose the Voice control while still ignoring activation.
        # Wait for a persisted conversation + assistant acknowledgement.
        deadline = time.monotonic() + 45.0
        while valid_fn() and time.monotonic() < deadline:
            status = self.voice.status()
            if status.get("loggedOut"):
                return False
            href = str(status.get("href") or "")
            persisted = "/c/" in href and "local-chatgpt" not in href
            response = self.voice.latest_response() if persisted else {}
            ack = str(response.get("assistantText") or "").strip()
            if status.get("ok") and status.get("ready") and persisted and ack:
                return True
            time.sleep(.35)
        return False

    def _prepare_chat(self, generation, force_new=False):
        new_chat = self._should_start_new(force_new)
        result = self.voice.new_chat() if new_chat else self.voice.continue_chat()
        if not self._valid(generation):
            return result, new_chat
        if not result.get("ok") and not result.get("loggedOut"):
            # A stale/restored conversation can occasionally be unavailable.
            # Fall back to a clean chat rather than leaving Tabby wedged.
            result = self.voice.new_chat()
            new_chat = True
        if "local-chatgpt" in str(result.get("href") or ""):
            result = self.voice.new_chat()
            new_chat = True
        if result.get("fresh"):
            new_chat = True
        if result.get("ok") and new_chat and self.startup_prompt_enabled and self.startup_prompt:
            if not self._send_startup_prompt(generation):
                return {"ok": False, "result": "startup-prompt-failed"}, new_chat
        if result.get("ok"):
            self._touch_session()
        return result, new_chat

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

    def _start_voice(self, generation, force_new=False):
        # Never let cleanup from the previous session race ahead of this new
        # chat and end it after activation.
        self._end_done.wait(timeout=10.0)
        if not self._valid(generation): return
        fresh = self._consume_prewarm(force_new=force_new)
        if fresh is None:
            with self._engine_lock:
                fresh, _ = self._prepare_chat(generation, force_new=force_new)
        if not self._valid(generation): return
        if fresh.get("loggedOut") or fresh.get("result") == "needs-login":
            self.state.update(state="approval")
            self.voice.set_debug(True)
            return
        if not fresh.get("ok"):
            self.state.update(state="error"); self._schedule_hide(); return
        self.state.update(state="wake")
        self._prime_response_cursor()
        deadline = time.monotonic() + 14.0
        clicked = False
        last_activate = 0.0
        while self._valid(generation) and time.monotonic() < deadline:
            now = time.monotonic()
            if not clicked or now - last_activate > 1.2:
                result = self.voice.activate()
                last_activate = now
                if not self._valid(generation): return
                if result.get("loggedOut") or result.get("result") == "needs-login":
                    self.state.update(state="approval"); self.voice.set_debug(True); return
                if result.get("ok"):
                    clicked = True
                    if result.get("active"):
                        with self._lock:
                            self._voice_active = True; self._seen_voice_active = True
                        self._touch_session()
                        self.state.update(state="listening")
                        return

            status = self.voice.status()
            if not self._valid(generation): return
            if status.get("loggedOut") or status.get("result") == "needs-login":
                self.state.update(state="approval"); self.voice.set_debug(True); return
            if status.get("ok") and status.get("active"):
                with self._lock:
                    self._voice_active = True; self._seen_voice_active = True
                self._touch_session()
                self.state.update(state="listening")
                return
            # If the page is Voice-ready again after a text/setup rehydrate,
            # allow another trusted click instead of treating the first miss as fatal.
            if status.get("ready"):
                clicked = False
            time.sleep(.25)

        if self._valid(generation):
            self.state.update(state="error"); self._schedule_hide()


    def toggle(self):
        """Global summon hotkey: Voice + hover text input, press again to close."""
        if not self.enabled:
            return {"ok": False, "error": "Tabby disabled"}
        if self.state.snapshot().get("summoned"):
            return self.close()
        result = self.wake()
        if result.get("ok"):
            # Keep the composer available on hover while using the same Voice
            # startup path as the Hey Tabby wakeword.
            self.state.update(inputArmed=True)
            return {"ok": True, "result": "waking-with-input"}
        return result

    def toggle_input(self):
        if not self.enabled: return {"ok": False, "error": "Tabby disabled"}
        with self._lock:
            snap = self.state.snapshot()
            if snap.get("summoned"):
                # The global Tabby hotkey is a true summon toggle: pressing it
                # while Tabby is visible closes the whole companion/session,
                # regardless of whether it was opened for text or voice.
                return self.close()
            generation = self._new_generation()
            self._voice_active = False; self._text_session = True; self._seen_voice_active = False
            self._cancel_hide()
            self.state.update(summoned=True, state="wake", inputArmed=True, attachmentPending=False, audioLevel=0.0)
        threading.Thread(target=self._start_text_session, args=(generation,), name="tabby-start-text", daemon=True).start()
        return {"ok": True, "result": "opening-input"}

    def _start_text_session(self, generation):
        self._end_done.wait(timeout=10.0)
        if not self._valid(generation): return
        result = self._consume_prewarm(force_new=False)
        if result is None:
            with self._engine_lock:
                result, _ = self._prepare_chat(generation)
        if not self._valid(generation): return
        if result.get("loggedOut") or result.get("result") == "needs-login":
            self.state.update(state="approval"); self.voice.set_debug(True); return
        if result.get("ok"):
            self._touch_session()
            self.state.update(state="idle")
        else:
            self.state.update(state="error"); self._schedule_hide()

    def new_session(self):
        if not self.enabled:
            return {"ok": False, "error": "Tabby disabled"}
        if self.state.snapshot().get("summoned"):
            self.close()
        with self._lock:
            generation = self._new_generation()
            self._voice_active = False; self._text_session = False; self._seen_voice_active = False
            self._cancel_hide()
            self.state.update(summoned=True, state="wake", inputArmed=True, attachmentPending=False, audioLevel=0.0)
        threading.Thread(target=self._start_voice, args=(generation, True), name="tabby-new-session", daemon=True).start()
        return {"ok": True, "result": "new-session"}

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
        self._touch_session()
        self._cancel_hide()
        self.state.update(state="thinking", inputArmed=True)
        def work():
            self._prime_response_cursor()
            with self._lock:
                self._text_reply_pending = self.text_reply_mode in {"always", "text-only"}
                self._text_reply_seen_change = False
                self._response_stable_ticks = 0
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
        self._touch_session()
        with self._lock:
            self._generation += 1
            self._voice_active = False; self._text_session = False; self._seen_voice_active = False
            self._text_reply_pending = False
            self._text_reply_seen_change = False
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
                self._prewarm_ready.clear()
                self._schedule_prewarm(.2)
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
                now = time.monotonic()
                if now >= self._next_hidden_prewarm_check:
                    self._next_hidden_prewarm_check = now + 5.0
                    need_new = self._should_start_new()
                    with self._prewarm_lock:
                        mismatch = self._prewarm_ready.is_set() and bool(self._prewarm_new) != bool(need_new)
                    if mismatch:
                        self._prewarm_ready.clear()
                    if (not self._prewarm_ready.is_set()) and (not self._prewarm_inflight):
                        self._schedule_prewarm(0)
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

            self._monitor_text_reply(status)

    def handle(self, request):
        command = str(request.get("command", "")).strip().lower()
        if command == "status":
            return {
                "ok": True, "state": self.state.snapshot(),
                "prewarmReady": self._prewarm_ready.is_set(),
                "prewarmInflight": self._prewarm_inflight,
                "prewarmNew": self._prewarm_new,
                "textReplyMode": self.text_reply_mode,
            }
        if command == "wake": return self.wake()
        if command == "close": return self.close()
        if command in {"toggle", "toggle-summon", "toggle_summon"}: return self.toggle()
        if command in {"new-session", "new_session", "new-chat"}: return self.new_session()
        if command in {"toggle-input", "toggle_input"}: return self.toggle_input()
        if command == "show-input": self.state.update(inputArmed=True, summoned=True); self._cancel_hide(); return {"ok":True}
        if command == "hide-input": self.state.update(inputArmed=False); self._schedule_hide(); return {"ok":True}
        if command == "send-text": return self.send_text(request.get("text", ""))
        if command == "paste-clipboard": return self.paste_clipboard()
        if command == "debug-on":
            self.debug = True
            threading.Thread(target=self.voice.set_debug, args=(True,), name="tabby-debug-on", daemon=True).start()
            return {"ok": True, "result": "debug-on"}
        if command == "debug-off":
            self.debug = False
            threading.Thread(target=self.voice.set_debug, args=(False,), name="tabby-debug-off", daemon=True).start()
            return {"ok": True, "result": "debug-off"}
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
