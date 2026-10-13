import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const qml = readFileSync(new URL('../Panel.qml', import.meta.url), 'utf8');

test('organic blob replaces sad smiley artwork and idle panel stays small', () => {
  assert.match(qml, /id:\s*face\s*[\s\S]*?ctx\.bezierCurveTo\(/);
  assert.doesNotMatch(qml, /ctx\.moveTo\(18, 20\)/);
  assert.match(qml, /faceSlotVisible \? 78 : 60/);
  assert.match(qml, /faceSlotVisible \? 46 : 0/);
  assert.match(qml, /composerVisible \? 306/);
});

test('only two composer controls: plus opens file picker, send submits', () => {
  assert.match(qml, /FileDialog attachmentPicker/);
  assert.match(qml, /onAccepted: path => root\.command\("attach-file", path\)/);
  assert.match(qml, /id: filePlusTap; onTapped: root\.attachmentPicker\.open\(\)/);
  assert.match(qml, /id: sendTap; enabled: !root\.sendPending; onTapped: root\.submit\(\)/);
  assert.doesNotMatch(qml, /id: attachButton/);
  assert.match(qml, /Layout\.preferredWidth: 294/);
  assert.match(qml, /Qt\.Key_N/);
});
