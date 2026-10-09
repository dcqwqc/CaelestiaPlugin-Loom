import io
import os
import tempfile
import unittest
from unittest.mock import patch
from tabby.ipc import IPCServer, send_command

class IpcReplyTests(unittest.TestCase):
    def test_large_multibyte_reply_survives_multiple_reads(self):
        text = "😀" * 20000
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            srv = IPCServer(lambda payload: {"ok": True, "text": text})
            srv.start()
            try:
                self.assertEqual(send_command({"command": "large"}, timeout=3), {"ok": True, "text": text})
            finally:
                srv.stop()

    def test_oversized_reply_is_complete_error_not_truncated_json(self):
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            srv = IPCServer(lambda payload: {"ok": True, "text": "x" * 140000})
            srv.start()
            try:
                r = send_command({"command": "huge"})
                self.assertEqual(r["ok"], False)
                self.assertIn("128 KiB", r["error"])
            finally:
                srv.stop()
