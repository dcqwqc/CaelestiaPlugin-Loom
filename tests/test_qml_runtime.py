"""An offscreen real-Qt sanity test of the keyed QML view/child model contract.

This does not claim a running Quickshell or Wayland visual interaction test.
"""
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "loom_model_runtime.qml"
_SCRIPT = r'''
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlComponent
from PySide6.QtCore import QUrl
app = QGuiApplication([])
engine = QQmlEngine()
qml = Path(__import__('sys').argv[1]).read_text().replace('Qt.quit();', '')
component = QQmlComponent(engine)
component.setData(qml.encode(), QUrl.fromLocalFile(__import__('sys').argv[1]))
if component.errors():
    raise RuntimeError(str(component.errors()))
root = component.create()
assert root is not None, 'Qt could not create the model fixture'
assert root.property('passed'), root.property('errorText')
assert root.property('creations') == 1, 'parent delegate replaced'
assert root.property('childCreations') == 2, 'child delegates replaced'
'''


class QtModelRuntimeTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 not installed")
    def test_sibling_updates_preserve_native_qt_delegates(self):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, "-c", _SCRIPT, str(FIXTURE)],
                                capture_output=True, text=True, env=env, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
