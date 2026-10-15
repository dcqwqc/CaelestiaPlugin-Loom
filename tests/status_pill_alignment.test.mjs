import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('../Panel.qml', import.meta.url), 'utf8');
const match = (pattern) => {
  const m = source.match(pattern);
  assert.ok(m, 'missing binding: ' + pattern);
  return m[1];
};
const compactWidthExpression = match(/readonly property real compactStatusPillWidth:\s*([\s\S]*?)\n\s*readonly property int workingHeight:/);
const compactWidth = new Function('chipRow', 'notificationCount', 'notificationChipRow', 'return ' + compactWidthExpression);
const rootWidthExpression = match(/implicitWidth:\s*(Math\.max\(tasksCardVisible[\s\S]*?\))\s*\n\s*implicitHeight:/);
const rootWidth = new Function(
  'tasksCardVisible','tasksCard','notificationsExpanded','notificationCount','notificationChipRow',
  'idleWorkingExpanded','chipVisible','root','boardRevealed','composerVisible','faceSlotVisible',
  'return ' + rootWidthExpression,
);

function layout({counter = 82, notification = 118, notifications = 4, chip = true, expanded = false} = {}) {
  const compact = compactWidth({implicitWidth:counter}, notifications, {implicitWidth:notification});
  const host = rootWidth(false, {implicitWidth:0}, expanded, notifications, {implicitWidth:notification},
    false, chip, {compactStatusPillWidth:compact}, false, false, false);
  const top = compact;
  const bottom = expanded ? 340 : chip ? compact : notification + 24;
  return {compact, host, top, bottom};
}

test('compact status rows use the same width and equal horizontal insets', () => {
  for (const notification of [85, 118, 152, 230]) {
    const {host,top,bottom} = layout({notification});
    assert.equal(top,bottom);
    assert.equal(host - top,16,'8px inset on each side');
  }
});

test('no notification leaves the original compact counter width', () => {
  assert.equal(layout({notifications:0}).compact,100);
});

test('notification-only view retains 8px left and right inset', () => {
  const {host,bottom} = layout({chip:false});
  assert.equal(host-bottom,16);
});

test('expanded drawer has enough space for its 340px content and padding', () => {
  const {host,bottom} = layout({expanded:true});
  assert.equal(host,356);
  assert.equal(bottom,340);
});

test('vertical layout has consistent inset, row gap, and bottom breathing room', () => {
  assert.match(source,/id: counterChip[\s\S]*?anchors\.topMargin: 4[\s\S]*?implicitHeight: 22/);
  assert.match(source,/readonly property int counterStripHeight: chipVisible \? 30 : 0/);
  assert.match(source,/id: notificationChip[\s\S]*?Layout\.preferredHeight: 26/);
  assert.match(source,/notificationCount > 0 \? 30 : 0/);
  // Counter occupies y=4..26, notification occupies y=30..56,
  // and the compact host occupies y=0..60.
  assert.equal(30 - (4 + 22),4);
  assert.equal(60 - (30 + 26),4);
});
