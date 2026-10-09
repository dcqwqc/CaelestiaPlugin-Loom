import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import loom_mcp
from tabby import ui_tree
from tabby.ipc import IPCServer
from tabby.state import TabbyState
from tabby.ui_tree import TemplateStore, UIError, UIViews, apply_patch, validate_tree

ROOT = Path(__file__).resolve().parents[1]


def settings_view():
    """A small interactive module: toggle reveals details, slider drives a bar."""
    return {"type": "card", "id": "settings", "props": {"title": "Build", "tone": "primary"}, "children": [
        {"type": "toggle", "id": "verbose", "props": {"label": "Verbose"},
         "on": {"change": [{"do": "toggle", "target": "details"}, {"do": "emit", "name": "verbose.changed"}]}},
        {"type": "text", "id": "details", "props": {"text": "Extra logs", "hidden": True}},
        {"type": "slider", "id": "level", "props": {"label": "Level", "min": 0, "max": 1, "step": 0.25},
         "on": {"change": [{"do": "set", "target": "bar", "prop": "value", "from_event": True}]}},
        {"type": "progress", "id": "bar", "props": {"label": "Level"}},
        {"type": "input", "id": "note", "props": {"placeholder": "Note", "max_length": 5},
         "on": {"submit": [{"do": "emit", "name": "note.submit"}]}},
        {"type": "select", "id": "env", "props": {"options": ["dev", "prod"], "value": "dev"}},
        {"type": "row", "id": "actions", "props": {"align": "end"}, "children": [
            {"type": "button", "id": "cancel", "props": {"label": "Cancel", "variant": "text"},
             "on": {"press": [{"do": "set", "target": "status", "prop": "text", "value": "Cancelled"}]}},
            {"type": "button", "id": "run", "props": {"label": "Run", "variant": "filled"},
             "on": {"press": [{"do": "emit", "name": "build.run"}]}},
        ]},
        {"type": "text", "id": "status", "props": {"style": "caption"}},
    ]}


def find(node, node_id):
    if node["id"] == node_id:
        return node
    for child in node.get("children", []):
        hit = find(child, node_id)
        if hit:
            return hit
    return None


class SchemaTests(unittest.TestCase):
    def test_valid_tree_is_normalised_with_typed_defaults(self):
        root, index = validate_tree(settings_view())
        self.assertEqual(len(index), 11)
        self.assertEqual(find(root, "bar")["props"], {"hidden": False, "label": "Level", "value": 0.0, "tone": "primary"})
        self.assertEqual(find(root, "run")["props"]["tone"], "primary")
        self.assertEqual(find(root, "note")["props"]["max_length"], 5)
        self.assertNotIn("children", find(root, "run"))

    def test_rejections(self):
        def tree(**node):
            return {"type": "column", "id": "root", "children": [{"type": "text", "id": "t", **node}]}
        bad = {
            "unknown type": {"type": "iframe", "id": "x"},
            "unknown prop": tree(props={"text": "a", "html": "<b>"}),
            "raw colour": tree(props={"tone": "#ff0000"}),
            "bad id": tree(id="1-bad id"),
            "wrong prop type": tree(props={"text": 5}),
            "children on leaf": tree(children=[{"type": "text", "id": "c"}]),
            "event on text": tree(on={"press": [{"do": "emit", "name": "x"}]}),
            "unknown field": tree(style="x"),
            "dup id": {"type": "row", "id": "r", "children": [{"type": "text", "id": "a"}, {"type": "text", "id": "a"}]},
            "script action": {"type": "button", "id": "b", "on": {"press": [{"do": "exec", "cmd": "rm -rf ~"}]}},
            "url action": {"type": "button", "id": "b", "on": {"press": [{"do": "emit", "name": "x", "url": "http://e"}]}},
            "unknown target": {"type": "button", "id": "b", "on": {"press": [{"do": "toggle", "target": "nope"}]}},
            "bad set prop": {"type": "column", "id": "c", "children": [
                {"type": "button", "id": "b", "on": {"press": [{"do": "set", "target": "c", "prop": "onclick", "value": 1}]}}]},
            "bad set value": {"type": "column", "id": "c", "children": [
                {"type": "button", "id": "b", "on": {"press": [{"do": "set", "target": "c", "prop": "gap", "value": "huge"}]}}]},
            "from_event without value": {"type": "button", "id": "b", "on": {"press": [
                {"do": "set", "target": "b", "prop": "label", "from_event": True}]}},
            "too many actions": {"type": "button", "id": "b", "on": {"press": [{"do": "emit", "name": "x"}] * 5}},
            "select value": {"type": "select", "id": "s", "props": {"options": ["a"], "value": "b"}},
            "slider range": {"type": "slider", "id": "s", "props": {"min": 1, "max": 1}},
            "nan": {"type": "progress", "id": "p", "props": {"value": float("nan")}},
            "bool as number": {"type": "progress", "id": "p", "props": {"value": True}},
        }
        for label, raw in bad.items():
            with self.subTest(label), self.assertRaises(UIError):
                validate_tree(raw)

    def test_depth_and_size_limits(self):
        deep = node = {"type": "column", "id": "d0", "children": []}
        for i in range(1, ui_tree.MAX_DEPTH):
            child = {"type": "column", "id": f"d{i}", "children": []}
            node["children"].append(child)
            node = child
        validate_tree(deep)
        node["children"].append({"type": "text", "id": "leaf"})
        with self.assertRaisesRegex(UIError, "deeper"):
            validate_tree(deep)
        wide = {"type": "column", "id": "w", "children": [{"type": "text", "id": f"t{i}"} for i in range(ui_tree.MAX_NODES)]}
        with self.assertRaisesRegex(UIError, "more than"):
            validate_tree(wide)

    def test_schema_summary_covers_every_component(self):
        summary = ui_tree.schema_summary()
        self.assertEqual(set(summary["components"]), set(ui_tree.COMPONENTS))
        self.assertEqual(summary["components"]["input"]["events"], {"change": "value", "submit": "value"})
        self.assertEqual(set(summary["actions"]), {"emit", "set", "toggle"})


class PatchUndoTests(unittest.TestCase):
    def setUp(self):
        self.views = UIViews()
        self.views.render("build", settings_view(), "Build")

    def root(self):
        return self.views.get("build")["root"]

    def test_granular_ops(self):
        view = self.views.patch("build", [
            {"op": "set_props", "id": "status", "props": {"text": "Queued", "tone": "warning"}},
            {"op": "insert", "parent": "actions", "index": 0, "node": {"type": "badge", "id": "b1", "props": {"text": "new"}}},
            {"op": "move", "id": "status", "parent": "actions"},
            {"op": "remove", "id": "env"},
            {"op": "replace", "id": "details", "node": {"type": "list", "id": "details", "props": {"entries": ["a", "b"]}}},
            {"op": "set_on", "id": "run", "on": {"press": [{"do": "emit", "name": "build.go"}]}},
            {"op": "set_props", "id": "note", "unset": ["placeholder"]},
        ], base_revision=1)
        root = view["root"]
        self.assertEqual(view["revision"], 2)
        self.assertEqual([c["id"] for c in find(root, "actions")["children"]], ["b1", "cancel", "run", "status"])
        self.assertEqual(find(root, "status")["props"]["tone"], "warning")
        self.assertIsNone(find(root, "env"))
        self.assertEqual(find(root, "details")["type"], "list")
        self.assertEqual(find(root, "run")["on"]["press"][0]["name"], "build.go")
        self.assertEqual(find(root, "note")["props"]["placeholder"], "")

    def test_patch_is_atomic_and_validated(self):
        before = self.root()
        bad_batches = [
            [{"op": "set_props", "id": "status", "props": {"text": "ok"}}, {"op": "set_props", "id": "bar", "props": {"value": 2}}],
            [{"op": "remove", "id": "details"}],  # verbose still targets it
            [{"op": "insert", "parent": "run", "node": {"type": "text", "id": "x"}}],
            [{"op": "insert", "parent": "actions", "node": {"type": "text", "id": "run"}}],
            [{"op": "move", "id": "actions", "parent": "run"}],
            [{"op": "move", "id": "settings", "parent": "actions"}],
            [{"op": "remove", "id": "settings"}],
            [{"op": "eval", "code": "1"}],
            [],
        ]
        for ops in bad_batches:
            with self.subTest(ops=ops), self.assertRaises(UIError):
                self.views.patch("build", ops)
        self.assertEqual(self.root(), before)
        self.assertEqual(self.views.get("build")["revision"], 1)
        with self.assertRaisesRegex(UIError, "revision conflict"):
            self.views.patch("build", [{"op": "set_props", "id": "status", "props": {"text": "x"}}], base_revision=7)

    def test_undo_redo(self):
        original = self.root()
        self.views.patch("build", [{"op": "set_props", "id": "status", "props": {"text": "one"}}])
        self.views.patch("build", [{"op": "set_props", "id": "status", "props": {"text": "two"}}])
        self.assertEqual(find(self.views.undo("build")["root"], "status")["props"]["text"], "one")
        self.assertEqual(find(self.views.undo("build", redo=True)["root"], "status")["props"]["text"], "two")
        self.views.undo("build")
        undone = self.views.undo("build")
        self.assertEqual(undone["root"], original)
        self.assertEqual(undone["redo_depth"], 2)
        with self.assertRaisesRegex(UIError, "nothing to undo"):
            self.views.undo("build")
        self.views.patch("build", [{"op": "set_props", "id": "status", "props": {"text": "fork"}}])
        self.assertEqual(self.views.get("build")["redo_depth"], 0)
        for i in range(ui_tree.UNDO_DEPTH + 5):
            self.views.patch("build", [{"op": "set_props", "id": "status", "props": {"text": str(i)}}])
        self.assertEqual(self.views.get("build")["undo_depth"], ui_tree.UNDO_DEPTH)

    def test_view_limit_and_close(self):
        for i in range(ui_tree.MAX_VIEWS - 1):
            self.views.render(f"v{i}", {"type": "text", "id": "t"})
        with self.assertRaisesRegex(UIError, "at most"):
            self.views.render("extra", {"type": "text", "id": "t"})
        self.views.close("v0")
        self.views.render("extra", {"type": "text", "id": "t"})
        with self.assertRaises(UIError):
            self.views.render("bad id!", {"type": "text", "id": "t"})


class EventTests(unittest.TestCase):
    def setUp(self):
        self.views = UIViews()
        self.views.render("build", settings_view())

    def node(self, node_id):
        return find(self.views.get("build")["root"], node_id)

    def test_basic_interactive_modules(self):
        before_press_revision = self.views.get("build")["revision"]
        press = self.views.dispatch("build", "run", "press", "ignored")
        self.assertEqual(self.views.get("build")["revision"], before_press_revision,
                         "an emit-only press must not reset unrelated input delegates")
        self.assertEqual((press["seq"], press["value"], press["emitted"]), (1, None, ["build.run"]))

        toggled = self.views.dispatch("build", "verbose", "change", True)
        self.assertEqual(toggled["emitted"], ["verbose.changed"])
        self.assertTrue(self.node("verbose")["props"]["value"])
        self.assertFalse(self.node("details")["props"]["hidden"])

        slid = self.views.dispatch("build", "level", "change", 0.6)
        self.assertEqual(slid["value"], 0.5)  # snapped to step 0.25
        self.assertEqual(self.node("bar")["props"]["value"], 0.5)
        self.assertEqual(self.views.dispatch("build", "level", "change", 9)["value"], 1.0)  # clamped

        self.assertEqual(self.views.dispatch("build", "note", "submit", "hello world")["value"], "hello")
        self.assertEqual(self.node("note")["props"]["value"], "hello")

        self.views.dispatch("build", "env", "change", "prod")
        self.assertEqual(self.node("env")["props"]["value"], "prod")

        self.views.dispatch("build", "cancel", "press")
        self.assertEqual(self.node("status")["props"]["text"], "Cancelled")

        log = self.views.read_events(since=2, view_id="build")
        self.assertEqual([e["node_id"] for e in log["events"]], ["level", "level", "note", "env", "cancel"])
        self.assertEqual(log["last_seq"], 7)
        # interactions are user state, not agent edits: undo stays empty
        self.assertEqual(self.views.get("build")["undo_depth"], 0)

    def test_invalid_interactions_change_nothing(self):
        before = self.views.get("build")
        cases = [("env", "change", "staging"), ("verbose", "change", "yes"), ("run", "change", None),
                 ("details", "press", None), ("level", "change", "1; rm"), ("missing", "press", None)]
        for node_id, event, value in cases:
            with self.subTest(node_id=node_id), self.assertRaises(UIError):
                self.views.dispatch("build", node_id, event, value)
        self.views.patch("build", [{"op": "set_props", "id": "run", "props": {"disabled": True}},
                                   {"op": "set_props", "id": "cancel", "props": {"hidden": True}}])
        for node_id in ("run", "cancel"):
            with self.subTest(node_id=node_id), self.assertRaisesRegex(UIError, "hidden or disabled"):
                self.views.dispatch("build", node_id, "press")
        self.assertEqual(self.views.read_events()["events"], [])
        self.assertEqual(self.node("env"), find(before["root"], "env"))

    def test_every_accepted_binding_succeeds_for_every_valid_value(self):
        self.views.render("v", {"type": "column", "id": "c", "children": [
            {"type": "toggle", "id": "tg", "on": {"change": [{"do": "set", "target": "b", "prop": "disabled", "from_event": True}]}},
            {"type": "slider", "id": "sl", "props": {"min": 0.2, "max": 0.8},
             "on": {"change": [{"do": "set", "target": "p", "prop": "value", "from_event": True}]}},
            {"type": "input", "id": "in", "props": {"max_length": 60},
             "on": {"submit": [{"do": "set", "target": "b", "prop": "label", "from_event": True}]}},
            {"type": "select", "id": "se", "props": {"options": ["warning", "error"]},
             "on": {"change": [{"do": "set", "target": "p", "prop": "tone", "from_event": True},
                               {"do": "set", "target": "se2", "prop": "value", "from_event": True}]}},
            {"type": "select", "id": "se2", "props": {"options": ["warning", "error", "success"]}},
            {"type": "progress", "id": "p"}, {"type": "button", "id": "b"}]})
        for node_id, value in (("tg", True), ("tg", False), ("sl", -5), ("sl", 0.8), ("in", "x" * 500),
                               ("se", "warning"), ("se", "error")):
            with self.subTest(node_id=node_id, value=value):
                self.views.dispatch("v", node_id, "submit" if node_id == "in" else "change", value)
        root = self.views.get("v")["root"]
        self.assertEqual((find(root, "p")["props"]["tone"], find(root, "se2")["props"]["value"]), ("error", "error"))
        self.assertEqual(find(root, "b")["props"]["label"], "x" * 60)

    def test_incompatible_bindings_rejected_at_validation(self):
        def view(source, target, prop):
            source = {**source, "id": "src", "on": {source.pop("event"): [
                {"do": "set", "target": "dst", "prop": prop, "from_event": True}]}}
            return {"type": "column", "id": "c", "children": [source, {**target, "id": "dst"}]}
        toggle = {"type": "toggle", "event": "change"}
        cases = {
            "bool to string": view(dict(toggle), {"type": "text"}, "text"),
            "string to number": view({"type": "input", "event": "submit"}, {"type": "progress"}, "value"),
            "select to number": view({"type": "select", "props": {"options": ["1"]}, "event": "change"}, {"type": "progress"}, "value"),
            "number to bool": view({"type": "slider", "event": "change"}, {"type": "toggle"}, "value"),
            "range too wide": view({"type": "slider", "props": {"min": 0, "max": 5}, "event": "change"}, {"type": "progress"}, "value"),
            "range below": view({"type": "slider", "props": {"min": -1, "max": 1}, "event": "change"}, {"type": "progress"}, "value"),
            "string too long": view({"type": "input", "event": "submit"}, {"type": "badge"}, "text"),
            "input length": view({"type": "input", "props": {"max_length": 9}, "event": "submit"},
                                 {"type": "input", "props": {"max_length": 8}}, "value"),
            "outside enum": view({"type": "select", "props": {"options": ["primary", "pink"]}, "event": "change"}, {"type": "badge"}, "tone"),
            "input into select": view({"type": "input", "event": "submit"}, {"type": "select", "props": {"options": ["a"]}}, "value"),
            "select not subset": view({"type": "select", "props": {"options": ["a", "b"]}, "event": "change"},
                                      {"type": "select", "props": {"options": ["a"]}}, "value"),
            "into int": view({"type": "slider", "props": {"min": 1, "max": 9}, "event": "change"}, {"type": "input"}, "max_length"),
            "into list": view({"type": "input", "event": "submit"}, {"type": "list"}, "entries"),
            "constraint prop": view({"type": "slider", "event": "change"}, {"type": "slider"}, "min"),
        }
        for label, raw in cases.items():
            with self.subTest(label), self.assertRaises(UIError):
                validate_tree(raw)
        literal = {"type": "column", "id": "c", "children": [
            {"type": "button", "id": "b", "on": {"press": [{"do": "set", "target": "s", "prop": "value", "value": "z"}]}},
            {"type": "select", "id": "s", "props": {"options": ["a"]}}]}
        with self.assertRaisesRegex(UIError, "select value must be one of"):
            validate_tree(literal)
        # A patch that would make an existing binding incompatible is rejected too.
        with self.assertRaisesRegex(UIError, "range"):
            self.views.patch("build", [{"op": "set_props", "id": "level", "props": {"max": 3}}])

    def test_event_log_is_bounded(self):
        for _ in range(ui_tree.MAX_EVENTS + 10):
            self.views.dispatch("build", "run", "press")
        log = self.views.read_events()
        self.assertEqual(len(log["events"]), ui_tree.MAX_EVENTS)
        self.assertEqual(log["events"][0]["seq"], 11)


class StateIntegrationTests(unittest.TestCase):
    def setUp(self):
        with redirect_stdout(io.StringIO()):
            self.state = TabbyState()

    def run_cmd(self, method, **command):
        with redirect_stdout(io.StringIO()) as out:
            result = getattr(self.state, method)(command)
        return result, out.getvalue()

    def test_views_coexist_with_board_items(self):
        self.run_cmd("whiteboard", command="display", items=[{"type": "text", "id": "note", "text": "hi"}])
        result, published = self.run_cmd("ui_view", command="ui-render", view_id="build", root=settings_view(), title="Build")
        self.assertTrue(result["ok"], result)
        self.assertIn('"uiViews":[{"id":"build"', published)
        snap = self.state.snapshot()
        self.assertEqual([i["id"] for i in snap["items"]], ["note"])
        self.assertEqual(snap["uiViews"][0]["root"]["id"], "settings")
        self.assertTrue(snap["whiteboardVisible"])

        self.run_cmd("whiteboard", command="ui-remove", ids=["note"])
        self.assertTrue(self.state.snapshot()["whiteboardVisible"])  # the view keeps the board up
        self.run_cmd("whiteboard", command="display", items=[{"type": "status", "id": "s", "label": "x"}])
        self.assertEqual(len(self.state.snapshot()["uiViews"]), 1)  # item updates keep views

        bad, _ = self.run_cmd("ui_view", command="ui-patch", view_id="build", ops=[{"op": "remove", "id": "settings"}])
        self.assertFalse(bad["ok"])
        event, _ = self.run_cmd("ui_view", command="ui-event", view_id="build", node_id="run", event="press")
        self.assertEqual(event["event"]["emitted"], ["build.run"])
        self.assertEqual(self.state.snapshot()["uiViews"][0]["revision"], 2)

        self.run_cmd("whiteboard", command="clear")
        snap = self.state.snapshot()
        self.assertEqual((snap["items"], snap["uiViews"], snap["whiteboardVisible"]), ([], [], False))

    def test_close_last_view_hides_empty_board(self):
        self.run_cmd("ui_view", command="ui-render", view_id="v", root={"type": "text", "id": "t"})
        self.run_cmd("ui_view", command="ui-close", view_id="v")
        self.assertFalse(self.state.snapshot()["whiteboardVisible"])

    def test_loomctl_reports_qml_interactions_over_the_socket(self):
        self.run_cmd("ui_view", command="ui-render", view_id="build", root=settings_view())
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}), \
                redirect_stdout(io.StringIO()):
            server = IPCServer(self.state.ui_view)
            server.start()
            try:
                # Exactly the argv Panel.qml builds via uiEventArgs().
                run = subprocess.run([sys.executable, str(ROOT / "loomctl.py"), "ui-event", "build", "level", "change", "0.75"],
                                     capture_output=True, text=True, timeout=10, cwd=str(ROOT))
                denied = subprocess.run([sys.executable, str(ROOT / "loomctl.py"), "ui-event", "build", "env", "change", '"qa"'],
                                        capture_output=True, text=True, timeout=10, cwd=str(ROOT))
            finally:
                server.stop()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)["event"]["value"], 0.75)
        self.assertEqual(denied.returncode, 1)
        self.assertEqual(find(self.state.snapshot()["uiViews"][0]["root"], "bar")["props"]["value"], 0.75)


def emoji_view(chars):
    return {"type": "column", "id": "c", "children": [
        {"type": "text", "id": f"t{i}", "props": {"text": "\U0001F600" * min(2000, chars - i * 2000)}}
        for i in range(0, -(-chars // 2000))]}


class SizeLimitTests(unittest.TestCase):
    def test_reviewer_emoji_tree_is_rejected_by_bytes(self):
        big = {"type": "column", "id": "c", "children": [
            {"type": "text", "id": f"t{i}", "props": {"text": "\U0001F600" * 2000}} for i in range(20)]}
        with self.assertRaisesRegex(UIError, "bytes as UTF-8 JSON"):
            validate_tree(big)
        bad = loom_mcp.call_tool("loom_ui_render", {"view_id": "v", "root": big})
        self.assertTrue(bad["isError"])

    def largest_view(self):
        """Grow the emoji payload until one more character crosses the byte limit."""
        lo, hi = 1, 8000
        while lo < hi:
            mid = (lo + hi + 1) // 2
            try:
                validate_tree(emoji_view(mid))
                lo = mid
            except UIError:
                hi = mid - 1
        return lo

    def test_multibyte_boundary_through_the_socket(self):
        chars = self.largest_view()
        fits, _ = validate_tree(emoji_view(chars))
        self.assertLessEqual(ui_tree.json_bytes(fits), ui_tree.MAX_VIEW_BYTES)
        self.assertGreater(ui_tree.json_bytes(fits), ui_tree.MAX_VIEW_BYTES - 8)
        with self.assertRaises(UIError):
            validate_tree(emoji_view(chars + 1))
        with redirect_stdout(io.StringIO()):
            state = TabbyState()
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}), \
                redirect_stdout(io.StringIO()):
            server = IPCServer(state.ui_view)
            server.start()
            try:
                title = "\U0001F600" * 120
                for i in range(ui_tree.MAX_VIEWS):  # every view at the limit, through the real transport
                    rendered = loom_mcp.call_tool("loom_ui_render", {"view_id": f"v{i}", "root": emoji_view(chars), "title": title})
                    self.assertFalse(rendered["isError"], rendered["structuredContent"])
                over = loom_mcp.call_tool("loom_ui_render", {"view_id": "v0", "root": emoji_view(chars + 1)})
                every = loom_mcp.call_tool("loom_ui_get", {})  # largest reply: all views at once
                state.ui_view({"command": "ui-close", "view_id": "v3"})
                state.ui_view({"command": "ui-render", "view_id": "ev", "root": {
                    "type": "input", "id": "in", "props": {"max_length": 500}}})
                for _ in range(ui_tree.MAX_EVENTS):
                    state.ui_view({"command": "ui-event", "view_id": "ev", "node_id": "in", "event": "submit",
                                   "value": "\U0001F600" * 500})
                pages, since = [], 0
                while not pages or pages[-1]["more"]:
                    page = loom_mcp.call_tool("loom_ui_events", {"since": since})
                    self.assertFalse(page["isError"], page["structuredContent"])
                    pages.append(page["structuredContent"])
                    since = pages[-1]["events"][-1]["seq"] if pages[-1]["events"] else since
            finally:
                server.stop()
        self.assertTrue(over["isError"])
        self.assertIn("too large", over["structuredContent"]["error"])
        self.assertFalse(every["isError"], every["structuredContent"])
        self.assertEqual(len(every["structuredContent"]["views"]), ui_tree.MAX_VIEWS)
        self.assertEqual(every["structuredContent"]["views"][0]["root"], fits)
        self.assertGreater(len(pages), 1)  # 200 x 2 KB of emoji cannot fit one 128 KiB reply
        seqs = [e["seq"] for page in pages for e in page["events"]]
        self.assertEqual(seqs, list(range(1, ui_tree.MAX_EVENTS + 1)))


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ui_templates.json"
        self.store = TemplateStore(self.path)
        self.tree = {"type": "card", "id": "confirm", "props": {"title": "Deploy {{service}}?"}, "children": [
            {"type": "text", "id": "msg", "props": {"text": "Target: {{env}}"}},
            {"type": "button", "id": "yes", "props": {"label": "Deploy"}, "on": {"press": [{"do": "emit", "name": "deploy.yes"}]}}]}

    def test_save_reopen_instantiate(self):
        saved = self.store.save("deploy.confirm", self.tree, "Confirm a deploy")
        self.assertEqual(saved["params"], ["env", "service"])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        again = TemplateStore(self.path)
        self.assertEqual([t["name"] for t in again.list()["templates"]], ["deploy.confirm"])
        root = again.instantiate("deploy.confirm", {"service": "matrix-bot", "env": "{{service}} <b>x</b>"})
        self.assertEqual(root["props"]["title"], "Deploy matrix-bot?")
        self.assertEqual(find(root, "msg")["props"]["text"], "Target: {{service}} <b>x</b>")  # single pass, plain text
        with self.assertRaisesRegex(UIError, "missing template param"):
            again.instantiate("deploy.confirm", {"service": "x"})
        with self.assertRaises(UIError):
            again.instantiate("deploy.confirm", {"service": "x", "env": 3})
        again.delete("deploy.confirm")
        self.assertEqual(again.list()["templates"], [])

    def test_invalid_templates_rejected_and_corruption_kept(self):
        with self.assertRaises(UIError):
            self.store.save("Bad Name", self.tree)
        with self.assertRaises(UIError):
            self.store.save("ok", {"type": "script", "id": "x"})
        self.assertFalse(self.path.exists())
        self.path.write_text("garbage")
        with self.assertRaisesRegex(ValueError, "unreadable"):
            self.store.save("ok", self.tree)
        self.assertEqual(self.path.read_text(), "garbage")


class MCPToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(loom_mcp, "UI_TEMPLATES", TemplateStore(Path(self.tmp.name) / "t.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent = []

    def call(self, name, args, reply=None):
        def fake(payload, timeout=3.0):
            self.sent.append(payload)
            return dict(reply or {"ok": True})
        with patch.object(loom_mcp, "send_command", fake):
            return loom_mcp.call_tool(name, args)

    def test_tools_are_registered_and_safe(self):
        names = {t["name"] for t in loom_mcp.tool_list()}
        expected = {"loom_ui_schema", "loom_ui_render", "loom_ui_patch", "loom_ui_undo", "loom_ui_redo", "loom_ui_get",
                    "loom_ui_close", "loom_ui_events", "loom_ui_template_save", "loom_ui_template_list",
                    "loom_ui_template_get", "loom_ui_template_delete", "loom_ui_template_render"}
        self.assertTrue(expected <= names)
        self.assertFalse(any("shell" in n or "exec" in n for n in names))
        self.assertIn("tabby_ui_render", loom_mcp.TOOL_INDEX)
        for tool in loom_mcp.tool_list():
            self.assertFalse(tool["inputSchema"].get("additionalProperties", False), tool["name"])

    def test_render_validates_before_ipc(self):
        ok = self.call("loom_ui_render", {"view_id": "build", "root": settings_view(), "title": "Build"})
        self.assertFalse(ok["isError"])
        self.assertEqual(self.sent[0]["command"], "ui-render")
        self.assertEqual(find(self.sent[0]["root"], "bar")["props"]["value"], 0.0)  # normalised
        bad = self.call("loom_ui_render", {"view_id": "build", "root": {"type": "webview", "id": "x"}})
        self.assertTrue(bad["isError"])
        self.assertIn("unknown component type", bad["structuredContent"]["error"])
        self.assertEqual(len(self.sent), 1)

    def test_patch_undo_events_passthrough(self):
        self.call("loom_ui_patch", {"view_id": "v", "ops": [{"op": "remove", "id": "x"}], "base_revision": 3})
        self.call("loom_ui_undo", {"view_id": "v"})
        self.call("loom_ui_events", {"since": 4, "view_id": "v"}, {"ok": True, "events": [{"seq": 5}], "last_seq": 5})
        self.assertEqual(self.sent, [
            {"command": "ui-patch", "view_id": "v", "ops": [{"op": "remove", "id": "x"}], "base_revision": 3},
            {"command": "ui-undo", "view_id": "v"},
            {"command": "ui-events", "since": 4, "view_id": "v"}])
        error = self.call("loom_ui_patch", {"view_id": "v", "ops": []}, {"ok": False, "error": "ops must be a list"})
        self.assertTrue(error["isError"])

    def test_template_save_from_view_and_render(self):
        view = {"ok": True, "view": {"root": validate_tree({"type": "text", "id": "t", "props": {"text": "Hi {{who}}"}})[0]}}
        saved = self.call("loom_ui_template_save", {"name": "greet", "from_view": "v"}, view)
        self.assertEqual(saved["structuredContent"]["params"], ["who"])
        self.call("loom_ui_template_render", {"name": "greet", "params": {"who": "Phil"}})
        self.assertEqual(self.sent[-1]["view_id"], "greet")
        self.assertEqual(self.sent[-1]["root"]["props"]["text"], "Hi Phil")
        self.assertEqual(loom_mcp.call_tool("loom_ui_template_list", {})["structuredContent"]["templates"][0]["name"], "greet")
        self.assertTrue(loom_mcp.call_tool("loom_ui_template_get", {"name": "nope"})["isError"])

    def test_schema_tool(self):
        result = loom_mcp.call_tool("loom_ui_schema", {})
        self.assertIn("slider", result["structuredContent"]["components"])


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.panel = (ROOT / "Panel.qml").read_text()
        start = self.panel.index("// ---- Declarative Loom UI")
        self.section = self.panel[start:self.panel.index("    function toneColour(", start)]

    def test_renderer_handles_every_component_type(self):
        for kind in ui_tree.COMPONENTS:
            self.assertIn(f'uiNode.kind === "{kind}"', self.section, kind)
        self.assertIn("model: T.LoomState.uiViewRows", self.panel)
        self.assertIn("reconcileUiViews(nextUiViews)", (ROOT / "services/LoomState.qml").read_text())

    def test_renderer_uses_theme_tokens_and_plain_text(self):
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|Qt\.rgba|Qt\.hsla", self.section))
        texts = self.section.count("StyledText {")
        self.assertGreater(texts, 10)
        self.assertEqual(self.section.count("textFormat: Text.PlainText"), texts)
        self.assertIn("Colours.palette.m3", self.section)
        # Interactions only ever reach the backend through loomctl ui-event.
        self.assertEqual(self.section.count("Quickshell.execDetached("), 1)

    def qml_function(self, name):
        start = self.section.index("function %s(" % name)
        depth, i = 0, self.section.index("{", start)
        while True:
            depth += {"{": 1, "}": -1}.get(self.section[i], 0)
            if depth == 0:
                break
            i += 1
        body = re.sub(r"\)\s*:\s*\w+\s*\{", ") {", self.section[start:i + 1], count=1)
        return re.sub(r"(\w+)\s*:\s*(?:bool|real|int|string|var)\b", r"\1", body)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_renderer_helpers_match_backend(self):
        script = ("const root = {python: '/usr/bin/python3', ctlPath: '/p/loomctl.py'};\n"
                  + "\n".join(self.qml_function(n) for n in ("uiHasType", "uiGap", "uiEventArgs", "uiSliderValue"))
                  + "\nroot.uiEventArgs = uiEventArgs;\n"
                  "const views = [{root: {type: 'card', children: [{type: 'row', children: [{type: 'input'}]}]}}];\n"
                  "console.log(JSON.stringify([\n"
                  "  uiHasType(views, 'input'), uiHasType(views, 'slider'), uiHasType([], 'input'),\n"
                  "  uiGap('none'), uiGap('large'), uiGap('bogus'),\n"
                  "  uiEventArgs('build', 'run', 'press', undefined),\n"
                  "  uiEventArgs('build', 'verbose', 'change', false),\n"
                  "  uiSliderValue({min: 0, max: 1, step: 0.25}, 0.6), uiSliderValue({min: 0, max: 10, step: 0}, 2),\n"
                  "]));\n")
        run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual(out[:6], [True, False, False, 0, 12, 8])
        self.assertEqual(out[6], ["/usr/bin/python3", "/p/loomctl.py", "ui-event", "build", "run", "press"])
        self.assertEqual(out[7][-1], "false")
        views = UIViews()
        views.render("build", settings_view())
        self.assertEqual(out[8], views.dispatch("build", "level", "change", 0.6)["value"])
        self.assertEqual(out[9], 10.0)


if __name__ == "__main__":
    unittest.main()
