import unittest
from unittest.mock import patch
from scripts import tabby_loom_sync as sync

class SyncRegressionTests(unittest.TestCase):
    @patch.object(sync, "send_command", return_value={"ok": True, "tasks": []})
    def test_removed_work_card_is_skipped_without_abort(self, sender):
        self.assertFalse(sync.complete_if_needed("missing", {"id": "mission", "outcome": "reviewed_verified_success"}))
        sender.assert_called_once()

    @patch.object(sync, "send_command", return_value={"ok": False, "error": "IPC timeout"})
    def test_failed_work_list_is_not_treated_as_deleted_cards(self, sender):
        with self.assertRaisesRegex(RuntimeError, "IPC timeout"):
            sync.complete_if_needed("missing", {"id": "mission"})

    @patch.object(sync, "send_command")
    def test_real_work_card_still_completes(self, sender):
        sender.side_effect = [
            {"ok": True, "tasks": [{"id": "live", "status": "working", "progress": .5}]},
            {"ok": True},
        ]
        self.assertTrue(sync.complete_if_needed("live", {"id": "mission", "outcome": "reviewed_verified_success"}))
        self.assertEqual(sender.call_args_list[1].args[0]["command"], "work-complete")

if __name__ == "__main__":
    unittest.main()
