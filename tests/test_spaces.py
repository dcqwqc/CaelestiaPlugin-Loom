import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tabby.spaces import SpaceStore, SpaceError, validate_placement
import loom_mcp


class SpaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "modules.json"
        self.store = SpaceStore(self.path)

    def test_save_reopen_and_atomic_private_file(self):
        item = self.store.create_module(kind="text", title="Note", data={"text": "hello"},
                                        placement={"width": 420, "height": 210})
        space = self.store.save_space(name="Status", module_ids=[item["id"]])
        again = SpaceStore(self.path)
        self.assertEqual(again.get_space(space["id"])["modules"][0]["data"]["text"], "hello")
        self.assertEqual(again.get_module(item["id"])["placement"]["width"], 420)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_idempotency_key_repeated_and_conflicting(self):
        one = self.store.create_module(kind="text", title="Once", request_id="req-1")
        same = SpaceStore(self.path).create_module(kind="text", title="Once", request_id="req-1")
        self.assertEqual(one["id"], same["id"])
        with self.assertRaisesRegex(SpaceError, "reused"):
            self.store.create_module(kind="text", title="Changed", request_id="req-1")
        self.assertEqual(len(self.store.list()["modules"]), 1)

    def test_update_placement_and_delete_keeps_space(self):
        item = self.store.create_module(kind="memory", title="RAM", placement={"surface": "performance"})
        space = self.store.save_space(name="Overview", module_ids=[item["id"]])
        updated = self.store.update_module(item["id"], placement={"width": 520, "anchor": "top-right"},
                                           visible=False)
        self.assertEqual(updated["placement"]["surface"], "performance")
        self.assertEqual(updated["placement"]["width"], 520)
        self.assertFalse(updated["visible"])
        self.store.delete_module(item["id"])
        self.assertEqual(self.store.get_space(space["id"])["space"]["module_ids"], [])
        self.assertEqual(len(self.store.list()["modules"]), 0)

    def test_bad_geometry_and_nonfinite_rejected(self):
        for args in ({"width": 20}, {"height": float("nan")},
                     {"surface": "unsafe"}, {"unknown": 1}, {"x": 1e100}):
            with self.subTest(args=args), self.assertRaises(SpaceError):
                validate_placement(args)
        with self.assertRaises(SpaceError):
            self.store.save_space(name="Invalid", module_ids=["missing"])

    def test_corruption_never_gets_overwritten(self):
        self.path.write_text("garbage")
        with self.assertRaisesRegex(SpaceError, "unreadable"):
            self.store.create_module(kind="text", title="Do not overwrite")
        self.assertEqual(self.path.read_text(), "garbage")

    def test_modules_are_reused_across_spaces(self):
        item = self.store.create_module(kind="text", title="Shared")
        first = self.store.save_space(name="First", module_ids=[item["id"]])
        second = self.store.save_space(name="Second", module_ids=[item["id"]])
        self.store.update_module(item["id"], title="New title")
        self.assertEqual(self.store.get_space(first["id"])["modules"][0]["title"], "New title")
        self.assertEqual(self.store.get_space(second["id"])["modules"][0]["title"], "New title")


class McpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SpaceStore(Path(self.tmp.name) / "modules.json")
        self.patch = patch.object(loom_mcp, "SPACE_STORE", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_tools_visible_and_alias_compatible(self):
        names = {item["name"] for item in loom_mcp.tool_list()}
        self.assertIn("loom_space_show", names)
        self.assertIn("loom_module_create", names)
        self.assertIn("loom_status", names)
        self.assertIn("tabby_space_show", loom_mcp.TOOL_INDEX)
        self.assertIn("lume_space_show", loom_mcp.TOOL_INDEX)

    def test_round_trip_ui_real_mcp_handler(self):
        create = loom_mcp.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "loom_module_create", "arguments": {"kind": "text", "title": "Note",
                                   "data": {"text": "Content"}, "placement": {"surface": "board"}}}})
        item = create["result"]["structuredContent"]
        self.assertEqual(item["title"], "Note")
        space = self.store.save_space(name="My space", module_ids=[item["id"]])
        seen = []
        with patch.object(loom_mcp, "_display", side_effect=lambda items, mode="append": seen.extend(items)):
            shown = loom_mcp.call_tool("loom_space_show", {"space_id": space["id"]})["structuredContent"]
        self.assertEqual(len(shown["rendered_ids"]), 1)
        self.assertEqual(seen[0]["body"], "Content")

    def test_native_performance_renderer_is_only_reported_as_requested(self):
        item = self.store.create_module(kind="memory", title="Memory", placement={"surface": "floating"})
        space = self.store.save_space(name="Floats", module_ids=[item["id"]])
        with patch.object(loom_mcp, "_display") as display:
            shown = loom_mcp.call_tool("loom_space_show", {"space_id": space["id"]})["structuredContent"]
        display.assert_not_called()
        self.assertFalse(shown["board_visible"])
        self.assertEqual(shown["rendered_ids"], [])
        self.assertEqual(shown["native_requested_ids"], [item["id"]])
        self.assertEqual(shown["skipped"], [])


if __name__ == "__main__":
    unittest.main()
