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
import loom_tasks
from tabby import missions, tasks_tile
from tabby.spaces import SpaceStore

ROOT = Path(__file__).resolve().parents[1]


def failing(message="offline"):
    def fetch():
        raise missions.MissionBridgeError(message)
    return fetch


class ProjectionTests(unittest.TestCase):
    def state(self, **task):
        return tasks_tile.project_mission({"id": "m1", **task})

    def test_ledger_status_and_outcome_map_to_honest_states(self):
        cases = [
            ({"status": "running"}, "running"),
            ({"status": "queued"}, "queued"),
            ({"status": "review"}, "review"),
            ({"status": "paused"}, "blocked"),
            ({"status": "failed"}, "failed"),
            ({"status": "done", "outcome": "autonomous_verified_success"}, "verified"),
            ({"status": "done", "outcome": "reviewed_verified_success"}, "verified"),
            ({"status": "done", "outcome": "accepted_by_human"}, "accepted"),
            ({"status": "done"}, "done"),
            ({"status": "done", "outcome": "worker_claimed_success"}, "done"),
        ]
        for task, expected in cases:
            with self.subTest(task=task):
                self.assertEqual(self.state(**task)["state"], expected)
        self.assertEqual(self.state(status="done")["label"], "Done · unverified")

    def test_legacy_loom_states_are_not_mislabeled_unknown(self):
        for status, phase, label in (
            ("ready", "queued", "Queued"),
            ("in_progress", "running", "Running"),
            ("waiting_children", "running", "Running"),
            ("blocked_decision", "blocked", "Needs decision"),
            ("paused_usage", "paused_usage", "Usage paused"),
            ("stopped", "stopped", "Stopped"),
        ):
            with self.subTest(status=status):
                row=self.state(status=status)
                self.assertEqual((row["state"], row["label"]), (phase, label))

    def test_linked_ideas_follow_authoritative_ledger_status(self):
        rows=[{"id": str(i), "title": "Test", "mission_id": "m",
               "status": status} for i,status in enumerate(("running","review","blocked","failed","done"))]
        out=tasks_tile.project(None, {"ideas": rows})
        self.assertEqual({r["id"]:r["state"] for r in out["ideas"]},
                         {"0":"running","1":"review","2":"blocked","3":"failed","4":"verified"})
        unlinked=tasks_tile.project_idea({"id":"safe","title":"safe","status":"done"})
        self.assertEqual(unlinked["state"],"captured")

    def test_unknown_status_is_shown_verbatim_not_guessed(self):
        row = self.state(status="teleporting")
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["label"], "Unknown · teleporting")
        self.assertEqual(self.state()["label"], "Unknown · no status")

    def test_no_progress_or_invented_fields_leak(self):
        row = self.state(status="running", progress=0.73, percent=73, eta="5m", cpu=99)
        self.assertEqual(set(row), {"id", "kind", "title", "state", "label", "status", "outcome", "updated_at"})
        self.assertNotIn("%", json.dumps(row))

    def test_ideas_are_captured_not_executing(self):
        out = tasks_tile.project(None, {"ok": True, "ideas": [
            {"id": "i1", "title": "Mute fix"},
            {"id": "i2", "body": "Dark mode", "mission_id": "261008-abcd"},
            {"title": "no id is dropped"}]})
        self.assertIsNone(out["missions"])
        states = {i["id"]: (i["state"], i["label"]) for i in out["ideas"]}
        self.assertEqual(states["i1"], ("captured", "Captured"))
        self.assertEqual(states["i2"], ("linked", "Mission linked · 261008-abcd"))
        self.assertEqual(out["counts"], {"captured": 1, "linked": 1})

    def test_attention_states_sort_first_and_lists_are_bounded(self):
        tasks = [{"id": "a", "status": "done", "outcome": "accepted_by_human"},
                 {"id": "b", "status": "running", "updatedAt": "2026-10-08T10:00:00Z"},
                 {"id": "c", "status": "paused"},
                 {"id": "d", "status": "running", "updatedAt": "2026-10-08T11:00:00Z"}]
        order = [m["id"] for m in tasks_tile.project({"tasks": tasks}, None)["missions"]]
        self.assertEqual(order, ["c", "d", "b", "a"])
        many = {"tasks": [{"id": str(n), "status": "queued"} for n in range(100)]}
        self.assertEqual(len(tasks_tile.project(many, None)["missions"]), tasks_tile.MAX_MISSIONS)

    def test_timestamps_and_hostile_text(self):
        self.assertEqual(tasks_tile._time(1_700_000_000_000), 1_700_000_000.0)
        self.assertIsNone(tasks_tile._time("not a date"))
        self.assertIsNone(tasks_tile._time(True))
        row = self.state(status="running", title="x\n" * 500)
        self.assertLessEqual(len(row["title"]), 160)
        self.assertNotIn("\n", row["title"])


class CacheLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state" / "loom-tasks.json"
        self.cache = tasks_tile.TasksCache(self.path)

    def ok_missions(self):
        return {"ok": True, "tasks": [{"id": "m1", "title": "Fix", "status": "running"}]}

    def ok_ideas(self):
        return {"ok": True, "ideas": [{"id": "i1", "title": "Idea"}]}

    def test_missing_or_corrupt_cache_is_empty_and_stale(self):
        self.assertTrue(self.cache.load()["stale"])
        self.path.parent.mkdir(parents=True)
        self.path.write_text("garbage")
        snap = self.cache.load()
        self.assertEqual((snap["missions"], snap["fetched_at"]), ([], None))
        self.path.write_text(json.dumps({"version": 99}))
        self.assertEqual(self.cache.load()["errors"], ["not refreshed yet"])

    def test_refresh_persists_private_atomic_snapshot(self):
        snap = tasks_tile.refresh(self.cache, fetch_missions=self.ok_missions,
                                  fetch_ideas=self.ok_ideas, now=1000.0)
        self.assertFalse(snap["stale"])
        self.assertEqual(snap["fetched_at"], 1000.0)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.assertEqual(tasks_tile.TasksCache(self.path).load(), snap)

    def test_failed_refresh_keeps_last_good_rows_and_marks_stale(self):
        tasks_tile.refresh(self.cache, fetch_missions=self.ok_missions, fetch_ideas=self.ok_ideas, now=1000.0)
        snap = tasks_tile.refresh(self.cache, fetch_missions=failing("Philipedia connection failed"),
                                  fetch_ideas=failing(), now=2000.0)
        self.assertTrue(snap["stale"])
        self.assertEqual(snap["fetched_at"], 1000.0)
        self.assertEqual(snap["missions"][0]["id"], "m1")
        self.assertEqual(len(snap["errors"]), 2)
        self.assertIn("connection failed", snap["errors"][0])

    def test_partial_failure_updates_only_the_working_source(self):
        tasks_tile.refresh(self.cache, fetch_missions=self.ok_missions, fetch_ideas=self.ok_ideas, now=1000.0)
        snap = tasks_tile.refresh(self.cache, fetch_missions=lambda: {"ok": True, "tasks": [
            {"id": "m1", "status": "done", "outcome": "reviewed_verified_success"}]},
            fetch_ideas=failing(), now=2000.0)
        self.assertEqual(snap["missions"][0]["state"], "verified")
        self.assertEqual(snap["ideas"][0]["id"], "i1")
        self.assertTrue(snap["stale"])
        self.assertEqual(snap["counts"], {"verified": 1, "captured": 1})

    def test_malformed_bridge_payload_is_an_error_not_an_empty_list(self):
        tasks_tile.refresh(self.cache, fetch_missions=self.ok_missions, fetch_ideas=self.ok_ideas, now=1000.0)
        snap = tasks_tile.refresh(self.cache, fetch_missions=lambda: {"ok": True},
                                  fetch_ideas=self.ok_ideas, now=2000.0)
        self.assertIn("missions: bridge response had no task list", snap["errors"])
        self.assertEqual(snap["missions"][0]["id"], "m1")

    def test_recovery_clears_stale(self):
        tasks_tile.refresh(self.cache, fetch_missions=failing(), fetch_ideas=failing(), now=1000.0)
        snap = tasks_tile.refresh(self.cache, fetch_missions=self.ok_missions,
                                  fetch_ideas=self.ok_ideas, now=3000.0)
        self.assertEqual((snap["stale"], snap["errors"], snap["fetched_at"]), (False, [], 3000.0))

    def test_default_fetchers_use_fixed_bridge_actions(self):
        sent = []
        def fake(payload, **kw):
            sent.append(payload["action"])
            return {"ok": True, "tasks": [], "ideas": []}
        with patch.object(missions, "request", side_effect=fake):
            tasks_tile.refresh(self.cache)
        self.assertEqual(sent, ["status", "inbox"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SpaceStore(Path(self.tmp.name) / "modules.json")
        self.cache = tasks_tile.TasksCache(Path(self.tmp.name) / "loom-tasks.json")

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = loom_tasks.main(list(argv), store=self.store, cache=self.cache)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "QML host expects exactly one JSON line")
        return code, json.loads(lines[0])

    def test_tile_is_idempotent_and_on_performance_surface(self):
        code, first = self.run_cli("tile")
        self.assertEqual(code, 0)
        self.assertEqual((first["kind"], first["placement"]["surface"]), ("tasks", "performance"))
        _, again = self.run_cli("tile")
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(self.store.list()["modules"]), 1)

    def test_modules_lists_every_supported_native_kind(self):
        expected = []
        for kind in ("tasks", "cpu", "memory", "storage", "battery", "weather"):
            expected.append(self.store.create_module(
                kind=kind, title=kind, placement={"surface": "floating"})["id"])
        board = self.store.create_module(kind="cpu", title="board")
        text = self.store.create_module(kind="text", title="text", placement={"surface": "floating"})
        code, result = self.run_cli("modules")
        self.assertEqual(code, 0)
        ids = [module["id"] for module in result["modules"]]
        self.assertTrue(set(expected).issubset(ids))
        self.assertNotIn(board["id"], ids)
        self.assertNotIn(text["id"], ids)

    def test_modules_on_empty_store_is_read_only_and_has_no_visible_modules(self):
        self.assertFalse(self.store.path.exists())
        code, result = self.run_cli("modules")
        self.assertEqual((code, result), (0, {"version": 1, "modules": []}))
        self.assertFalse(self.store.path.exists())

    def test_reserved_hover_tile_is_not_a_native_surface(self):
        _, tile = self.run_cli("tile")
        native = self.store.create_module(
            kind="tasks", title="Native tasks", placement={"surface": "performance"})
        code, result = self.run_cli("modules")
        self.assertEqual(code, 0)
        self.assertEqual([module["id"] for module in result["modules"]], [native["id"]])
        self.assertNotEqual(tile["id"], native["id"])

    def test_unrelated_performance_tasks_module_is_not_selected_as_reserved_tile(self):
        mine = self.store.create_module(kind="tasks", title="My sprint", data={"source": "user"},
                                        placement={"surface": "performance", "width": 500, "height": 500})
        hidden = self.store.create_module(kind="tasks", title="Hidden", visible=False,
                                          placement={"surface": "performance"})
        space = self.store.save_space(name="Mine", module_ids=[mine["id"], hidden["id"]])
        before = self.store.list()
        _, tile = self.run_cli("tile")
        self.assertNotIn(tile["id"], (mine["id"], hidden["id"]))
        self.assertEqual(tile["data"], loom_tasks.TILE_DATA)
        _, again = self.run_cli("tile")
        self.assertEqual(again["id"], tile["id"])
        code, resized = self.run_cli("resize", mine["id"], "300", "200")
        self.assertEqual((code, resized["placement"]["width"]), (0, 300))
        self.assertEqual(self.run_cli("resize", tile["id"], "420", "330")[0], 0)
        self.assertEqual(self.store.get_module(mine["id"])["placement"]["width"], 300)
        self.assertEqual(self.store.get_module(hidden["id"]), before["modules"][1])
        self.assertEqual(self.store.get_space(space["id"])["space"], before["spaces"][0])
        self.assertEqual(len(self.store.list()["modules"]), 3)

    def test_deleted_tile_is_recreated_not_replaced_by_another_module(self):
        _, tile = self.run_cli("tile")
        other = self.store.create_module(kind="tasks", title="Other", placement={"surface": "performance"})
        self.store.delete_module(tile["id"])
        self.assertIsNone(self.store.module_for_request(loom_tasks.TILE_REQUEST_ID))
        _, fresh = self.run_cli("tile")
        self.assertNotIn(fresh["id"], (tile["id"], other["id"]))
        self.assertEqual(fresh["data"]["role"], "loom-panel-tasks-tile")

    def test_resize_persists_and_survives_reopen(self):
        _, tile = self.run_cli("tile")
        code, resized = self.run_cli("resize", tile["id"], "520", "410")
        self.assertEqual(code, 0)
        reopened = SpaceStore(self.store.path).get_module(tile["id"])["placement"]
        self.assertEqual((reopened["width"], reopened["height"]), (520, 410))
        self.assertEqual(reopened["surface"], "performance")
        _, again = self.run_cli("tile")
        self.assertEqual(again["placement"]["width"], 520)

    def test_place_persists_anchor_and_offsets_without_losing_size(self):
        _, tile = self.run_cli("tile")
        self.run_cli("resize", tile["id"], "520", "410")
        code, placed = self.run_cli("place", tile["id"], "bottom-right", "31", "47")
        self.assertEqual(code, 0)
        self.assertEqual(placed["placement"], {
            "surface": "performance", "anchor": "bottom-right", "x": 31, "y": 47,
            "width": 520, "height": 410, "monitor": "", "workspace": ""
        })
        self.assertEqual(SpaceStore(self.store.path).get_module(tile["id"])["placement"], placed["placement"])

    def test_place_rejects_invalid_anchor_and_non_owned_module(self):
        _, tile = self.run_cli("tile")
        code, err = self.run_cli("place", tile["id"], "sideways", "1", "2")
        self.assertEqual((code, err["ok"]), (1, False))
        other = self.store.create_module(kind="tasks", title="Other")
        code, err = self.run_cli("place", other["id"], "top-left", "1", "2")
        self.assertEqual((code, err["ok"]), (1, False))

    def test_resize_rejects_bad_geometry_and_non_tasks_modules(self):
        _, tile = self.run_cli("tile")
        code, err = self.run_cli("resize", tile["id"], "20", "410")
        self.assertEqual((code, err["ok"]), (1, False))
        note = self.store.create_module(kind="text", title="Note")
        code, err = self.run_cli("resize", note["id"], "300", "300")
        self.assertIn("not supported by the native surface host", err["error"])
        code, err = self.run_cli("resize", "missing", "300", "300")
        self.assertEqual(code, 1)

    def test_snapshot_never_touches_network(self):
        with patch.object(missions, "request", side_effect=AssertionError("network")):
            code, snap = self.run_cli("snapshot")
        self.assertEqual((code, snap["version"], snap["stale"]), (0, 1, True))

    def test_refresh_offline_still_prints_one_stale_line(self):
        with patch.object(missions, "request", side_effect=missions.MissionBridgeError("offline")):
            code, snap = self.run_cli("refresh")
        self.assertEqual((code, snap["stale"]), (0, True))

    def test_argument_syntax(self):
        for argv in ([], ["bogus"], ["resize", "id", "wide", "10"], ["resize", "id"],
                     ["refresh", "--host", "evil"], ["snapshot", "extra"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as ctx, \
                 redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
                loom_tasks.main(argv, store=self.store, cache=self.cache)
            self.assertEqual(ctx.exception.code, 2)

    def test_executable_entry_point(self):
        env = {**os.environ, "HOME": self.tmp.name}
        helped = subprocess.run([sys.executable, str(ROOT / "loom_tasks.py"), "--help"],
                                capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(helped.returncode, 0)
        for word in ("snapshot", "refresh", "tile", "modules", "resize", "place"):
            self.assertIn(word, helped.stdout)
        snap = subprocess.run([sys.executable, str(ROOT / "loom_tasks.py"), "snapshot"],
                              capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(snap.returncode, 0)
        self.assertEqual(json.loads(snap.stdout)["version"], 1)
        self.assertFalse((Path(self.tmp.name) / ".local").exists(), "snapshot must not write")


class McpAndRendererTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SpaceStore(Path(self.tmp.name) / "modules.json")
        self.cache = tasks_tile.TasksCache(Path(self.tmp.name) / "loom-tasks.json")
        for name, value in (("SPACE_STORE", self.store), ("TASKS_CACHE", self.cache)):
            p = patch.object(loom_mcp, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_snapshot_tool_is_read_only_and_cached(self):
        tool = next(t for t in loom_mcp.tool_list() if t["name"] == "loom_tasks_snapshot")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertIn("tabby_tasks_snapshot", loom_mcp.TOOL_INDEX)
        tasks_tile.refresh(self.cache, fetch_missions=lambda: {"ok": True, "tasks": [{"id": "m", "status": "review"}]},
                           fetch_ideas=lambda: {"ok": True, "ideas": []}, now=5.0)
        with patch.object(missions, "request", side_effect=AssertionError("network")):
            result = loom_mcp.call_tool("loom_tasks_snapshot", {})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["missions"][0]["state"], "review")

    def test_space_show_reports_native_tasks_renderer_and_keeps_fallbacks(self):
        tile = loom_tasks.ensure_tile(self.store)
        cpu = self.store.create_module(kind="cpu", title="CPU", placement={"surface": "performance"})
        board_cpu = self.store.create_module(kind="cpu", title="CPU board")
        space = self.store.save_space(name="Perf", module_ids=[tile["id"], cpu["id"], board_cpu["id"]])
        with patch.object(loom_mcp, "_display") as display:
            shown = loom_mcp.call_tool("loom_space_show", {"space_id": space["id"]})["structuredContent"]
        display.assert_not_called()
        reasons = {s["module_id"]: s["reason"] for s in shown["skipped"]}
        self.assertEqual(reasons[tile["id"]], "reserved hover tile")
        self.assertEqual(shown["rendered_ids"], [cpu["id"]])
        self.assertFalse(shown["board_visible"])
        self.assertNotIn(cpu["id"], reasons)
        self.assertEqual(reasons[board_cpu["id"]], "native live cpu renderer pending")


class QmlStaticTests(unittest.TestCase):
    """Static checks only: no QML runtime is available on the headless test host."""

    def card_source(self):
        # The card source is inline in Panel.qml to work with versioned QML URLs.
        panel = self.read("Panel.qml")
        return panel.split("component TasksView:", 1)[1].split("    property real phase:", 1)[0]

    def read(self, name):
        if name == "LoomTasksCard.qml":
            return self.card_source()
        return (ROOT / name).read_text(encoding="utf8")

    def test_braces_balance_and_card_uses_shell_styling(self):
        for name in ("Main.qml", "Panel.qml", "FloatingWidgets.qml", "services/LoomState.qml"):
            text = re.sub(r'"(?:\\.|[^"\\])*"|`[^`]*`|//[^\n]*', "", self.read(name))
            with self.subTest(name=name):
                self.assertEqual(text.count("{"), text.count("}"))
                self.assertEqual(text.count("("), text.count(")"))
        card = self.read("LoomTasksCard.qml")
        for needle in ("Colours.palette", "Colours.tPalette", "StyledRect", "StyledText", "Tokens"):
            self.assertIn(needle, card)

    def test_card_has_no_progress_or_sensor_rendering(self):
        card = re.sub(r"//[^\n]*", "", self.read("LoomTasksCard.qml")).lower()
        for banned in ("progress", "percent", "%", "cpu", "temperature"):
            self.assertNotIn(banned, card, msg=banned)

    def test_card_rows_avoid_item_state_and_sync_in_place(self):
        card = self.read("LoomTasksCard.qml")
        self.assertNotIn("required property string state", card)
        self.assertIn("rows_.set(i, row)", card)
        self.assertIn("rows_.move(j, i, 1)", card)

    def test_top_shell_task_views_never_stay_open_without_counter_hover(self):
        panel = self.read("Panel.qml")
        self.assertIn("readonly property bool idleWorkingExpanded: chipVisible && chipExpanded && hasWorking", panel)
        self.assertRegex(panel, r"readonly property bool workingListVisible:\s*idleWorkingExpanded")
        self.assertRegex(panel, r"readonly property bool tasksCardVisible:\s*T\.LoomState\.tasksPanelVisible && idleWorkingExpanded")
        self.assertIn("if (!hover.hovered && !root.panelHostHovered && !chipHover.hovered)", panel)

    def test_native_floating_host_has_real_shell_bindings(self):
        main = self.read("Main.qml")
        host = self.read("FloatingWidgets.qml")
        self.assertIn('source: Qt.resolvedUrl("FloatingWidgets.qml")', main)
        for needle in ("PanelWindow", "Colours.palette", "Colours.tPalette", "Cpu.percentage",
                       "Memory.percentage", "Storage.primaryDisk?.perc", "Weather.temp",
                       "Weather.description", "UPower.displayDevice.percentage",
                       "ServiceRef { service: Cpu }", "ServiceRef { service: Memory }",
                       "ServiceRef { service: Storage }"):
            self.assertIn(needle, host)
        self.assertNotIn("Storage.percentage", host)
        for needle in ("anchors.top: projected.top", "margins.left:", 'root.persist(module, "place"',
                       'root.persist(module, "resize"', "placement.monitor", "Instantiator",
                       "T.LoomState.surfaceModules", "mapToGlobal(centroid.position)"):
            self.assertIn(needle, host)

    def test_floating_release_updates_shared_module_before_clearing_live_geometry(self):
        host = self.read("FloatingWidgets.qml")
        self.assertIn("function commitPlacement", host)
        self.assertIn("function commitSize", host)
        self.assertEqual(host.count("T.LoomState.replaceSurfaceModule(updated)"), 2)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_global_drag_and_edge_aware_resize_math_executes(self):
        drag = self.qml_function("FloatingWidgets.qml", "dragOffset").replace("function dragOffset", "function")
        resize = self.qml_function("FloatingWidgets.qml", "resizeFromGlobal").replace("function resizeFromGlobal", "function")
        script = (f"const dragOffset={drag}; const resizeFromGlobal={resize};"
                  "console.log(JSON.stringify([dragOffset(10,20,{x:100,y:100},{x:130,y:140},false,false),"
                  "dragOffset(50,60,{x:100,y:100},{x:130,y:140},true,true),"
                  "resizeFromGlobal(400,300,{x:100,y:100},{x:130,y:140},true,true)]));")
        run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), [
            {"x": 40, "y": 60}, {"x": 20, "y": 20}, {"width": 370, "height": 260}
        ])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_floating_geometry_projection_executes(self):
        fn = self.qml_function("FloatingWidgets.qml", "geometry")
        script = ("const geometry=" + fn.replace("function geometry", "function") + ";"
                  + "console.log(JSON.stringify([geometry('top-left',12.4,9.7),geometry('bottom-right',3,4),geometry('center',-23.4,-41.6)]));")
        run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        values = json.loads(run.stdout)
        self.assertEqual(values[0], {"top": True, "bottom": False, "left": True, "right": False,
                                     "centered": False, "horizontal": 12, "vertical": 10})
        self.assertEqual((values[1]["bottom"], values[1]["right"]), (True, True))
        self.assertTrue(values[2]["centered"])
        self.assertEqual((values[2]["horizontal"], values[2]["vertical"]), (-23, -42))

    def qml_function(self, name, func):
        text = self.read(name)
        start = text.index("function %s(" % func)
        depth, i = 0, text.index("{", start)
        while True:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                break
            i += 1
        body = text[start:i + 1]
        # Drop QML type annotations so plain JavaScript can run it.
        body = re.sub(r"\)\s*:\s*\w+\s*\{", ") {", body, count=1)
        return re.sub(r"(\w+)\s*:\s*(?:bool|real|int|string|var)\b", r"\1", body)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_viewer_registration_lifecycle_runs_real_qml_functions(self):
        card = self.read("LoomTasksCard.qml")
        self.assertIn("onVisibleChanged: if (completed) syncViewer(visible)", card)
        self.assertIn("Component.onDestruction: syncViewer(false)", card)
        self.assertRegex(card, r"completed = true;\s*syncViewer\(visible\);")
        script = (
            "const S = {tasksViewers: 0};\n"
            # `with` reproduces QML's lookup of bare names on the owning object.
            "with (S) { S.setTasksViewer = " + self.qml_function("services/LoomState.qml", "setTasksViewer")
            .replace("function setTasksViewer", "function") + "; }\n"
            "const T = {LoomState: S};\n"
            "function Card(visible) {\n"
            "  const c = {visible, completed: false, viewerRegistered: false};\n"
            "  with (c) { c.syncViewer = " + self.qml_function("LoomTasksCard.qml", "syncViewer")
            .replace("function syncViewer", "function") + "; }\n"
            "  c.setVisible = v => { c.visible = v; if (c.completed) c.syncViewer(v); };\n"
            "  c.complete = () => { c.completed = true; c.syncViewer(c.visible); };\n"
            "  c.destroy = () => c.syncViewer(false);\n"
            "  return c;\n"
            "}\n"
            "const out = [];\n"
            "const panel = Card(true); panel.setVisible(false); panel.complete(); out.push(S.tasksViewers);\n"
            "panel.setVisible(true); out.push(S.tasksViewers);\n"
            "panel.setVisible(true); out.push(S.tasksViewers);\n"
            "const host = Card(true); host.complete(); out.push(S.tasksViewers);\n"
            "panel.setVisible(false); out.push(S.tasksViewers);\n"
            "panel.setVisible(false); out.push(S.tasksViewers);\n"
            "host.destroy(); out.push(S.tasksViewers);\n"
            "panel.destroy(); out.push(S.tasksViewers);\n"
            "panel.setVisible(true); panel.destroy(); out.push(S.tasksViewers);\n"
            "console.log(JSON.stringify(out));\n")
        run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        # hidden construction=0, shown=1 (timer runs), repeat=1, 2nd card=2,
        # hide=1, repeat hide=1, destroy visible=0, destroy hidden=0, re-show+destroy=0
        self.assertEqual(json.loads(run.stdout), [0, 1, 1, 2, 1, 1, 0, 0, 0])

    def test_lifecycle_wiring(self):
        main = self.read("Main.qml")
        self.assertIn('"refresh"]', main)
        self.assertIn("running: T.LoomState.tasksViewers > 0", main)
        self.assertIn("if (tasksRefresh.running) return;", main)
        self.assertIn("tasksRefresh.running = false", main)
        state = self.read("services/LoomState.qml")
        reset = state[state.index("function reset()"):]
        self.assertNotIn("tasks", reset, "backend restarts must not wipe the ledger snapshot")
        self.assertIn("readonly property bool tasksCardVisible: T.LoomState.tasksPanelVisible",
                      self.read("Panel.qml"))


if __name__ == "__main__":
    unittest.main()
