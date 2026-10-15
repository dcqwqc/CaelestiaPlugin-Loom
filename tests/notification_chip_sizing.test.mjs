import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const qml = readFileSync(new URL('../Panel.qml', import.meta.url), 'utf8');

const widthStart = qml.indexOf('implicitWidth: Math.max(tasksCardVisible');
const widthEnd = qml.indexOf('implicitHeight:', widthStart);
assert.ok(widthStart >= 0 && widthEnd > widthStart, 'host width binding exists');
const widthExpression = qml.slice(widthStart + 'implicitWidth:'.length, widthEnd).trim();
const hostWidth = new Function(
  'tasksCardVisible', 'tasksCard', 'notificationsExpanded', 'notificationCount',
  'notificationChipRow', 'idleWorkingExpanded', 'chipVisible', 'counterChip',
  'boardRevealed', 'composerVisible', 'faceSlotVisible',
  'return (' + widthExpression + ');',
);
const chipWidthBinding = qml.match(/Layout\.preferredWidth: (root\.notificationsExpanded \? 340 : notificationChipRow\.implicitWidth \+ 24)/g) || [];
const chipWidth = (expanded, rowWidth) => new Function(
  'root', 'notificationChipRow',
  'return (' + chipWidthBinding[0].replace('Layout.preferredWidth: ', '') + ');',
)({notificationsExpanded: expanded}, {implicitWidth: rowWidth});

function host(overrides = {}) {
  const state = {
    tasksCardVisible: false, tasksCard: {implicitWidth: 0},
    notificationsExpanded: false, notificationCount: 4,
    notificationChipRow: {implicitWidth: 112},
    idleWorkingExpanded: false, chipVisible: true,
    counterChip: {implicitWidth: 140},
    boardRevealed: false, composerVisible: false, faceSlotVisible: false,
    ...overrides,
  };
  return hostWidth(...[
    'tasksCardVisible', 'tasksCard', 'notificationsExpanded', 'notificationCount',
    'notificationChipRow', 'idleWorkingExpanded', 'chipVisible', 'counterChip',
    'boardRevealed', 'composerVisible', 'faceSlotVisible',
  ].map(k => state[k]));
}

test('compact notification chip follows the actual text width instead of 94px', () => {
  assert.equal(chipWidthBinding.length, 1, 'exactly one notification pill width binding');
  assert.match(qml, /id: notificationChipRow/);
  assert.match(qml, /text: String\(root.notificationCount\) \+ " needs attention"/);
  assert.equal(chipWidth(false, 112), 136);
  assert.equal(chipWidth(true, 112), 340);
  assert.ok(host() >= 136 + 16, 'host contains chip with breathing room');
});

test('counter and notification widths are both respected while docked', () => {
  const actual = host({notificationChipRow: {implicitWidth: 135}, counterChip: {implicitWidth: 158}});
  assert.ok(actual >= 135 + 40);
  assert.ok(actual >= 158 + 16);
});

test('expanded notifications retain the 340px panel, including the outer margin', () => {
  assert.ok(host({notificationsExpanded: true, chipVisible: false}) >= 348);
});

test('larger counts cannot overflow the compact host', () => {
  for (const rowWidth of [95, 112, 145, 220]) {
    assert.ok(host({notificationChipRow: {implicitWidth: rowWidth}, chipVisible: false}) >= rowWidth + 40);
  }
});

test('no notifications keeps the counter-sized host', () => {
  assert.equal(host({notificationCount: 0}), 156);
});
