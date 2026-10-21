import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
const s=readFileSync(new URL('../Panel.qml',import.meta.url),'utf8');
const block=s.match(/function updateChipHover\(\): void \{([\s\S]*?)\n    \}/);
assert.ok(block);
test('narrow native hover target leaves physical screen edge free',()=>{
  assert.match(s,/panelHitY: chipVisible && !workingListVisible \? 0 : 0/);
  assert.match(s,/panelHitHeight: chipVisible && !workingListVisible \? 12 : height/);
  assert.match(s,/panelHitWidth: chipVisible && !workingListVisible \? Math\.max\(32, counterChip\.implicitWidth - 4\) : width/);
  assert.match(s,/implicitHeight: 22/);
});
test('pointer in compact pill opens task drawer, click/touch still works',()=>{
  assert.match(block[1],/chipVisible && hasWorking && !touchPinned/);
  assert.match(block[1],/chipExpanded = true/);
  assert.match(s,/id: counterTargetHover[\s\S]*?onHoveredChanged: root.updateChipHover\(\)/);
  assert.match(s,/id: hover[\s\S]*?onHoveredChanged: root.updateChipHover\(\)/);
  assert.match(s,/acceptedDevices: PointerDevice.TouchScreen \| PointerDevice.Mouse/);
});
test('mouseleave collapses with grace and horizontal anchor never drifts',()=>{
  assert.match(block[1],/checkChipDismissal\(\)/);
  assert.match(s,/id: chipCollapseTimer[\s\S]*?interval: 140/);
  assert.match(s,/panelLockHorizontalCenter: true/);
  assert.match(s,/panelVisible: !panelFullscreen/);
});
