import unittest
from unittest.mock import patch

from tabby.zen import ZenClient


class WorkerGridTests(unittest.TestCase):
    AREA = (72, 22, 1826, 1156)

    def test_fills_the_area_row_major_three_across(self):
        for n in range(1, 13):
            rects = ZenClient.worker_grid(n, self.AREA)
            self.assertEqual(len(rects), n)
            # every row reaches the right edge and the last row reaches the bottom
            right = self.AREA[0] + self.AREA[2]
            rows = {}
            for x, y, w, h in rects:
                rows.setdefault(y, []).append((x, w))
            for cells in rows.values():
                self.assertAlmostEqual(max(x + w for x, w in cells), right, delta=1)
                self.assertEqual(min(x for x, _ in cells), self.AREA[0])
            self.assertAlmostEqual(max(y + h for _, y, _, h in rects), self.AREA[1] + self.AREA[3], delta=1)
        self.assertEqual([r[1] for r in ZenClient.worker_grid(3, self.AREA)], [22, 22, 22])  # one row of three
        self.assertEqual(len({r[1] for r in ZenClient.worker_grid(4, self.AREA)}), 2)
        self.assertEqual(ZenClient.worker_grid(0, self.AREA), [])

    def test_area_skips_headless_ai_outputs_and_respects_reserved(self):
        z = ZenClient.__new__(ZenClient)
        mons = [{"id": 2, "name": "AI-claude-1", "width": 1920, "height": 1080, "scale": 2, "x": 2880, "y": 0},
                {"id": 0, "name": "eDP-1", "width": 2880, "height": 1800, "scale": 1.5, "x": 0, "y": 0,
                 "focused": True, "reserved": [60, 10, 10, 10]}]
        self.assertEqual(z._worker_area(mons, monitor_id=2), (72, 22, 1826, 1156))

    def test_layout_is_idempotent_within_rounding(self):
        z = ZenClient.__new__(ZenClient)
        z._worker_order = []
        z._hypr_env = lambda: {}
        area = (72, 22, 1826, 1156)
        want = ZenClient.worker_grid(2, area)
        clients = [{"address": f"0x{i}", "monitor": 0, "workspace": {"name": "special:tabby-work"},
                    "at": [want[i][0], want[i][1] + 1], "size": [want[i][2], want[i][3]]} for i in range(2)]
        z._worker_clients = lambda task_id=None: clients
        z._worker_area = lambda mons, mid=None: area
        with patch("tabby.zen.subprocess.run") as run:
            run.return_value.stdout = "[]"
            self.assertEqual(z.layout_workers(), 0)
            clients[1]["at"] = [500, 500]
            self.assertEqual(z.layout_workers(), 1)


if __name__ == "__main__":
    unittest.main()
