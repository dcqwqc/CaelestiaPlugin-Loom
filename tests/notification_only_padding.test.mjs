import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('../Panel.qml', import.meta.url), 'utf8');

function section(text, start, end) {
  const begin = text.indexOf(start);
  assert.ok(begin >= 0, 'missing ' + start);
  const finish = text.indexOf(end, begin);
  assert.ok(finish > begin, 'missing ' + end);
  return text.slice(begin, finish);
}

const counter = section(source, 'id: counterChip', 'id: chipRow');
const notification = section(source, 'id: notificationChip', 'id: notificationChipRow');
const host = section(source, 'readonly property int counterStripHeight:', 'Behavior on implicitWidth');
const marginExpr = (name) => {
  const line = notification.split(String.fromCharCode(10)).find(line => line.trim().startsWith('Layout.' + name + ':'));
  assert.ok(line, 'missing ' + name);
  return new Function('root', 'return ' + line.slice(line.indexOf(':') + 1).trim());
};
const topMargin = marginExpr('topMargin');
const bottomMargin = marginExpr('bottomMargin');

test('task counter has the exact original geometry and independent touch target', () => {
  assert.match(counter, /anchors\.topMargin:\s*-1/);
  assert.match(counter, /implicitWidth:\s*chipRow\.implicitWidth \+ 18/);
  assert.match(counter, /implicitHeight:\s*22/);
  assert.doesNotMatch(source, /compactStatusPillWidth/);
  assert.match(source, /id: counterTouchArea[\s\S]*?width: 60\s*height: 48/);
});

test('only compact notification shifts up 5px without moving following content', () => {
  assert.equal(topMargin({chipVisible: true, notificationsExpanded: false}), -5);
  assert.equal(bottomMargin({chipVisible: true, notificationsExpanded: false}), 5);
  assert.equal(topMargin({chipVisible: false, notificationsExpanded: false}), 0);
  assert.equal(bottomMargin({chipVisible: false, notificationsExpanded: false}), 0);
  assert.equal(topMargin({chipVisible: true, notificationsExpanded: true}), 0);
  assert.equal(bottomMargin({chipVisible: true, notificationsExpanded: true}), 0);
  assert.equal(topMargin({chipVisible: true, notificationsExpanded: false}) +
    bottomMargin({chipVisible: true, notificationsExpanded: false}), 0);
});

test('notification retains content-derived width and centered label', () => {
  assert.match(notification, /Layout\.preferredWidth: root\.notificationsExpanded \? 340 : notificationChipRow\.implicitWidth \+ 24/);
  assert.match(notification, /Layout\.preferredHeight: 26/);
  assert.match(notification, /Layout\.alignment: Qt\.AlignHCenter/);
  assert.match(source, /id: notificationChipRow[\s\S]*?anchors\.centerIn: parent/);
  assert.match(host, /notificationCount > 0 \? notificationChipRow\.implicitWidth \+ 40 : 0/);
});

test('compact notification moves up into the shell with balanced bottom inset', () => {
  assert.match(host, /counterStripHeight: chipVisible \? 30 : 0/);
  assert.match(host, /notificationCount > 0 \? \(chipVisible && !notificationsExpanded \? 27 : 30\) : 0/);
  const counterBottom = -1 + 22;
  const notificationTop = 30 + topMargin({chipVisible: true, notificationsExpanded: false});
  const notificationBottom = notificationTop + 26;
  const shellBottom = 30 + 27;
  assert.equal(notificationTop - counterBottom, 4);
  assert.equal(shellBottom - notificationBottom, 6);
  assert.equal(notificationTop, 25);
  assert.equal(shellBottom, 57);
});

test('expanded notification panel keeps its previous width and height allowance', () => {
  assert.match(host, /notificationsExpanded \? 348 : 0/);
  assert.match(source, /id: notificationPanel[\s\S]*?Layout\.preferredWidth: 340/);
  assert.equal(topMargin({chipVisible: true, notificationsExpanded: true}), 0);
});
