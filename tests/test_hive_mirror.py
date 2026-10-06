"""HAG-23: Hive ledger writer (scripts/hive_task.py) and Working mirror (scripts/hive_mirror.py)."""
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hive_task  # noqa: E402
from hive_mirror import HiveIO, Mirror, view  # noqa: E402
from tabby.working import WorkingStore  # noqa: E402


def write_ledger(root: Path, tasks, nxt=5, archive=None):
    (root / "tasks.json").write_text(json.dumps({"tasks": tasks, "ticket": {"prefix": "HAG", "next": nxt, "aliases": {}}}, indent=2))
    if archive is not None:
        (root / "tasks-archive.json").write_text(json.dumps({"tasks": archive}))


def card(root: Path, cid: str):
    return next(c for c in json.loads((root / "tasks.json").read_text())["tasks"] if c["id"] == cid)


def _worker(root, n, field):
    led = hive_task.Ledger(root, by=f"w{n}")
    for i in range(10):
        led.update("HAG-1", {f"{field}{n}_{i}": i})
        led.create({"title": f"w{n}-{i}"})


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_ledger(self.root, [
            {"id": "HAG-1", "title": "one", "status": "todo", "assignee": "a", "humanQA": [{"q": "x"}]},
            {"id": "HAG-2", "title": "two → unicode", "status": "doing", "custom": {"keep": True}},
        ], archive=[{"id": "HAG-9", "title": "old"}])
        self.led = hive_task.Ledger(self.root, by="test")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_uses_counter_above_archived_ids(self):
        res = self.led.create({"title": "new", "status": "doing", "assignee": "b"})
        self.assertEqual(res["id"], "HAG-10")  # counter says 5, archive already used 9
        doc = json.loads((self.root / "tasks.json").read_text())
        self.assertEqual(doc["ticket"], {"prefix": "HAG", "next": 11, "aliases": {}})
        made = card(self.root, "HAG-10")
        self.assertTrue(made["createdAt"] and made["startedAt"] and made["updatedAt"])
        self.assertEqual(made["updatedBy"], "test")

    def test_update_stamps_times_and_keeps_everything_else(self):
        before = card(self.root, "HAG-2")
        self.led.update("HAG-1", {"status": "doing"})
        self.assertIn("startedAt", card(self.root, "HAG-1"))
        self.led.complete("HAG-1", "shipped")
        c1 = card(self.root, "HAG-1")
        self.assertEqual((c1["status"], c1["result"]), ("done", "shipped"))
        self.assertIn("doneAt", c1)
        self.assertEqual(c1["humanQA"], [{"q": "x"}])
        self.assertEqual(card(self.root, "HAG-2"), before)
        # Same bytes the hive app writes: JSON.stringify(doc, null, 2), raw unicode, no newline.
        raw = (self.root / "tasks.json").read_text()
        self.assertIn("two → unicode", raw)
        self.assertFalse(raw.endswith("\n"))
        self.led.update("HAG-1", {"status": "doing"})
        self.assertIn("reopenedAt", card(self.root, "HAG-1"))

    def test_if_status_guard_and_no_op(self):
        res = self.led.complete("HAG-1", "x", if_status=["doing", "blocked"])
        self.assertFalse(res["changed"])
        self.assertIn("expected", res["skipped"])
        self.assertEqual(card(self.root, "HAG-1")["status"], "todo")
        mtime = (self.root / "tasks.json").stat().st_mtime_ns
        self.assertFalse(self.led.update("HAG-1", {"status": "todo"})["changed"])
        self.assertEqual((self.root / "tasks.json").stat().st_mtime_ns, mtime)

    def test_rejects_bad_input(self):
        for bad in ({"status": "wip"}, {"id": "HAG-3"}, {"bad key": 1}, {}):
            with self.assertRaises(hive_task.LedgerError):
                self.led.update("HAG-1", bad)
        with self.assertRaises(hive_task.LedgerError):
            self.led.update("HAG-404", {"status": "doing"})
        with self.assertRaises(hive_task.LedgerError):
            hive_task.run_request({"op": "update", "id": "../x", "patch": {"a": 1}}, self.root)

    def test_null_removes_field(self):
        self.led.update("HAG-2", {"blocked": "why"})
        self.led.update("HAG-2", {"blocked": None})
        self.assertNotIn("blocked", card(self.root, "HAG-2"))

    def test_concurrent_writers_lose_nothing(self):
        procs = [multiprocessing.Process(target=_worker, args=(str(self.root), n, "f")) for n in range(6)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
            self.assertEqual(p.exitcode, 0)
        c1 = card(self.root, "HAG-1")
        for n in range(6):
            for i in range(10):
                self.assertEqual(c1[f"f{n}_{i}"], i)
        ids = [c["id"] for c in json.loads((self.root / "tasks.json").read_text())["tasks"]]
        self.assertEqual(len(ids), 62)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse((self.root / "tasks.json.lock").exists())

    def test_stale_lock_is_taken_over_live_lock_waits(self):
        lock = self.root / "tasks.json.lock"
        lock.mkdir()
        (lock / "owner").write_text(json.dumps({"pid": 2 ** 22 + 7, "time": int(time.time() * 1000)}))
        self.led.update("HAG-1", {"note": "after dead owner"})
        self.assertFalse(lock.exists())
        lock.mkdir()
        (lock / "owner").write_text(json.dumps({"pid": os.getpid(), "time": int(time.time() * 1000)}))
        with patch.object(hive_task, "LOCK_TRIES", 3), self.assertRaises(hive_task.LedgerError):
            self.led.update("HAG-1", {"note": "blocked"})
        self.assertEqual(card(self.root, "HAG-1")["note"], "after dead owner")
        old = {"pid": os.getpid(), "time": int((time.time() - 60) * 1000)}
        (lock / "owner").write_text(json.dumps(old))
        self.led.update("HAG-1", {"note": "after old owner"})
        self.assertEqual(card(self.root, "HAG-1")["note"], "after old owner")

    def test_unlocked_writer_before_compare_is_kept(self):
        """The hive app writes without the lock between our read and our rename."""
        real = hive_task._dump
        fired = []

        def racer(doc):
            out = real(doc)
            if not fired:
                fired.append(1)
                other = json.loads((self.root / "tasks.json").read_text())
                other["tasks"].append({"id": "HAG-77", "title": "app wrote this"})
                (self.root / "tasks.json").write_text(json.dumps(other, indent=2))
            return out

        with patch.object(hive_task, "_dump", racer):
            self.led.update("HAG-1", {"status": "doing"})
        self.assertEqual(card(self.root, "HAG-77")["title"], "app wrote this")
        self.assertEqual(card(self.root, "HAG-1")["status"], "doing")

    def test_unlocked_writer_replacing_ours_is_redone(self):
        stale = (self.root / "tasks.json").read_text()
        fired = []
        real_sleep = time.sleep

        def clobber(s):
            if s == hive_task.VERIFY_DELAYS_S[0] and not fired:
                fired.append(1)
                (self.root / "tasks.json").write_text(stale)  # app wrote its stale copy over ours
            real_sleep(0)

        with patch.object(hive_task.time, "sleep", clobber):
            self.led.update("HAG-1", {"status": "doing"})
        self.assertTrue(fired)
        self.assertEqual(card(self.root, "HAG-1")["status"], "doing")

    def test_newer_edit_after_ours_is_not_redone_over(self):
        real_sleep = time.sleep

        def newer(s):
            if s == hive_task.VERIFY_DELAYS_S[0]:
                doc = json.loads((self.root / "tasks.json").read_text())
                c = next(c for c in doc["tasks"] if c["id"] == "HAG-1")
                c.update(status="done", updatedAt="2999-01-01T00:00:00.000Z")
                (self.root / "tasks.json").write_text(json.dumps(doc))
            real_sleep(0)

        with patch.object(hive_task.time, "sleep", newer):
            self.led.update("HAG-1", {"status": "doing"})
        self.assertEqual(card(self.root, "HAG-1")["status"], "done")

    def test_clobber_caught_by_the_second_check(self):
        stale = (self.root / "tasks.json").read_text()
        real_sleep = time.sleep

        def late(s):
            if s == hive_task.VERIFY_DELAYS_S[1] and not late.fired:
                late.fired = True
                (self.root / "tasks.json").write_text(stale)
            real_sleep(0)
        late.fired = False

        with patch.object(hive_task.time, "sleep", late):
            self.led.update("HAG-1", {"status": "doing"})
        self.assertTrue(late.fired)
        self.assertEqual(card(self.root, "HAG-1")["status"], "doing")

    def test_paused_old_owner_cannot_release_the_new_owners_lock(self):
        """Levi HAG-23 #1: A pauses > 30 s, B takes the lock over, A resumes."""
        path = self.root / "tasks.json"
        a = hive_task.LedgerLock(path, "A").__enter__()
        owner = a.lock / "owner"
        old = json.loads(owner.read_text()); old["time"] -= 60_000  # A has been paused for a minute
        owner.write_text(json.dumps(old))
        b = hive_task.LedgerLock(path, "B").__enter__()  # B judges A stale and takes over
        self.assertTrue(b.owned())
        self.assertFalse(a.owned())
        a.__exit__(None, None, None)  # A resumes and leaves
        self.assertTrue(b.owned(), "A must not delete B's lock")
        with patch.object(hive_task, "LOCK_TRIES", 3), self.assertRaises(hive_task.LedgerError):
            hive_task.LedgerLock(path, "C").__enter__()  # no third writer gets in
        b.__exit__(None, None, None)
        self.assertFalse(a.lock.exists())
        self.assertEqual([p.name for p in self.root.iterdir() if ".lock" in p.name], [])

    def test_writer_whose_lock_was_taken_over_does_not_rename(self):
        led = hive_task.Ledger(self.root, by="A")
        calls = []

        def steal_once(doc):
            calls.append(1)
            if len(calls) == 1:  # someone took the lock over while we were working
                lock = self.root / "tasks.json.lock"
                (lock / "owner").write_text(json.dumps({"pid": 2 ** 22 + 7, "time": int(time.time() * 1000), "token": "thief"}))
                self.before = (self.root / "tasks.json").read_bytes()
            card_ = hive_task.find(doc["tasks"], "HAG-1")
            card_["note"] = "ours"
            hive_task.stamp(card_, "todo", hive_task.now_iso())
            return card_

        led._write(steal_once, lambda doc, c: hive_task.find(doc["tasks"], "HAG-1").get("note") == "ours")
        self.assertEqual(len(calls), 2, "first attempt aborted, second applied")
        self.assertEqual(card(self.root, "HAG-1")["note"], "ours")
        self.assertFalse((self.root / "tasks.json.lock").exists())

    def test_empty_or_corrupt_files_fail_closed(self):
        (self.root / "tasks-archive.json").write_text("")
        with self.assertRaisesRegex(hive_task.LedgerError, "cannot pick a safe ticket id"):
            self.led.create({"title": "x"})
        (self.root / "tasks-archive.json").write_text('{"tasks": [{"id": "HAG-9"')
        with self.assertRaisesRegex(hive_task.LedgerError, "cannot pick a safe ticket id"):
            self.led.create({"title": "x"})
        (self.root / "tasks-archive.json").unlink()
        self.assertEqual(self.led.create({"title": "x"})["id"], "HAG-5")  # missing archive = none archived
        before = (self.root / "tasks.json").read_bytes()
        (self.root / "tasks.json").write_text("")
        for op in (lambda: self.led.create({"title": "y"}), lambda: self.led.update("HAG-1", {"status": "doing"}),
                   lambda: self.led.list()):
            with self.assertRaisesRegex(hive_task.LedgerError, "empty"):
                op()
        self.assertEqual((self.root / "tasks.json").read_bytes(), b"")  # nothing written over it
        (self.root / "tasks.json").write_bytes(before[:40])
        with self.assertRaisesRegex(hive_task.LedgerError, "not valid JSON"):
            self.led.update("HAG-1", {"status": "doing"})
        (self.root / "tasks.json").unlink()
        self.assertEqual(self.led.create({"title": "fresh hive"})["id"], "HAG-1")  # missing ledger = new hive

    def test_cli(self):
        script = ROOT / "scripts/hive_task.py"
        run = lambda *a: json.loads(subprocess.run([sys.executable, str(script), "--root", str(self.root), "--by", "cli", *a],
                                                   capture_output=True, text=True).stdout)
        self.assertEqual(run("update", "HAG-1", "status=blocked", "humanQA:=[]", "blocked=needs Chrome")["status"], "blocked")
        self.assertEqual(card(self.root, "HAG-1")["humanQA"], [])
        self.assertEqual(run("complete", "HAG-1", "done it", "--if-status", "doing")["changed"], False)
        self.assertEqual(run("show", "HAG-1")["card"]["blocked"], "needs Chrome")
        self.assertEqual([c["id"] for c in run("list", "--status", "doing,blocked")["tasks"]], ["HAG-1", "HAG-2"])
        self.assertFalse(run("update", "HAG-1", "status=nope")["ok"])
        # Machine form, as the Tabby mirror and Loom send it: script on stdin, request in argv.
        out = subprocess.run([sys.executable, "-", "--root", str(self.root), "--request",
                              json.dumps({"op": "complete", "id": "HAG-1", "result": "r", "by": "x"})],
                             input=script.read_bytes(), capture_output=True)
        self.assertTrue(json.loads(out.stdout)["changed"])


class FakeTabby:
    """Tabby's work-* IPC commands over the real WorkingStore."""

    def __init__(self, path):
        self.store = WorkingStore(path)

    def __call__(self, cmd):
        c, s = cmd["command"], self.store
        if c == "work-list":
            return {"ok": True, "tasks": s.list()}
        if c == "work-create":
            return {"ok": True, "task": s.create(cmd["title"], summary=cmd.get("summary", ""), status=cmd.get("status", "working"))}
        task = None
        if c == "work-update":
            task = s.update(cmd["task_id"], title=cmd.get("title"), status=cmd.get("status"), summary=cmd.get("summary"))
        elif c == "work-complete":
            task = s.complete(cmd["task_id"], summary=cmd.get("summary", ""))
        elif c == "work-reopen":
            task = s.reopen(cmd["task_id"])
        elif c == "work-delete-user":
            return {"ok": s.delete_user(cmd["task_id"])}
        return {"ok": True, "task": task} if task else {"ok": False, "error": "unknown working task"}

    def working(self):
        return [t for t in self.store.list() if t["status"] == "working"]  # what the HAG-13 chip counts


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_ledger(self.root, [
            {"id": "HAG-1", "title": "backlog card", "status": "todo", "assignee": "claude"},
            {"id": "HAG-2", "title": "old", "status": "done"},
        ])
        self.tabby = FakeTabby(self.root / "working.json")
        self.now = [1000.0]
        self.logs = []
        self.hive = HiveIO("local", str(self.root))
        self.mirror = Mirror(self.hive, self.tabby, self.root / "mirror.json", done_linger_s=600,
                             clock=lambda: self.now[0], log=self.logs.append)
        self.led = hive_task.Ledger(self.root, by="god")

    def tearDown(self):
        self.tmp.cleanup()

    def tasks(self):
        return self.tabby.store.list()

    def test_full_lifecycle(self):
        self.mirror.tick()
        self.assertEqual(self.tasks(), [])  # no backlog, no old done cards in Working
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        [t] = self.tasks()
        self.assertEqual((t["title"], t["status"], t["summary"]), ("HAG-1 · backlog card", "working", "claude"))
        self.assertEqual(len(self.tabby.working()), 1)
        # Idempotent: nothing changes on the next pass.
        stamp = t["updatedAt"]
        self.assertEqual(sum(self.mirror.tick().values()), 0)
        self.assertEqual(self.tasks()[0]["updatedAt"], stamp)
        # Blocked: shown with the reason, no longer counted as running.
        self.led.update("HAG-1", {"status": "blocked", "blocked": "needs Chrome"})
        self.mirror.tick()
        [t] = self.tasks()
        self.assertEqual((t["status"], t["summary"]), ("blocked", "claude · blocked: needs Chrome"))
        self.assertEqual(len(self.tabby.working()), 0)
        # Done: completed, then gone after the linger.
        self.led.update("HAG-1", {"status": "done", "result": "merged"})
        self.mirror.tick()
        [t] = self.tasks()
        self.assertEqual((t["status"], t["summary"]), ("done", "done · merged"))
        self.now[0] += 601
        self.mirror.tick()
        self.assertEqual(self.tasks(), [])
        self.assertEqual(json.loads((self.root / "mirror.json").read_text())["cards"], {})

    def test_back_to_todo_or_archived_removes(self):
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        self.led.update("HAG-1", {"status": "todo"})
        self.mirror.tick()
        self.assertEqual(self.tasks(), [])
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        self.led.delete("HAG-1")  # archived by the hive app's hygiene sweep
        self.mirror.tick()
        self.assertEqual(self.tasks(), [])

    def test_completing_in_tabby_completes_the_card(self):
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        [t] = self.tasks()
        self.tabby({"command": "work-complete", "task_id": t["id"], "summary": "finished via MCP"})
        self.mirror.tick()
        c = card(self.root, "HAG-1")
        self.assertEqual((c["status"], c["result"], c["updatedBy"]), ("done", "finished via MCP", "tabby-mirror"))
        mtime = (self.root / "tasks.json").stat().st_mtime_ns
        self.mirror.tick()
        self.assertEqual((self.root / "tasks.json").stat().st_mtime_ns, mtime)  # one write, not one per tick
        self.now[0] += 601
        self.mirror.tick()
        self.assertEqual(self.tasks(), [])

    def test_blocking_and_resuming_in_tabby(self):
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        tid = self.tasks()[0]["id"]
        self.tabby({"command": "work-update", "task_id": tid, "status": "blocked", "summary": "waiting for API key"})
        self.mirror.tick()
        c = card(self.root, "HAG-1")
        self.assertEqual((c["status"], c["blocked"]), ("blocked", "waiting for API key"))
        self.assertEqual(self.tasks()[0]["summary"], "claude · blocked: waiting for API key")
        self.tabby({"command": "work-update", "task_id": tid, "status": "working"})
        self.mirror.tick()
        c = card(self.root, "HAG-1")
        self.assertEqual(c["status"], "doing")
        self.assertNotIn("blocked", c)

    def test_stale_tabby_edit_never_overrides_newer_card(self):
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        tid = self.tasks()[0]["id"]
        self.led.update("HAG-1", {"status": "todo"})  # god moved it back meanwhile
        self.tabby({"command": "work-complete", "task_id": tid, "summary": "done?"})
        self.mirror.tick()
        self.assertEqual(card(self.root, "HAG-1")["status"], "todo")
        self.assertEqual(self.tasks(), [])
        self.assertTrue(any("ignored" in line for line in self.logs))

    def test_dismissed_by_hand_stays_hidden_until_card_moves(self):
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        self.tabby({"command": "work-delete-user", "task_id": self.tasks()[0]["id"]})
        self.mirror.tick()
        self.mirror.tick()
        self.assertEqual(self.tasks(), [])
        self.assertEqual(card(self.root, "HAG-1")["status"], "doing")  # the card is untouched
        self.led.update("HAG-1", {"status": "blocked", "blocked": "x"})
        self.mirror.tick()
        self.assertEqual(self.tasks()[0]["status"], "blocked")

    def test_lost_mapping_adopts_instead_of_duplicating(self):
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        (self.root / "mirror.json").unlink()
        self.mirror.tick()
        self.assertEqual(len(self.tasks()), 1)

    def test_reverse_write_failure_keeps_both_sides(self):
        self.led.update("HAG-1", {"status": "doing"})
        self.mirror.tick()
        tid = self.tasks()[0]["id"]
        self.tabby({"command": "work-complete", "task_id": tid, "summary": "done"})
        with patch.object(self.hive, "write", side_effect=RuntimeError("ssh down")):
            self.mirror.tick()
        self.assertEqual(self.tasks()[0]["status"], "done")  # not reopened by the forward pass
        self.mirror.tick()
        self.assertEqual(card(self.root, "HAG-1")["status"], "done")

    def test_view_uses_open_human_question_as_reason(self):
        v = view({"id": "HAG-5", "title": "t", "status": "blocked", "assignee": "x",
                  "humanQA": [{"q": "old", "a": "y"}, {"q": "Install  Chrome?"}]})
        self.assertEqual(v["summary"], "x · blocked: waiting on the human: Install Chrome?")


class ManualTaskBackendTests(unittest.TestCase):
    """Completing or removing a manual task must not wait on the browser."""

    def backend(self, path):
        from unittest.mock import MagicMock
        from backend import TabbyBackend
        b = TabbyBackend.__new__(TabbyBackend)
        b.working = WorkingStore(path)
        b.voice = MagicMock()
        b._working_idle_ticks, b._working_retry_at = {}, {}
        b._publish_working = lambda: None
        return b

    def test_manual_tasks_skip_worker_close_chat_tasks_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self.backend(Path(tmp) / "working.json")
            manual = b.working.create("HAG-1 · x")
            self.assertTrue(b.work_complete(manual["id"], "done")["ok"])
            self.assertTrue(b.work_delete_user(manual["id"])["ok"])
            b.voice.worker_close.assert_not_called()
            chat = b.working.pin("https://chatgpt.com/c/abc", "chat")
            b.work_complete(chat["id"])
            b.work_delete_user(chat["id"])
            self.assertEqual(b.voice.worker_close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
