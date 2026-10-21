import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../Panel.qml', import.meta.url), 'utf8');

test('all Loom modes dock below an open dashboard, including summoned Voice', () => {
  assert.match(source, /panelDockToDashboard:\s*true/);
  assert.match(source, /panelDockStripHeight:\s*counterStripHeight/);
  assert.match(source, /property bool panelDashboardDocked:\s*false/);
  assert.match(source, /panelOverFullscreen:\s*false/);
});

test('dashboard summons the compact chip even with zero tasks and unfolds working tasks below', () => {
  assert.match(source, /chipVisible:\s*!panelFullscreen && !fullLoom && \(workingCount > 0 \|\| panelDashboardDocked\)/);
  assert.match(source, /idleWorkingExpanded:\s*chipVisible && hasWorking && \(chipExpanded \|\| panelDashboardDocked\)/);
  // The summoned full Loom view remains independent of the compact counter.
  assert.match(source, /fullLoom:\s*T\.LoomState\.summoned \|\|/);
  assert.match(source, /faceSlotVisible:\s*T\.LoomState\.summoned \|\| T\.LoomState\.voiceActive/);
});

test('compact capsule and original touch target move together with the sheet', () => {
  const chip = source.match(/id: counterChip([\s\S]*?)RowLayout/);
  const touch = source.match(/id: counterTouchArea([\s\S]*?)TapHandler/);
  assert.ok(chip && touch);
  for (const section of [chip[1], touch[1]]) {
    assert.match(section, /anchors\.top:\s*parent\.top/);
    assert.doesNotMatch(section, /anchors\.bottom:/);
  }
  assert.match(chip[1], /anchors\.topMargin:\s*-1/);
  assert.match(chip[1], /implicitHeight:\s*22/);
  assert.match(touch[1], /width:\s*60/);
  assert.match(touch[1], /height:\s*48/);
});

test('expanded cards stay beneath the fixed chip in both modes', () => {
  assert.match(source, /anchors\.topMargin:\s*root\.chipVisible \? root\.counterStripHeight : 0/);
  assert.match(source, /onPanelDashboardDockedChanged:/);
  assert.match(source, /chipExpanded = false/);
});
