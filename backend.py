from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import threading
import time
from pathlib import Path

from tabby.audio_meter import AudioMeter
from tabby.fn_hotkey import FnHotkeyMonitor, LeftAltHotkeyMonitor
from tabby.ipc import IPCServer
from tabby.state import TabbyState
from tabby.zen import ZenClient
from tabby.working import WorkingStore

CONFIG_PATH = Path.home() / ".config/tabby/config.json"
SESSION_PATH = Path.home() / ".local/state/tabby/session.json"
DEFAULT_STARTUP_PROMPT = (
    "Keep voice replies concise and natural. "
    "Use available tools when I ask you to act on my computer. Treat these as guidance "
    "for this conversation and do not explain them unless I ask."
)
DEFAULTS = {
    "assistant_name": "Loom",
    "wake_phrase": "Hey Loom",
    "close_phrase": "Bye Loom",
    "wake_aliases": "Hey Lume, Hey Lumi, Hey Lum, Hey Loam, Hello Loom",
    "close_aliases": "Bye Lume, Bye Lum, Goodbye Loom, By Loom",
    "enabled": True,
    "debug_engine": False,
    "auto_hide_seconds": 5,
    "mouth_sensitivity": 1.8,
    "hover_text_input": True,
    "session_mode": "continue",
    "smart_new_chat_minutes": 60,
    "startup_prompt_enabled": False,
    "background_prewarm_enabled": False,
    "startup_prompt": DEFAULT_STARTUP_PROMPT,
    "text_reply_mode": "text-only",
    "hotkey_mode": "double-left-alt",
    "double_tap_ms": 350,
    "fn_double_tap_ms": 350,
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
        self.session_mode = str(self.config.get("session_mode", "continue")).strip().lower()
        if self.session_mode not in {"smart", "continue", "new"}: self.session_mode = "smart"
        self.smart_new_chat_minutes = max(1, min(1440, int(self.config.get("smart_new_chat_minutes", 60))))
        self.startup_prompt_enabled = bool(self.config.get("startup_prompt_enabled", False))
        self.background_prewarm_enabled = bool(self.config.get("background_prewarm_enabled", False))
        self.assistant_name = str(self.config.get("assistant_name") or "Loom").strip()[:60] or "Loom"
        legacy_prompt = str(self.config.get("startup_prompt", DEFAULT_STARTUP_PROMPT) or "").strip()
        if legacy_prompt.startswith(("You are Tabby,", "You are Lume,")):
            legacy_prompt = DEFAULT_STARTUP_PROMPT
        self.startup_prompt = legacy_prompt[:12000]
        self.text_reply_mode = str(self.config.get("text_reply_mode", "text-only")).strip().lower()
        if self.text_reply_mode not in {"always", "text-only", "never"}: self.text_reply_mode = "text-only"
        self.hotkey_mode = str(self.config.get("hotkey_mode", "double-left-alt")).strip().lower()
        if self.hotkey_mode not in {"double-left-alt", "double-fn", "super-shift-space", "both"}: self.hotkey_mode = "double-left-alt"
        self.double_tap_ms = max(150, min(800, int(self.config.get("double_tap_ms", self.config.get("fn_double_tap_ms", 350)))))
        self.fn_double_tap_ms = self.double_tap_ms
        self.state = TabbyState(self.enabled)
        self.working = WorkingStore()
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
        self._prewarm_href = ""
        self._next_hidden_prewarm_check = 0.0
        self._last_assistant_count = 0
        self._last_assistant_text = ""
        self._text_reply_pending = False
        self._text_reply_seen_change = False
        self._voice_text_handoff = False
        self._response_stable_ticks = 0
        self._next_response_poll = 0.0
        self._local_voice_fallback = False
        self._local_voice_busy = False
        self._local_stt_proc = None
        self._local_stt_thread = None
        self._local_stt_generation = 0
        sensitivity = max(.5, min(4.0, float(self.config.get("mouth_sensitivity", 1.8))))
        self.audio = AudioMeter(self._on_audio, sensitivity=sensitivity)
        self.fn_hotkey = FnHotkeyMonitor(self._on_fn_double_tap, self.double_tap_ms)
        self.alt_hotkey = LeftAltHotkeyMonitor(self._on_left_alt_double_tap, self.double_tap_ms)
        self._monitor = threading.Thread(target=self._monitor_loop, name="tabby-monitor", daemon=True)
        self._workspace_visibility = threading.Thread(target=self._workspace_visibility_loop, name="tabby-workspace-visibility", daemon=True)
        self._working_idle_ticks = {}
        self._working_retry_at = {}
        self._active_work_task_id = ""
        self._active_work_baseline_count = 0
        self._active_work_saw_working = False
        self._last_work_open = {}
        self._working_monitor = threading.Thread(target=self._working_monitor_loop, name="tabby-working-monitor", daemon=True)
        self.state.update(working=self.working.list())

    def start(self):
        self.ipc.start()
        self.audio.start()
        self.fn_hotkey.start()
        self.alt_hotkey.start()
        self.state.update(fnHotkeyAvailable=self.fn_hotkey.available, altHotkeyAvailable=self.alt_hotkey.available)
        self._monitor.start()
        self._workspace_visibility.start()
        self._working_monitor.start()
        threading.Thread(target=self.voice.set_debug, args=(self.debug,), name="tabby-debug-sync", daemon=True).start()
        self._schedule_prewarm(.15)

    def stop(self):
        self._stop.set()
        self._cancel_hide()
        self._stop_local_voice()
        self.audio.stop()
        self.fn_hotkey.stop()
        self.alt_hotkey.stop()
        self.ipc.stop()
        try: self.voice.end()
        except Exception: pass

    def _on_fn_double_tap(self):
        if not self.enabled or self.hotkey_mode not in {"double-fn", "both"}:
            return
        self.toggle()

    def _on_left_alt_double_tap(self):
        if not self.enabled or self.hotkey_mode not in {"double-left-alt", "both"}:
            return
        self.toggle()

    def fallback_hotkey(self):
        fn_available = bool(self.fn_hotkey.available)
        alt_available = bool(self.alt_hotkey.available)
        self.state.update(fnHotkeyAvailable=fn_available, altHotkeyAvailable=alt_available)
        if self.hotkey_mode == "double-left-alt" and alt_available:
            return {"ok": True, "result": "fallback-disabled"}
        if self.hotkey_mode == "double-fn" and fn_available:
            return {"ok": True, "result": "fallback-disabled"}
        # `both` explicitly keeps Super+Shift+Space active alongside Double Alt.
        return self.toggle()

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

    def _set_force_new_next(self, value=True):
        self._update_session_meta(force_new_next=bool(value))

    def _clear_force_new_next(self):
        # Consuming the one-shot "new next" flag must not discard an unused
        # prepared chat. `_consume_prewarm` owns clearing prepared_chat_url.
        self._update_session_meta(force_new_next=False)

    def _session_stamp(self):
        return float(self._read_session_meta().get("last_used") or 0)

    def _hidden_valid(self):
        return not self._stop.is_set() and not self.state.snapshot().get("summoned")

    def _schedule_prewarm(self, delay=0.0):
        if not self.enabled or self._stop.is_set() or not getattr(self, "background_prewarm_enabled", False):
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
                # Never create a new ChatGPT conversation from idle prewarming.
                # Only an actual summon / explicit new request may do so.
                if need_new:
                    return
                stamp = self._session_stamp()
                with self._engine_lock:
                    if not self._hidden_valid():
                        return
                    meta = self._read_session_meta()
                    prepared_url = str(meta.get("prepared_chat_url") or "")
                    last_url = str(meta.get("last_chat_url") or "")
                    reuse_prepared = bool("/c/" in prepared_url and "local-chatgpt" not in prepared_url)
                    reuse_last = bool((not need_new) and "/c/" in last_url and "local-chatgpt" not in last_url)
                    resume_url = prepared_url if reuse_prepared else (last_url if reuse_last else "")
                    created_new = bool(need_new and not reuse_prepared)
                    result = self._resume_chat_in_place(resume_url) if resume_url else (self.voice.new_chat() if need_new else self.voice.continue_chat())
                    if not self._hidden_valid():
                        return
                    if result.get("loggedOut") or result.get("result") == "needs-login" or not result.get("ok"):
                        return
                    href = str(result.get("href") or "")
                    # A restarted engine can only expose the canonical blank
                    # composer even though Smart still regards the prior session
                    # as recent. `continue-chat` marks that as fresh=true. Treat
                    # it as a newly-created fallback so startup guidance is
                    # loaded before the next summon.
                    if result.get("fresh") and not reuse_prepared:
                        created_new = True
                    if (not need_new) and "local-chatgpt" in href:
                        result = self.voice.new_chat()
                        created_new = True
                        if not result.get("ok"):
                            return
                    # force_new_next is a one-shot ownership handoff. Consume it
                    # as soon as the replacement conversation exists; Voice
                    # activation may fail later and must not create another
                    # startup-only chat on every retry.
                    if created_new and result.get("ok"):
                        self._clear_force_new_next()
                    # No automatic startup messages while hidden. They create
                    # orphan "Acknowledge" conversations without user input.
                    status = self.voice.status()
                    # A prewarm slot is useful for Voice only once the semantic
                    # Start Voice control is hydrated as well as the composer.
                    # If the page is otherwise healthy, let the bridge's bounded
                    # continue wait finish hydration without reloading the chat.
                    if not self._hidden_valid() or not status.get("ok") or status.get("loggedOut")                             or not status.get("composerReady") or status.get("working"):
                        return
                    status_href = str(status.get("href") or "")
                    if "/c/" in status_href and "local-chatgpt" not in status_href:
                        self._update_session_meta(last_chat_url=status_href)
                    if created_new and "/c/" in status_href and "local-chatgpt" not in status_href:
                        self._update_session_meta(prepared_chat_url=status_href)
                    with self._prewarm_lock:
                        self._prewarm_new = bool(need_new)
                        self._prewarm_session_stamp = stamp
                        self._prewarm_href = str(status.get("href") or "")
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
        # last_used is recency metadata, not browser identity. close()/monitor
        # can legitimately touch it after a prewarm without invalidating the
        # already-open conversation. _consume_prewarm validates the real href
        # and composer state before accepting the slot.
        return True

    def _consume_prewarm(self, force_new=False):
        if self._prewarm_inflight:
            self._prewarm_done.wait(timeout=1.5)
        if not self._prewarm_matches(force_new):
            return None
        with self._engine_lock:
            status = self.voice.status()
            href = str(status.get("href") or "")
            # A restored remote tab can swap WindowGlobal for a few hundred ms.
            # Do not throw away a prepared conversation on one actor-unavailable
            # sample: continue-chat is navigation-free and waits for the same
            # page/Voice controls to rehydrate.
            needs_stabilize = (
                not status.get("ok")
                or (status.get("ok") and not status.get("loggedOut") and not status.get("composerReady"))
            )
            if needs_stabilize:
                stabilized = self.voice.continue_chat()
                stabilized_href = str(stabilized.get("href") or "")
                if stabilized.get("ok") and stabilized_href == self._prewarm_href:
                    status = stabilized
                    href = stabilized_href
        if (not status.get("ok") or status.get("loggedOut") or not status.get("composerReady")
                or status.get("working") or "local-chatgpt" in href or href != self._prewarm_href):
            self._prewarm_ready.clear()
            return None
        self._prewarm_ready.clear()
        with self._prewarm_lock:
            was_new_slot = bool(self._prewarm_new)
        if was_new_slot:
            meta = self._read_session_meta()
            if str(meta.get("prepared_chat_url") or "") == href:
                self._update_session_meta(prepared_chat_url="")
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
        items.append({"type":"text", "source":"assistant-reply", "title":getattr(self, "assistant_name", "Loom"), "text":text[:2400]})
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

    def _update_session_meta(self, **values):
        try:
            SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = self._read_session_meta()
            data.update(values)
            tmp = SESSION_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n")
            os.chmod(tmp, 0o600)
            tmp.replace(SESSION_PATH)
        except Exception:
            pass

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
        if bool(meta.get("force_new_next")):
            return True
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
        if not self.startup_prompt_enabled:
            return True
        if valid_fn is None:
            valid_fn = lambda: self._valid(generation)
        prompt = (
            f"Startup instructions for this {self.assistant_name} conversation:\n"
            + f"You are {self.assistant_name}, my desktop companion. "
            + self.startup_prompt
            + "\nTreat this as guidance for the rest of this conversation."
            + "\nAcknowledge that these instructions are loaded by replying with exactly LOOM_READY."
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

    def _resume_chat_in_place(self, resume_url):
        """Reuse an already-open conversation without reloading the same /c/ URL.

        A transient WindowActor gap is treated as a process-swap/re-hydration
        event first. Only navigate when the engine is positively on another URL.
        """
        resume_url = str(resume_url or "")
        if not resume_url:
            return {}
        try:
            current = self.voice.status()
        except Exception:
            current = {}
        current_href = str(current.get("href") or "")
        if current.get("ok") and current_href == resume_url:
            # Healthy idle/working pages stay in place. A page on the correct
            # URL with no composer and no active generation is a broken/stalled
            # hydration state; continuing it just burns the full timeout over
            # and over, so reload that same conversation exactly once.
            if current.get("composerReady") or current.get("working") or current.get("connectionInterrupted"):
                candidate = self.voice.continue_chat()
                if candidate.get("ok"):
                    return candidate
            return self.voice.open_chat(resume_url)
        if not current.get("ok"):
            try:
                candidate = self.voice.continue_chat()
            except Exception:
                candidate = {}
            candidate_href = str(candidate.get("href") or "")
            if candidate.get("ok") and candidate_href == resume_url:
                return candidate
        return self.voice.open_chat(resume_url)

    def _prepare_chat(self, generation, force_new=False):
        new_chat = self._should_start_new(force_new)
        meta = self._read_session_meta()
        prepared_url = str(meta.get("prepared_chat_url") or "")
        last_url = str(meta.get("last_chat_url") or "")
        reuse_prepared = bool(not force_new and "/c/" in prepared_url and "local-chatgpt" not in prepared_url)
        reuse_last = bool((not force_new) and (not new_chat) and "/c/" in last_url and "local-chatgpt" not in last_url)
        resume_url = prepared_url if reuse_prepared else (last_url if reuse_last else "")
        if resume_url:
            result = self._resume_chat_in_place(resume_url)
        else:
            result = self.voice.new_chat() if new_chat else self.voice.continue_chat()
        if not self._valid(generation):
            return result, new_chat
        if not result.get("ok") and not result.get("loggedOut"):
            # A transient Zen or ChatGPT failure must never silently turn a
            # saved conversation into a fresh one.
            if resume_url or not new_chat:
                return result, new_chat
        if "local-chatgpt" in str(result.get("href") or "") and resume_url:
            # Optimistic local route: recover the durable conversation instead
            # of creating more chats on each retry.
            result = self.voice.open_chat(resume_url)
            if not result.get("ok"):
                return result, new_chat
        if result.get("fresh") and not reuse_prepared:
            new_chat = True
        # Consume the one-shot new-chat request when the new/prepared chat is
        # successfully acquired. Do not wait for Voice activation: if mic or
        # Voice startup fails, keeping this flag set causes repeated new chats
        # containing only the startup instructions.
        if result.get("ok") and new_chat:
            self._clear_force_new_next()
        if result.get("ok") and new_chat and not reuse_prepared and self.startup_prompt_enabled and self.startup_prompt:
            if not self._send_startup_prompt(generation):
                return {"ok": False, "result": "startup-prompt-failed"}, new_chat
        if result.get("ok"):
            href = str(result.get("href") or "")
            if "/c/" in href and "local-chatgpt" not in href:
                self._update_session_meta(last_chat_url=href)
            if reuse_prepared and href == prepared_url:
                self._update_session_meta(prepared_chat_url="")
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
            self.state.update(summoned=True, voiceActive=False, state="wake", inputArmed=False, attachmentPending=False, audioLevel=0.0)
        threading.Thread(target=self._start_voice, args=(generation,), name="tabby-start-voice", daemon=True).start()
        return {"ok": True, "result": "waking"}

    def _ensure_voice_mic_on(self):
        last = {}
        for _ in range(4):
            try:
                last = self.voice.mic_on()
            except Exception:
                last = {}
            if last.get("ok") and not last.get("micMuted"):
                return True
            time.sleep(.15)
        return False

    def _stop_local_voice(self):
        with self._lock:
            proc = self._local_stt_proc
            self._local_stt_proc = None
            self._local_voice_fallback = False
            self._local_voice_busy = False
            self._local_stt_generation += 1
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1.2)
            except Exception:
                try: proc.kill()
                except Exception: pass

    def _local_stt_control(self, command):
        with self._lock:
            proc = self._local_stt_proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(str(command).strip() + "\n")
            proc.stdin.flush()
            return True
        except Exception:
            return False

    def _wait_for_output_silence(self, start_timeout=3.0, max_duration=40.0):
        start = time.monotonic()
        heard = False
        silent_since = None
        while time.monotonic() - start < max_duration:
            with self._lock:
                level = float(self._audio_level)
            now = time.monotonic()
            if level > .035:
                heard = True
                silent_since = None
            elif heard:
                if silent_since is None:
                    silent_since = now
                elif now - silent_since >= .8:
                    return True
            elif now - start >= start_timeout:
                return False
            time.sleep(.08)
        return heard

    def _local_voice_send(self, text, generation):
        text = str(text or '').strip()
        if not text or not self._valid(generation):
            return
        with self._lock:
            if self._local_voice_busy or not self._local_voice_fallback:
                return
            self._local_voice_busy = True
        try:
            self._local_stt_control('pause')
            self.state.update(state='thinking', audioLevel=0.0)
            self._prime_response_cursor()
            with self._lock:
                baseline_count = self._last_assistant_count
                baseline_text = self._last_assistant_text
            result = self.voice.send_text(text)
            if not result.get('ok') or not self._valid(generation):
                self.state.set_state('error')
                return
            deadline = time.monotonic() + 60.0
            latest_text = ''
            while self._valid(generation) and time.monotonic() < deadline:
                reply = self.voice.latest_response()
                status = self.voice.status()
                count = int(reply.get('assistantCount') or 0)
                candidate = str(reply.get('assistantText') or '').strip()
                changed = bool(candidate) and (count != baseline_count or candidate != baseline_text)
                if changed:
                    latest_text = candidate
                    if not status.get('working'):
                        break
                time.sleep(.25)
            if latest_text:
                with self._lock:
                    self._last_assistant_count = int(reply.get('assistantCount') or 0)
                    self._last_assistant_text = latest_text
                self._show_assistant_text(latest_text)
                self.state.set_state('success')
                time.sleep(.18)
                spoken = {}
                try:
                    spoken = self.voice.read_aloud()
                except Exception:
                    spoken = {}
                if str(spoken.get('result') or '').startswith('read-aloud'):
                    self._wait_for_output_silence()
            if self._valid(generation):
                self.state.set_state('listening')
        finally:
            self._local_stt_control('resume')
            with self._lock:
                self._local_voice_busy = False

    def _local_stt_reader(self, proc, generation, worker_generation):
        try:
            for raw in proc.stdout or ():
                if not self._valid(generation):
                    break
                with self._lock:
                    if worker_generation != self._local_stt_generation or not self._local_voice_fallback:
                        break
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                kind = str(event.get('type') or '')
                if kind == 'level':
                    with self._lock:
                        busy = self._local_voice_busy
                    if not busy:
                        level = max(0.0, min(1.0, float(event.get('level') or 0.0)))
                        self.state.update(audioLevel=round(level, 4))
                elif kind == 'transcript':
                    text = str(event.get('text') or '').strip()
                    if text:
                        self._local_voice_send(text, generation)
                elif kind == 'error':
                    self.state.set_state('error')
        finally:
            with self._lock:
                if self._local_stt_proc is proc:
                    self._local_stt_proc = None

    def _start_local_voice(self, generation):
        if not self._valid(generation):
            return False
        self._stop_local_voice()
        try:
            ended = self.voice.end()
            if "active" in ended:
                self.state.update(voiceActive=bool(ended.get("active")))
        except Exception:
            pass
        root = Path(__file__).resolve().parent
        worker = root / 'tabby' / 'local_stt_worker.py'
        python = Path.home() / '.local/share/caelestia/plugin-runtime/protocol7/venv/bin/python'
        if not python.exists():
            return False
        try:
            log = open(Path.home() / '.cache/tabby/local-stt.log', 'a', buffering=1)
        except Exception:
            log = subprocess.DEVNULL
        try:
            proc = subprocess.Popen(
                [str(python), str(worker)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1,
            )
        except Exception:
            return False
        with self._lock:
            self._voice_active = False
            self._text_session = False
            self._local_voice_fallback = True
            self._local_voice_busy = False
            self._local_stt_proc = proc
            self._local_stt_generation += 1
            worker_generation = self._local_stt_generation
        self.state.update(state='listening', audioLevel=0.0)
        thread = threading.Thread(
            target=self._local_stt_reader,
            args=(proc, generation, worker_generation),
            name='tabby-local-stt', daemon=True,
        )
        self._local_stt_thread = thread
        thread.start()
        return True

    @staticmethod
    def _voice_has_live_mic(voice_state):
        for track in list((voice_state or {}).get("audioTracks") or []):
            if not isinstance(track, dict):
                continue
            if (
                str(track.get("kind") or "") == "audio"
                and str(track.get("readyState") or "") == "live"
                and track.get("enabled") is not False
            ):
                return True
        return False

    def _recover_voice_mic(self, generation, voice_state):
        """Confirm a real ChatGPT WebRTC mic track before declaring Voice ready.

        ChatGPT can report the Voice surface as active before getUserMedia has
        actually produced an audio track. Treat that as startup-in-progress, not
        success. Only fall back to Protocol7 after a few bounded recovery polls.
        """
        state = dict(voice_state or {})
        for attempt in range(10):
            if not self._valid(generation):
                return "failed"
            if (
                state.get("ok")
                and state.get("active")
                and not state.get("micMuted")
                and self._voice_has_live_mic(state)
            ):
                return "voice"

            # If ChatGPT exposed Voice muted (or the live track has not appeared
            # yet), nudge its semantic mic control a few times while the page
            # finishes hydrating. ensureMicrophoneOn is idempotent when already on.
            if state.get("active") and (state.get("micMuted") or attempt in {0, 3, 6}):
                try:
                    nudged = self.voice.mic_on()
                    if isinstance(nudged, dict) and nudged:
                        state = nudged
                except Exception:
                    pass
                if (
                    state.get("ok")
                    and state.get("active")
                    and not state.get("micMuted")
                    and self._voice_has_live_mic(state)
                ):
                    return "voice"

            time.sleep(.18)
            try:
                latest = self.voice.status()
                if isinstance(latest, dict) and latest:
                    state = latest
            except Exception:
                pass

        if self._start_local_voice(generation):
            return "fallback"
        return "failed"

    def _fallback_to_text_session(self, generation, status=None):
        """Keep Tabby usable when ChatGPT Voice cannot start.

        Voice quota dialogs and other Voice-only blockers must never take the
        hover composer down with them. Preserve the current conversation, clear
        any transient Voice modal with a same-chat reload when possible, and
        continue as a normal text session.
        """
        if not self._valid(generation):
            return False
        current = dict(status or {})
        href = str(current.get("href") or "")
        last_chat = str(self._read_session_meta().get("last_chat_url") or "")
        target_chat = href if ("/c/" in href and "local-chatgpt" not in href) else last_chat
        if "/c/" not in target_chat or "local-chatgpt" in target_chat:
            target_chat = ""
        refreshed = {}
        try:
            if target_chat:
                # Voice-limit UI can bounce the page back to the root composer.
                # Recover the persisted conversation instead of silently losing
                # context or generating another startup-only chat.
                refreshed = self.voice.open_chat(target_chat)
            elif current.get("composerReady"):
                refreshed = self.voice.continue_chat()
        except Exception:
            refreshed = {}
        if isinstance(refreshed, dict) and refreshed.get("ok"):
            current = refreshed
        if not self._valid(generation):
            return False
        if current.get("loggedOut") or current.get("result") == "needs-login":
            self.state.update(state="approval")
            return False
        # If the original page already exposed the text composer, a best-effort
        # reload failure should not turn a Voice-only problem into a Tabby error.
        if not current.get("composerReady") and not (status or {}).get("composerReady"):
            return False
        with self._lock:
            self._voice_active = False
            self._text_session = True
            self._seen_voice_active = False
            self._voice_text_handoff = False
        self._cancel_hide()
        self._touch_session()
        self.state.update(
            summoned=True, voiceActive=False, state="idle", inputArmed=True,
            attachmentPending=False, audioLevel=0.0,
        )
        return True

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
                if "active" in result:
                    self.state.update(voiceActive=bool(result.get("active")))
                if not self._valid(generation): return
                if result.get("loggedOut") or result.get("result") == "needs-login":
                    self.state.update(state="approval"); return
                if result.get("ok"):
                    clicked = True
                    if result.get("active"):
                        self._touch_session()
                        self._clear_force_new_next()
                        mic_mode = self._recover_voice_mic(generation, result)
                        if mic_mode == "fallback":
                            return
                        if mic_mode == "failed":
                            self.state.update(state="error")
                            return
                        with self._lock:
                            self._voice_active = True; self._seen_voice_active = True
                        self.state.update(state="listening")
                        return
                    # ChatGPT can reject Voice with a quota/usage-limit modal
                    # while the ordinary text composer remains fully available.
                    # activate() already waited for the Voice transition, so do
                    # not keep retrying until Tabby errors out: degrade to text.
                    if result.get("composerReady") and self._fallback_to_text_session(generation, result):
                        return

            status = self.voice.status()
            if "active" in status:
                self.state.update(voiceActive=bool(status.get("active")))
            if not self._valid(generation): return
            if status.get("loggedOut") or status.get("result") == "needs-login":
                self.state.update(state="approval"); return
            if status.get("ok") and status.get("active"):
                self._touch_session()
                self._clear_force_new_next()
                mic_mode = self._recover_voice_mic(generation, status)
                if mic_mode == "fallback":
                    return
                if mic_mode == "failed":
                    self.state.update(state="error")
                    return
                with self._lock:
                    self._voice_active = True; self._seen_voice_active = True
                self.state.update(state="listening")
                return
            # If the page is Voice-ready again after a text/setup rehydrate,
            # allow another trusted click instead of treating the first miss as fatal.
            if status.get("ready"):
                clicked = False
            time.sleep(.25)

        if self._valid(generation):
            try:
                final_status = self.voice.status()
            except Exception:
                final_status = {}
            if self._fallback_to_text_session(generation, final_status):
                return
            self.state.update(state="error"); self._schedule_hide()


    def summon(self):
        """Canonical open path used by hotkeys AND the wakeword.

        This is the newer hotkey variant: Voice startup plus the hover composer.
        It is idempotent while already open; closing is a separate action so
        saying the wake phrase can never accidentally toggle Tabby off.
        """
        if not self.enabled:
            return {"ok": False, "error": "Tabby disabled"}
        if self.state.snapshot().get("summoned"):
            self.state.update(inputArmed=True)
            return {"ok": True, "result": "already-summoned-with-input"}
        result = self.wake()
        if result.get("ok"):
            self.state.update(inputArmed=True)
            return {"ok": True, "result": "waking-with-input"}
        return result

    def toggle(self):
        """Global summon hotkey: Voice + hover text input, press again to close."""
        if not self.enabled:
            return {"ok": False, "error": "Tabby disabled"}
        if self.state.snapshot().get("summoned"):
            return self.close()
        return self.summon()

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
            self.state.update(summoned=True, voiceActive=False, state="wake", inputArmed=True, attachmentPending=False, audioLevel=0.0)
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
            self.state.update(state="approval"); return
        if result.get("ok"):
            self._touch_session()
            self._clear_force_new_next()
            self.state.update(state="idle")
        else:
            self.state.update(state="error"); self._schedule_hide()

    def new_session(self):
        self._update_session_meta(prepared_chat_url="")
        if not self.enabled:
            return {"ok": False, "error": "Tabby disabled"}
        if self.state.snapshot().get("summoned"):
            self.close()
        with self._lock:
            generation = self._new_generation()
            self._voice_active = False; self._text_session = False; self._seen_voice_active = False
            self._cancel_hide()
            self.state.update(summoned=True, voiceActive=False, state="wake", inputArmed=True, attachmentPending=False, audioLevel=0.0)
        threading.Thread(target=self._start_voice, args=(generation, True), name="tabby-new-session", daemon=True).start()
        return {"ok": True, "result": "new-session"}

    def _resume_voice_after_text(self, generation, baseline_count, baseline_text):
        """Wait for the typed turn to land, then return to Voice listening."""
        response_seen = False
        response_deadline = time.monotonic() + 60.0
        while self._valid(generation) and time.monotonic() < response_deadline:
            try:
                response = self.voice.latest_response()
            except Exception:
                time.sleep(.35)
                continue
            text = str(response.get("assistantText") or "").strip()
            count = int(response.get("assistantCount") or 0)
            if bool(text) and (count != baseline_count or text != baseline_text):
                response_seen = True
                if self.text_reply_mode != "never":
                    self._show_assistant_text(text)
                break
            time.sleep(.3)

        if not self._valid(generation):
            return

        # ChatGPT can take a while after a text response before exposing Start
        # Voice again. Keep the handoff alive until that control really returns,
        # then retry the trusted click until Voice becomes active.
        resume_deadline = time.monotonic() + 60.0
        last_activate = 0.0
        while self._valid(generation) and time.monotonic() < resume_deadline:
            try:
                status = self.voice.status()
            except Exception:
                time.sleep(.3)
                continue
            if "active" in status:
                self.state.update(voiceActive=bool(status.get("active")))
            if status.get("active"):
                with self._lock:
                    self._voice_active = True
                    self._voice_text_handoff = False
                    self._text_session = False
                    self._seen_voice_active = True
                self._touch_session()
                mic_mode = self._recover_voice_mic(generation, status)
                if mic_mode == "fallback":
                    return
                if mic_mode == "failed":
                    self.state.update(state="error", attachmentPending=False)
                    return
                self.state.update(state="listening", attachmentPending=False)
                return
            now = time.monotonic()
            if status.get("ready") and now - last_activate > 1.5:
                try:
                    activation = self.voice.activate()
                    if "active" in activation:
                        self.state.update(voiceActive=bool(activation.get("active")))
                except Exception:
                    pass
                last_activate = now
            time.sleep(.25)

        with self._lock:
            self._voice_text_handoff = False
            self._text_session = True
        self.state.update(state="success" if response_seen else "error", attachmentPending=False)

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
            self._mark_active_work_started()
            with self._lock:
                baseline_count = self._last_assistant_count
                baseline_text = self._last_assistant_text
                was_voice_active = self._voice_active
                self._text_reply_pending = self.text_reply_mode in {"always", "text-only"}
                self._text_reply_seen_change = False
                self._response_stable_ticks = 0
                if was_voice_active:
                    # ChatGPT Live's visible composer currently ignores our
                    # synthetic submit while Voice is active. Switch to a
                    # controlled text handoff instead of reporting a false send.
                    self._voice_text_handoff = True
                    self._voice_active = False
                    self._text_session = True

            if was_voice_active:
                try:
                    ended = self.voice.end()
                    if "active" in ended:
                        self.state.update(voiceActive=bool(ended.get("active")))
                except Exception:
                    pass
                deadline = time.monotonic() + 5.0
                while self._valid(generation) and time.monotonic() < deadline:
                    try:
                        if not self.voice.status().get("active"):
                            break
                    except Exception:
                        pass
                    time.sleep(.15)

            result = self.voice.send_text(text)
            if not self._valid(generation): return
            if result.get("ok"):
                self.state.update(state="thinking", attachmentPending=False)
                with self._lock:
                    self._text_session = True
                if was_voice_active:
                    self._resume_voice_after_text(generation, baseline_count, baseline_text)
            else:
                with self._lock:
                    self._voice_text_handoff = False
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
        self._stop_local_voice()
        with self._lock:
            active_work_task_id = self._active_work_task_id
            self._active_work_task_id = ""
            self._active_work_baseline_count = 0
            self._active_work_saw_working = False
            self._generation += 1
            self._voice_active = False; self._text_session = False; self._seen_voice_active = False
            self._text_reply_pending = False
            self._text_reply_seen_change = False
            self._voice_text_handoff = False
            self._cancel_hide()
            self.state.update(summoned=False, state="idle", inputArmed=False, audioLevel=0.0, attachmentPending=False, whiteboardVisible=False, items=[])
        self._end_done.clear()
        def finish_previous_session():
            try:
                ended = {}
                for _ in range(3):
                    try:
                        ended = self.voice.end()
                    except Exception:
                        ended = {}
                    if "active" in ended:
                        self.state.update(voiceActive=bool(ended.get("active")))
                    if ended.get("active") is False:
                        break
                    try:
                        observed = self.voice.status()
                    except Exception:
                        observed = {}
                    if "active" in observed:
                        self.state.update(voiceActive=bool(observed.get("active")))
                    if observed.get("active") is False:
                        break
                    time.sleep(.08)
                # Never hide a live ChatGPT Voice session without its green face.
                if bool(self.state.snapshot().get("voiceActive")):
                    with self._lock:
                        self._voice_active = True
                        self._seen_voice_active = True
                    self.state.update(summoned=True, state="listening")
                    return
                if active_work_task_id:
                    self._set_force_new_next(True)
                    task = self.working.get(active_work_task_id)
                    if task and task.get("status") == "working":
                        try:
                            self.voice.worker_open(task["id"], task.get("url", ""), reload=True)
                        except Exception:
                            pass
                if not self.debug:
                    try:
                        self.voice.hide()
                    except Exception:
                        pass
            finally:
                self._end_done.set()
                self._prewarm_ready.clear()
                self._schedule_prewarm(.2)
        threading.Thread(target=finish_previous_session, name="tabby-end", daemon=True).start()
        return {"ok": True, "result": "closed"}

    def _publish_working(self):
        self.state.update(working=self.working.list())

    @staticmethod
    def _usable_work_title(value):
        title = " ".join(str(value or "").split()).strip()
        low = title.lower()
        for suffix in (" | chatgpt", " - chatgpt"):
            if low.endswith(suffix):
                title = title[:-len(suffix)].strip()
                low = title.lower()
        if not title or low in {"chatgpt", "new chat", "tabby engine"}:
            return ""
        return title[:160]

    def work_list(self):
        return {"ok": True, "tasks": self.working.list()}

    def work_pin_current(self, title=""):
        try:
            status = self.voice.status()
            href = str(status.get("href") or "")
            if not status.get("ok") or "/c/" not in href or "local-chatgpt" in href:
                return {"ok": False, "error": "Current ChatGPT conversation is not persisted yet"}
            latest = self.voice.latest_response()
            count = int(latest.get("assistantCount") or 0)
            working_now = bool(status.get("working"))
            baseline = max(0, count - (1 if working_now and count > 0 else 0))
            chosen = self._usable_work_title(title) or self._usable_work_title(status.get("title")) or "Working task"
            task = self.working.pin(
                href, chosen,
                saw_working=working_now,
                baseline_assistant_count=baseline,
            )
            self._publish_working()
            worker = self.voice.worker_open(task["id"], href, reload=True)
            if not worker.get("ok"):
                # Creation is transactional: if the worker cannot take over,
                # leave the current chat untouched and roll back the new pin.
                self.working.delete_user(task["id"])
                self._publish_working()
                return {"ok": False, "error": worker.get("result", "worker-open-failed")}

            # The worker owns the old conversation now. The normal Tabby slot
            # must become a clean future conversation, regardless of Smart /
            # Continue mode.
            self._set_force_new_next(True)
            self._prewarm_ready.clear()
            if self.state.snapshot().get("summoned"):
                self.close()
            else:
                self._schedule_prewarm(.1)
            return {"ok": True, "result": "pinned", "task": task}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _open_work_task(self, task_id, voice=False):
        task = self.working.get(task_id)
        if not task:
            return {"ok": False, "error": "unknown working task"}
        # Transfer ownership of this conversation from its hidden worker to the
        # visible/main Tabby slot. Never keep two live views of the same chat.
        try:
            self.voice.worker_close(task["id"])
        except Exception:
            pass
        latest = {}
        try:
            latest = self.voice.latest_response()
        except Exception:
            pass
        with self._lock:
            self._active_work_task_id = task["id"]
            self._active_work_baseline_count = int(task.get("baselineAssistantCount") or 0)
            self._active_work_saw_working = bool(task.get("sawWorking"))
            generation = self._new_generation()
            self._voice_active = False
            self._text_session = not voice
            self._seen_voice_active = False
            self._voice_text_handoff = False
            self._cancel_hide()
            self.state.update(
                summoned=True, state="wake", inputArmed=True,
                attachmentPending=False, audioLevel=0.0,
            )
        self._prewarm_ready.clear()
        # Main-engine navigation must be atomic against background prewarm.
        # Otherwise a setup/new-chat command can land between open-chat steps
        # and silently replace the Working conversation we are resuming.
        with self._engine_lock:
            result = self.voice.open_chat(task.get("url", ""))
        self._last_work_open = {"stage":"open-chat", "result":dict(result)}
        if not self._valid(generation):
            self._last_work_open = {"stage":"cancelled", "result":dict(result)}
            return {"ok": False, "result": "cancelled"}
        if not result.get("ok"):
            self.state.update(state="error")
            return result
        self._prime_response_cursor()
        try:
            loaded_latest = self.voice.latest_response()
            with self._lock:
                self._active_work_baseline_count = int(loaded_latest.get("assistantCount") or 0)
        except Exception:
            pass
        if not voice:
            self.state.update(state="idle")
            return {"ok": True, "result": "work-opened", "task": task}

        deadline = time.monotonic() + 18.0
        last_activate = 0.0
        while self._valid(generation) and time.monotonic() < deadline:
            status = self.voice.status()
            self._last_work_open = {"stage":"voice-poll", "status":dict(status)}
            if status.get("active"):
                with self._lock:
                    self._voice_active = True
                    self._text_session = False
                    self._seen_voice_active = True
                mic_mode = self._recover_voice_mic(generation, status)
                if mic_mode == "fallback":
                    return {"ok": True, "result": "work-local-voice-active", "task": task}
                if mic_mode == "failed":
                    self.state.update(state="error")
                    return {"ok": False, "result": "work-voice-mic-failed", "task": task}
                self.state.update(state="listening")
                return {"ok": True, "result": "work-voice-active", "task": task}
            now = time.monotonic()
            if status.get("ready") and now - last_activate > 1.4:
                activation = self.voice.activate()
                self._last_work_open = {"stage":"activate", "status":dict(status), "activation":dict(activation)}
                last_activate = now
            time.sleep(.25)
        self._last_work_open = {"stage":"voice-timeout", "task":task.get("id")}
        self.state.update(state="error")
        return {"ok": False, "result": "work-voice-timeout", "task": task}

    def work_open(self, task_id, voice=False):
        task = self.working.get(str(task_id or ""))
        if not task:
            return {"ok": False, "error": "unknown working task"}
        if not task.get("url"):
            return {"ok": False, "error": "task has no ChatGPT conversation to open"}
        threading.Thread(
            target=self._open_work_task,
            args=(str(task_id or ""), bool(voice)),
            name="tabby-work-open",
            daemon=True,
        ).start()
        return {"ok": True, "result": "queued"}

    def _mark_active_work_started(self):
        with self._lock:
            task_id = self._active_work_task_id
            baseline = self._last_assistant_count
            if not task_id:
                return
            self._active_work_baseline_count = int(baseline or 0)
            self._active_work_saw_working = False
        task = self.working.update(
            task_id,
            status="working", progress=0.0, completedAt=0.0,
            sawWorking=False, baselineAssistantCount=int(baseline or 0), summary="",
        )
        if task:
            self._publish_working()

    def _monitor_active_work(self, status):
        with self._lock:
            task_id = self._active_work_task_id
            baseline = self._active_work_baseline_count
            saw = self._active_work_saw_working
        if not task_id:
            return
        if bool(status.get("working")):
            if not saw:
                with self._lock:
                    self._active_work_saw_working = True
                self.working.update(task_id, status="working", sawWorking=True)
                self._publish_working()
            return
        if not saw:
            return
        try:
            latest = self.voice.latest_response()
        except Exception:
            return
        count = int(latest.get("assistantCount") or 0)
        if count <= int(baseline or 0):
            return
        summary = str(latest.get("assistantText") or "")[:4000]
        task = self.working.complete(task_id, summary=summary)
        if task:
            with self._lock:
                self._active_work_saw_working = False
                self._active_work_baseline_count = count
            self._publish_working()

    def work_delete_user(self, task_id):
        task = self.working.get(task_id)
        if not task:
            return {"ok": False, "error": "unknown working task"}
        try:
            self.voice.worker_close(task["id"])
        except Exception:
            pass
        deleted = self.working.delete_user(task["id"])
        self._working_idle_ticks.pop(task["id"], None)
        self._working_retry_at.pop(task["id"], None)
        self._publish_working()
        return {"ok": bool(deleted), "result": "deleted" if deleted else "not-found"}

    def work_update(self, task_id, **values):
        task = self.working.update(
            task_id,
            title=values.get("title"),
            progress=values.get("progress"),
            status=values.get("status"),
            summary=values.get("summary"),
        )
        if not task:
            return {"ok": False, "error": "unknown working task"}
        self._publish_working()
        return {"ok": True, "task": task}

    def work_create(self, title="", summary="", progress=0.0, status="working"):
        if not str(title or "").strip():
            return {"ok": False, "error": "missing title"}
        try:
            task = self.working.create(str(title), summary=str(summary or ""), progress=float(progress or 0.0), status=str(status or "working"))
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self._publish_working()
        return {"ok": True, "result": "created", "task": task}

    def work_reopen(self, task_id, summary=None):
        task = self.working.reopen(task_id, summary=summary)
        if not task:
            return {"ok": False, "error": "unknown working task"}
        self._working_idle_ticks.pop(task["id"], None)
        self._working_retry_at.pop(task["id"], None)
        self._publish_working()
        return {"ok": True, "result": "reopened", "task": task}

    def work_complete(self, task_id, summary=""):
        task = self.working.complete(task_id, summary=str(summary or ""))
        if not task:
            return {"ok": False, "error": "unknown working task"}
        try:
            self.voice.worker_close(task["id"])
        except Exception:
            pass
        self._working_idle_ticks.pop(task["id"], None)
        self._publish_working()
        return {"ok": True, "result": "completed", "task": task}

    def _working_monitor_loop(self):
        # `latest-response` already includes ChatGPT's working/title/ready
        # state, so use one bridge round-trip per worker tick instead of a
        # separate status + response query. This keeps Working responsive while
        # the main Tabby slot is simultaneously prewarming.
        while not self._stop.wait(1.5):
            tasks = self.working.list()
            for task in tasks:
                if self._stop.is_set():
                    return
                task_id = str(task.get("id") or "")
                if not task_id or task.get("status") != "working" or not task.get("url"):
                    continue
                now = time.monotonic()
                try:
                    latest = self.voice.worker_latest_response(task_id)
                    if not latest.get("ok"):
                        if now >= float(self._working_retry_at.get(task_id, 0)):
                            opened = self.voice.worker_open(task_id, task.get("url", ""), reload=False)
                            self._working_retry_at[task_id] = now + (3.0 if opened.get("ok") else 12.0)
                        continue

                    title = self._usable_work_title(latest.get("title"))
                    updates = {}
                    if title and title != task.get("title"):
                        updates["title"] = title

                    working_now = bool(latest.get("working"))
                    count = int(latest.get("assistantCount") or 0)
                    if working_now:
                        self._working_idle_ticks[task_id] = 0
                        if not task.get("sawWorking"):
                            updates["sawWorking"] = True
                    else:
                        # Completion requires evidence of a new assistant turn
                        # after the task was pinned (or that we actually saw it
                        # working), plus three consecutive idle observations.
                        evidence = bool(task.get("sawWorking")) or count > int(task.get("baselineAssistantCount") or 0)
                        age = time.time() - float(task.get("createdAt") or time.time())
                        if evidence and age >= 4.0:
                            ticks = int(self._working_idle_ticks.get(task_id, 0)) + 1
                            self._working_idle_ticks[task_id] = ticks
                            if ticks >= 3:
                                summary = str(latest.get("assistantText") or "")[:4000]
                                self.working.complete(task_id, summary=summary)
                                self.voice.worker_close(task_id)
                                self._working_idle_ticks.pop(task_id, None)
                                self._publish_working()
                                continue
                        else:
                            self._working_idle_ticks[task_id] = 0

                    if updates:
                        self.working.update(task_id, **updates)
                        self._publish_working()
                except Exception:
                    continue

    def _on_audio(self, level):
        with self._lock:
            self._audio_level = float(level)
            active = (self._voice_active or self._local_voice_fallback) and self.state.snapshot().get("summoned")
            if level > .035: self._last_sound = time.monotonic()
        if not active:
            return
        self.state.update(audioLevel=round(float(level), 4))

    def _workspace_visibility_loop(self):
        while not self._stop.wait(.10):
            try:
                self.voice.sync_workspace_visibility()
            except Exception:
                pass

    def _monitor_loop(self):
        inactive_since = None
        while not self._stop.wait(.12):
            snap = self.state.snapshot()
            if not snap.get("summoned"):
                inactive_since = None
                now = time.monotonic()
                if self.background_prewarm_enabled and now >= self._next_hidden_prewarm_check:
                    self._next_hidden_prewarm_check = now + 60.0
                    need_new = self._should_start_new()
                    stale_engine = False
                    if self._prewarm_ready.is_set():
                        try:
                            prepared_status = self.voice.status()
                            prepared_href = str(prepared_status.get("href") or "")
                            stale_engine = (not prepared_status.get("ok") or not prepared_status.get("composerReady")
                                            or prepared_href != self._prewarm_href)
                        except Exception:
                            stale_engine = True
                    with self._prewarm_lock:
                        mismatch = self._prewarm_ready.is_set() and bool(self._prewarm_new) != bool(need_new)
                    if mismatch or stale_engine:
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
            self.state.update(voiceActive=active)
            working = bool(status.get("working"))
            voice_dropped_to_text = False
            with self._lock:
                handoff = self._voice_text_handoff
                if handoff:
                    self._voice_active = False; inactive_since = None
                elif active and self._voice_active and self._voice_has_live_mic(status):
                    self._seen_voice_active = True; inactive_since = None
                elif self._voice_active:
                    if inactive_since is None: inactive_since = time.monotonic()
                    elif time.monotonic() - inactive_since > 1.5:
                        self._voice_active = False
                        # Voice quota exhaustion can briefly enter Voice, play
                        # ChatGPT's limit notice, then drop back to the normal
                        # composer. The hover text input is still valid, so keep
                        # Tabby alive and turn that same chat into a text session
                        # instead of treating the Voice-only failure as a close.
                        if self._seen_voice_active and snap.get("inputArmed") and status.get("composerReady"):
                            voice_dropped_to_text = True
                        elif self._seen_voice_active:
                            self.close(); continue
                level = self._audio_level
                voice_active = self._voice_active
                text_session = self._text_session
                local_voice = self._local_voice_fallback
                local_busy = self._local_voice_busy

            if voice_dropped_to_text:
                inactive_since = None
                if self._fallback_to_text_session(self._generation, status):
                    continue
                self.state.update(state="error")
                continue

            if local_voice:
                self._cancel_hide()
                if level > .035 or time.monotonic() - self._last_sound < .20:
                    self.state.set_state("speaking")
                else:
                    self.state.set_state("thinking" if local_busy else "listening")
            elif handoff:
                self._cancel_hide(); self.state.set_state("thinking")
            elif voice_active:
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
            self._monitor_active_work(status)

    def handle(self, request):
        command = str(request.get("command", "")).strip().lower()
        if command == "status":
            return {
                "localVoiceFallback": bool(self._local_voice_fallback),
                "localVoiceBusy": bool(self._local_voice_busy),
                "ok": True, "state": self.state.snapshot(),
                "prewarmReady": self._prewarm_ready.is_set(),
                "prewarmInflight": self._prewarm_inflight,
                "prewarmNew": self._prewarm_new,
                "sessionMode": self.session_mode,
                "startupPromptEnabled": self.startup_prompt_enabled,
                "backgroundPrewarmEnabled": self.background_prewarm_enabled,
                "textReplyMode": self.text_reply_mode,
                "hotkeyMode": self.hotkey_mode,
                "fnHotkeyAvailable": bool(self.fn_hotkey.available),
                "altHotkeyAvailable": bool(self.alt_hotkey.available),
                "doubleTapMs": self.double_tap_ms,
                "fnDoubleTapMs": self.fn_double_tap_ms,
                "lastWorkOpen": self._last_work_open,
            }
        # `wake` is kept as a compatibility alias, but it now deliberately uses
        # the exact same newer summon variant as the physical hotkey.
        if command in {"wake", "summon", "open"}: return self.summon()
        if command == "close": return self.close()
        if command in {"toggle", "toggle-summon", "toggle_summon"}: return self.toggle()
        if command in {"toggle-fallback", "toggle_fallback"}: return self.fallback_hotkey()
        if command in {"new-session", "new_session", "new-chat"}: return self.new_session()
        if command in {"toggle-input", "toggle_input"}: return self.toggle_input()
        if command == "show-input": self.state.update(inputArmed=True, summoned=True); self._cancel_hide(); return {"ok":True}
        if command == "hide-input": self.state.update(inputArmed=False); self._schedule_hide(); return {"ok":True}
        if command == "send-text": return self.send_text(request.get("text", ""))
        if command == "paste-clipboard": return self.paste_clipboard()
        if command == "work-list": return self.work_list()
        if command == "work-pin-current": return self.work_pin_current(request.get("title", ""))
        if command == "work-open": return self.work_open(request.get("task_id", ""), voice=False)
        if command == "work-voice": return self.work_open(request.get("task_id", ""), voice=True)
        if command == "work-delete-user": return self.work_delete_user(request.get("task_id", ""))
        if command == "work-complete": return self.work_complete(request.get("task_id", ""), request.get("summary", ""))
        if command == "work-create":
            return self.work_create(request.get("title", ""), request.get("summary", ""), request.get("progress", 0.0), request.get("status", "working"))
        if command == "work-reopen": return self.work_reopen(request.get("task_id", ""), request.get("summary"))
        if command == "work-update":
            return self.work_update(
                request.get("task_id", ""),
                title=request.get("title"), progress=request.get("progress"),
                status=request.get("status"), summary=request.get("summary"),
            )
        if command == "debug-on":
            self.debug = True
            threading.Thread(target=self.voice.set_debug, args=(True,), name="tabby-debug-on", daemon=True).start()
            return {"ok": True, "result": "debug-on"}
        if command == "debug-off":
            self.debug = False
            threading.Thread(target=self.voice.set_debug, args=(False,), name="tabby-debug-off", daemon=True).start()
            return {"ok": True, "result": "debug-off"}
        if command == "state": self.state.set_state(request.get("value","idle")); return {"ok":True}
        if command in {"show","hide","clear","text","progress","choice","shape","display","ui-remove","choose"}:
            result = self.state.whiteboard(request)
            if result.get("ok") and self.state.snapshot().get("whiteboardVisible"):
                self._cancel_hide()  # keep a board the agent just drew on screen
            return result
        if command == "ui-state": return self.state.ui_snapshot()
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
