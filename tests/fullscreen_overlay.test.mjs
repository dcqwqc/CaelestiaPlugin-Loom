import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const qml = readFileSync(new URL('../Panel.qml', import.meta.url), 'utf8');
const expression = qml.match(/^\s*readonly property bool panelOverFullscreen:\s*(.+)$/m)?.[1];
assert.ok(expression, 'Loom fullscreen contract must be declared');

function overFullscreen(state = {}, composerVisible = false) {
  const defaults = { summoned: false, voiceActive: false, whiteboardVisible: false, inputArmed: false };
  return vm.runInNewContext(expression, { T: { LoomState: { ...defaults, ...state } }, composerVisible });
}

test('idle task counter and notifications opt into fullscreen overlay', () => {
  assert.equal(overFullscreen(), true);
  // Only a visible Loom panel activates the shared overlay surface.
  assert.match(qml, /panelVisible:.*chipVisible/);
});

test('explicit Loom voice, summon, whiteboard or armed text composer can overlay fullscreen', () => {
  assert.equal(overFullscreen({ summoned: true }), true);
  assert.equal(overFullscreen({ voiceActive: true }), true);
  assert.equal(overFullscreen({ whiteboardVisible: true }), true);
  assert.equal(overFullscreen({ inputArmed: true }, true), true);
  assert.equal(overFullscreen({ inputArmed: true }, false), true);
});
