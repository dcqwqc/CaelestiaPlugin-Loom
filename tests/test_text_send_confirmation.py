"""A Loom text click is never sufficient proof of ChatGPT submission."""
import threading
import time
import unittest
from unittest.mock import patch
from backend import TabbyBackend
from tabby.state import TabbyState


class TextSendConfirmationTests(unittest.TestCase):
    def backend(self, result):
        b=TabbyBackend.__new__(TabbyBackend)
        b._lock=threading.RLock()
        b._generation=1
        b._voice_active=False
        b._text_send_inflight=False
        b._last_assistant_count=0
        b._last_assistant_text=""
        b.text_reply_mode="text-only"
        b._text_reply_pending=False
        b._voice_text_handoff=False
        b.state=TabbyState(True)
        b.state.update(summoned=True,inputArmed=True)
        b._valid=lambda generation: generation==b._generation
        b._touch_session=lambda:None
        b._cancel_hide=lambda:None
        b._prime_response_cursor=lambda:None
        b._mark_active_work_started=lambda:None
        done=threading.Event()
        class Voice:
            def send_text(self,text):
                self.text=text
                done.set()
                return result
        b.voice=Voice()
        return b,done

    def test_legacy_click_only_is_not_success(self):
        b,event=self.backend({"ok":True,"result":"sent"})
        with patch("backend.threading.Thread") as worker:
            worker.return_value.start.side_effect=lambda: worker.call_args.kwargs["target"]()
            self.assertEqual(b.send_text("keep draft")["result"],"queued")
        self.assertEqual(b.state.snapshot()["textSendStatus"],"unknown")

    def test_verified_turn_is_success(self):
        b,event=self.backend({"ok":True,"result":"sent-confirmed","sent":True})
        with patch("backend.threading.Thread") as worker:
            worker.return_value.start.side_effect=lambda: worker.call_args.kwargs["target"]()
            b.send_text("hello")
        self.assertEqual(b.state.snapshot()["textSendStatus"],"sent")

    def test_failed_send_does_not_clear_attachment(self):
        b,event=self.backend({"ok":False,"result":"send-button-not-found","sent":False})
        b.state.update(attachmentPending=True, attachmentName="photo.jpg")
        with patch("backend.threading.Thread") as worker:
            worker.return_value.start.side_effect=lambda: worker.call_args.kwargs["target"]()
            b.send_text("picture")
        state=b.state.snapshot()
        self.assertEqual(state["textSendStatus"],"error")
        self.assertTrue(state["attachmentPending"])
