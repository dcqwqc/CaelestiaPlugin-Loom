import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tabby import orbit_delegate
import loom_mcp


class OrbitDelegateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (orbit_delegate.FLEET_ROOT / "runtime/fleet_mcp.py").exists():
            raise unittest.SkipTest("host Fleet Gateway missing")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "allowed.py").write_text("x=1\n")
        # Fleet admission snapshots project instruction files; no git needed.
        self.config = self.root / "policy.json"
        self.config.write_text(json.dumps({
            "database": str(self.root / "supervisor.db"),
            "allowed_origins": [orbit_delegate.ORIGIN],
            "repositories": {
                "fixture": {
                    "path": str(repo),
                    "providers": ["codex"],
                    "allowed_paths": ["allowed.py"],
                    "validation_profiles": {"check": {"syntax": ["/usr/bin/python3", "-m", "py_compile", "allowed.py"]}},
                    "max_timeout_seconds": 300
                }
            },
            "max_jobs": 5, "max_depth": 1
        }))
        self.args = {"request_id": "loom:fix:test001", "title": "Fix bounded demo",
                     "goal": "Change one allowed fixture file and test",
                     "repository": "fixture", "provider": "codex",
                     "paths": ["allowed.py"], "validation_profile": "check",
                     "checks": ["syntax"]}

    def _call(self, action, args):
        return orbit_delegate.host_call(action, args, config_path=self.config)

    def test_real_fleet_admits_once_and_status_read_only(self):
        first = self._call("delegate", self.args)
        self.assertEqual(first["state"], "queued",first)
        self.assertTrue(first["job_id"])
        self.assertFalse(first["verified_done"])
        repeated = self._call("delegate", self.args)
        self.assertEqual(first["job_id"], repeated["job_id"])
        self.assertTrue(repeated["reused"])
        status = self._call("status", {"request_id": self.args["request_id"]})
        self.assertEqual(status["job_id"], first["job_id"])
        self.assertFalse(status["continuing"])
        import sqlite3
        with sqlite3.connect("file:" + str(self.root / "supervisor.db") + "?mode=ro", uri=True) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM fleet_jobs").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)

    def test_same_key_different_goal_denied_without_second_job(self):
        first = self._call("delegate", self.args)
        changed = dict(self.args, goal="A different task")
        second = self._call("delegate", changed)
        self.assertEqual(second["state"], "blocked")
        self.assertEqual(first["job_id"], self._call("status", {"request_id": self.args["request_id"]})["job_id"])

    def test_cannot_escalate_repository_or_path(self):
        a = dict(self.args, repository="forbidden")
        self.assertEqual(self._call("delegate", a)["state"], "blocked")
        a = dict(self.args, paths=["../../etc/passwd"])
        self.assertEqual(self._call("delegate", a)["state"], "blocked")
        self.assertEqual(self._call("status", {"request_id": self.args["request_id"]})["state"], "not_found")

    def test_read_only_status_missing(self):
        self.assertEqual(self._call("status", {"request_id": self.args["request_id"]})["state"], "not_found")

    def test_status_is_read_only_even_if_database_missing(self):
        import sqlite3
        cfg=json.loads(self.config.read_text())
        db=Path(cfg["database"])
        self.assertFalse(db.exists())
        result=self._call("status",{"request_id":self.args["request_id"]})
        self.assertEqual(result["state"],"not_found")
        self.assertFalse(db.exists())

    def test_remote_call_fixed_ssh_command(self):
        saved = {}
        class Done:
            returncode = 0
            stdout = '{"state":"queued","job_id":"job123"}'
        def runner(argv, **kwargs):
            saved["argv"] = argv
            saved["input"] = kwargs["input"]
            return Done()
        r = orbit_delegate.remote_call("delegate", self.args, runner=runner)
        self.assertEqual(r["job_id"], "job123")
        self.assertEqual(saved["argv"][-2:], ["philipedia", orbit_delegate.BRIDGE_CMD])
        self.assertNotIn(self.args["goal"], " ".join(saved["argv"]))

    def test_concurrent_same_key_returns_one_fleet_job(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:self._call("delegate",self.args),range(12)))
        ids={x.get("job_id") for x in results}
        self.assertEqual(len(ids),1,results)
        self.assertEqual({x["state"] for x in results},{"queued"})

    def test_unicode_bridge_long_goal_and_explicit_blocked_error(self):
        args=dict(self.args, goal="日本語" * 4000)
        class Done:
            returncode=0
            stdout='{"state":"queued","job_id":"existing"}'
        response=orbit_delegate.remote_call("delegate",args,runner=lambda *a,**k:Done())
        self.assertEqual(response["job_id"],"existing")
        class Denied:
            returncode=1
            stdout='{"state":"blocked","reason":"host scope denied"}'
        response=orbit_delegate.remote_call("delegate",args,runner=lambda *a,**k:Denied())
        self.assertEqual(response["state"],"blocked")
        self.assertIn("host scope",response["reason"])

    def test_timeout_refuses_blind_redispatch(self):
        result=orbit_delegate.remote_call("delegate",self.args,
               runner=lambda *a,**k: (_ for _ in ()).throw(
                   subprocess.TimeoutExpired("ssh",20)))
        self.assertEqual(result["state"],"uncertain_outcome")

    def test_oversized_checks_denied(self):
        args=dict(self.args,checks=["unit"]*31)
        self.assertEqual(orbit_delegate.remote_call("delegate",args)["state"],"denied")

    def test_projection_invalid_result_never_claims_done(self):
        response=orbit_delegate._projection({"state":"done","id":"x","result":"invalid",
                                             "revision":2},"example:key",True)
        self.assertFalse(response["verified_done"])

    def test_tool_in_loom_mcp(self):
        names = [tool["name"] for tool in loom_mcp.tool_list()]
        self.assertIn("loom_orbit_delegate", names)
        self.assertIn("loom_orbit_status", names)

    def test_remote_offline_no_false_admission(self):
        class Offline:
            returncode = 255
            stdout = ""
        r = orbit_delegate.remote_call("delegate", self.args, runner=lambda *a, **k: Offline())
        self.assertEqual(r["state"], "device_unavailable")


if __name__ == "__main__":
    unittest.main()
