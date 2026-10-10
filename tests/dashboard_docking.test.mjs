import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../Panel.qml', import.meta.url), 'utf8');

test('only the idle counter opts into native dashboard docking', () => {
  assert.match(source, /panelDockToDashboard:\s*chipVisible/);
  assert.match(source, /panelDockStripHeight:\s*counterStripHeight/);
  assert.match(source, /property bool panelDashboardDocked:\s*false/);
  assert.match(source, /panelOverFullscreen:\s*true/);
});

test('compact capsule and original touch target move together with the sheet', () => {
  const chip = source.match(/id: counterChip([\s\S]*?)RowLayout/);
  const touch = source.match(/id: counterTouchArea([\s\S]*?)TapHandler/);
  assert.ok(chip && touch);
  for (const section of [chip[1], touch[1]]) {
    assert.match(section, /anchors\.top:\s*root\.panelDashboardDocked \? undefined : parent\.top/);
    assert.match(section, /anchors\.bottom:\s*root\.panelDashboardDocked \? parent\.bottom : undefined/);
  }
  assert.match(chip[1], /anchors\.bottomMargin:\s*root\.panelDashboardDocked \? 9 : 0/);
  assert.match(chip[1], /implicitHeight:\s*22/);
  assert.match(touch[1], /width:\s*60/);
  assert.match(touch[1], /height:\s*48/);
});

test('expanded cards open above bottom dock but below normal top tab', () => {
  assert.match(source, /anchors\.topMargin:\s*root\.panelDashboardDocked \? 0 : \(root\.chipVisible \? root\.counterStripHeight : 0\)/);
  assert.match(source, /anchors\.bottomMargin:\s*root\.panelDashboardDocked \? \(root\.chipVisible \? root\.counterStripHeight : 0\) : 0/);
});
