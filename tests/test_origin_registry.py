import tempfile
import unittest
from pathlib import Path

from tabby.origin_registry import OriginRegistry


class OriginRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "origins.sqlite3"
        self.registry = OriginRegistry(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_multi_chat_persists_across_restart(self):
        one = self.registry.register("mobile:one", origin_url="https://chatgpt.com/c/chat-one",
                                     operation_key="register-one")
        two = self.registry.register("desktop:two", origin_url="https://chatgpt.com/c/chat-two",
                                     operation_key="register-two")
        reopened = OriginRegistry(self.path)
        self.assertEqual(reopened.resolve("mobile:one")["conversation_id"], "chat-one")
        self.assertEqual(reopened.resolve("desktop:two")["canonical_url"], two["canonical_url"])
        self.assertNotEqual(one["conversation_id"], two["conversation_id"])

    def test_registration_is_idempotent_and_replay_collision_is_rejected(self):
        args = dict(origin_url="https://chatgpt.com/c/stable", operation_key="same-operation")
        first = self.registry.register("origin:stable", **args)
        second = self.registry.register("origin:stable", **args)
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        with self.assertRaisesRegex(ValueError, "replay collision"):
            self.registry.register("origin:other", operation_key="same-operation")

    def test_absent_provenance_does_not_resolve(self):
        self.assertIsNone(self.registry.resolve("origin:missing"))
        with self.assertRaisesRegex(ValueError, "origin_ref"):
            self.registry.resolve("")

    def test_late_binding_and_verified_project_move_keep_identity(self):
        pending = self.registry.register("mobile:late", operation_key="late-create")
        self.assertEqual(pending["canonical_url"], "")
        bound = self.registry.register("mobile:late",
            origin_url="https://chatgpt.com/g/g-p-0123456789abcdef0123456789abcdef-working/c/conversation-1",
            operation_key="late-bind")
        moved = self.registry.register("mobile:late",
            origin_url="https://chatgpt.com/g/g-p-fedcba9876543210fedcba9876543210-done/c/conversation-1",
            operation_key="verified-move")
        self.assertEqual(bound["conversation_id"], moved["conversation_id"])
        self.assertIn("-done/c/conversation-1", moved["canonical_url"])

    def test_reference_and_conversation_collisions_are_rejected(self):
        self.registry.register("origin:a", origin_url="https://chatgpt.com/c/chat-a",
                               operation_key="collision-a")
        with self.assertRaisesRegex(ValueError, "another conversation"):
            self.registry.register("origin:a", origin_url="https://chatgpt.com/c/chat-b",
                                   operation_key="collision-b")
        with self.assertRaisesRegex(ValueError, "another origin_ref"):
            self.registry.register("origin:b", origin_url="https://chatgpt.com/c/chat-a",
                                   operation_key="collision-c")

    def test_wrong_or_ambiguous_strings_are_never_accepted_as_urls(self):
        for value in ("connection-123", "https://example.com/c/chat", "https://chatgpt.com/"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "canonical"):
                self.registry.register("origin:bad", origin_url=value,
                                       operation_key="bad-" + str(len(value)))


if __name__ == "__main__":
    unittest.main()
