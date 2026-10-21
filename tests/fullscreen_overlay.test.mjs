import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const qml = readFileSync(new URL("../Panel.qml", import.meta.url), "utf8");

function prop(name) {
  const value = qml.match(new RegExp(String.raw`^\s*readonly property bool ` + name + String.raw`:\s*(.+)$`, "m"))?.[1];
  assert.ok(value, "Expected fullscreen property " + name);
  return value;
}

const context = (fullscreen, state = {}) => ({
  panelFullscreen: fullscreen,
  T: { LoomState: { enabled: true, summoned: false, voiceActive: false,
    whiteboardVisible: false, inputArmed: false, ...state } },
  fullLoom: false, workingCount: 8, panelDashboardDocked: false,
  faceSlotVisible: true, workingListVisible: true, chipVisible: true,
  tasksCardVisible: true, notificationCount: 5,
});
const evaluate = (name, fullscreen, state) =>
  vm.runInNewContext(prop(name), context(fullscreen, state));

test("Loom never opts into a fullscreen overlay, even when summoned or voice-active", () => {
  assert.equal(evaluate("panelOverFullscreen", false), false);
  assert.equal(evaluate("panelOverFullscreen", true, { summoned: true, voiceActive: true }), false);
  assert.match(qml, /property bool panelFullscreen:\s*false/);
});

test("fullscreen hides the entire Loom panel including tasks, notifications, and whiteboards", () => {
  assert.equal(evaluate("panelVisible", true), false);
  assert.equal(evaluate("panelVisible", true, { summoned: true, voiceActive: true, whiteboardVisible: true }), false);
  assert.equal(evaluate("panelVisible", false), true);
});

test("fullscreen removes the status dots and disables the top-edge hit target", () => {
  assert.equal(evaluate("chipVisible", true), false);
  assert.equal(evaluate("panelInputEnabled", true), false);
  assert.equal(evaluate("chipVisible", false), true);
  assert.equal(evaluate("panelInputEnabled", false), true);
  assert.match(qml, /onPanelFullscreenChanged:[\s\S]*?chipExpanded = false;[\s\S]*?touchPinned = false;/);
});
