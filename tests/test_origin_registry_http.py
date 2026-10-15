import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

from tabby.origin_registry import (OriginRegistry, OriginRegistryError,
                                   OriginRegistryUnavailable, RemoteOriginRegistry,
                                   create_origin_registry)
from tabby.origin_registry_http import MAX_JSON_BODY, create_server


class OriginRegistryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "origins.sqlite3"
        self.token_path = self.root / "token"
        self.token = "test-token-" + "x" * 32
        self.token_path.write_text(self.token, encoding="utf-8")
        self.token_path.chmod(0o600)
        self._start_server()

    def tearDown(self):
        if getattr(self, "server", None):
            self.server.shutdown()
            self.thread.join(timeout=2)
            self.server.server_close()
        self.tmp.cleanup()

    def _start_server(self):
        self.server = create_server("127.0.0.1", 0, db_path=self.db_path,
                                    token_file=self.token_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.client = RemoteOriginRegistry(self.url, self.token_path, timeout=1)

    def _stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.server = None

    def test_authenticated_register_resolve_idempotency_and_conflict(self):
        first = self.client.register("mobile:one", origin_url="https://chatgpt.com/c/chat-one",
                                     operation_key="register-one")
        replay = self.client.register("mobile:one", origin_url="https://chatgpt.com/c/chat-one",
                                      operation_key="register-one")
        self.assertEqual(first["canonical_url"], "https://chatgpt.com/c/chat-one")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(self.client.resolve("mobile:one")["conversation_id"], "chat-one")
        with self.assertRaisesRegex(OriginRegistryError, "another origin_ref"):
            self.client.register("mobile:two", origin_url="https://chatgpt.com/c/chat-one",
                                 operation_key="register-two")
        with OriginRegistry(self.db_path).db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM origins").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM operations").fetchone()[0], 1)

    def test_unauthorized_and_oversized_requests_are_rejected(self):
        body = json.dumps({"origin_ref": "private"}).encode()
        req = request.Request(self.url + "/v1/resolve", data=body, method="POST",
                              headers={"Content-Type": "application/json"})
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(req, timeout=1)
        self.assertEqual(caught.exception.code, 401)

        req = request.Request(self.url + "/v1/resolve", data=b"{}", method="POST",
                              headers={"Authorization": "Bearer " + self.token,
                                       "Content-Type": "application/json",
                                       "Content-Length": str(MAX_JSON_BODY + 1)})
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(req, timeout=1)
        self.assertEqual(caught.exception.code, 413)

    def test_persistence_across_server_restart_and_url_resolution(self):
        self.client.register("desktop:stable", origin_url="https://chatgpt.com/c/persisted",
                             operation_key="persist-register")
        self._stop_server()
        self._start_server()
        resolved = self.client.resolve("desktop:stable")
        self.assertEqual(resolved["canonical_url"], "https://chatgpt.com/c/persisted")
        self.assertEqual(os.stat(self.db_path).st_mode & 0o777, 0o600)

    def test_remote_down_fails_closed_without_local_fallback(self):
        client = self.client
        self._stop_server()
        with self.assertRaisesRegex(OriginRegistryUnavailable, "unavailable"):
            client.resolve("mobile:one")
        self.assertFalse((self.root / "fallback.sqlite3").exists())

    def test_factory_selects_remote_without_network_and_validates_config(self):
        remote = create_origin_registry({"LOOM_ORIGIN_REGISTRY_URL": "http://127.0.0.1:1",
                                         "LOOM_ORIGIN_REGISTRY_TOKEN_FILE": str(self.token_path)})
        self.assertIsInstance(remote, RemoteOriginRegistry)
        with self.assertRaisesRegex(ValueError, "TOKEN_FILE"):
            create_origin_registry({"LOOM_ORIGIN_REGISTRY_URL": "http://127.0.0.1:1"})
        local_path = self.root / "local.sqlite3"
        local = create_origin_registry({"LOOM_ORIGIN_REGISTRY_DB": str(local_path)})
        self.assertIsInstance(local, OriginRegistry)
        self.assertTrue(local_path.exists())

    def test_token_file_must_not_be_a_symlink(self):
        linked = self.root / "linked-token"
        linked.symlink_to(self.token_path)
        client = RemoteOriginRegistry(self.url, linked, timeout=1)
        with self.assertRaisesRegex(OriginRegistryUnavailable, "symlink"):
            client.resolve("mobile:one")

    def test_token_file_must_be_private(self):
        self.token_path.chmod(0o644)
        with self.assertRaisesRegex(OriginRegistryUnavailable, "mode-0600"):
            self.client.resolve("mobile:one")


if __name__ == "__main__":
    unittest.main()
