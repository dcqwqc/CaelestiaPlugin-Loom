import io
import json
import socket
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

    def test_fragmented_unicode_request_reassembled(self):
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            srv = IPCServer(lambda payload: {"ok": True, "payload": payload["text"]})
            srv.start()
            try:
                sock = socket.socket(socket.AF_UNIX)
                sock.settimeout(3)
                sock.connect(str(srv.path))
                request = json.dumps({"text": "😀" * 1000}, ensure_ascii=False).encode()
                midpoint = request.index("😀".encode()) + 2  # split inside UTF-8 codepoint
                sock.sendall(request[:midpoint])
                sock.sendall(request[midpoint:])
                sock.shutdown(socket.SHUT_WR)
                reply = bytearray()
                while True:
                    part = sock.recv(65536)
                    if not part:
                        break
                    reply.extend(part)
                sock.close()
                self.assertEqual(json.loads(reply.decode())["payload"], "😀" * 1000)
            finally:
                srv.stop()
