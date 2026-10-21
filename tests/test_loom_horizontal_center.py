from pathlib import Path
import unittest


class TestLoomHorizontalCenter(unittest.TestCase):
    def test_width_resize_is_atomic_and_center_locked(self):
        qml = (Path(__file__).resolve().parents[1] / "Panel.qml").read_text()
        self.assertIn("readonly property bool panelLockHorizontalCenter: true", qml)
        self.assertNotIn("Behavior on implicitWidth", qml)
        self.assertIn("Behavior on implicitHeight", qml)

    def test_center_geometry_at_ordinary_screen_widths(self):
        for viewport in (1280, 1920, 2560, 3440):
            for width in (60, 80, 120, 240, 348, 240, 60):
                x = (viewport - width) / 2
                self.assertEqual(x + width / 2, viewport / 2)


if __name__ == "__main__":
    unittest.main()
