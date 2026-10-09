import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class StableDeclarativeViewsTests(unittest.TestCase):
    def test_typed_views_use_stable_keyed_list_model(self):
        state = (ROOT / "services/LoomState.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()
        self.assertIn("property ListModel uiViewRows: ListModel", state)
        self.assertIn("uiViewRows.move(found, wanted, 1)", state)
        self.assertIn('uiViewRows.setProperty(wanted, "view", incoming)', state)
        self.assertIn("if (moduleSignature(previous) !== moduleSignature(incoming))", state)
        self.assertIn("model: T.LoomState.uiViewRows", panel)
        self.assertNotIn("model: Array.isArray(T.LoomState.uiViews)", panel)

    def test_read_only_poll_does_not_keep_whiteboard_open(self):
        backend = (ROOT / "backend.py").read_text()
        self.assertIn('command in {"ui-render", "ui-patch", "ui-undo", "ui-redo", "ui-event"}', backend)
