import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const qml = readFileSync(new URL('../Panel.qml', import.meta.url), 'utf8');
const counter = qml.match(/StyledRect\s*\{\s*id:\s*counterChip([\s\S]*?)\n\s*RowLayout\s*\{/);
assert.ok(counter, 'the idle counter pill must be present');

test('idle pill moves up within its unchanged native tab', () => {
  assert.match(counter[1], /anchors\.topMargin:\s*-1/);
  assert.match(counter[1], /implicitHeight:\s*22(?:\s|$)/);
  assert.match(qml, /counterStripHeight:\s*chipVisible\s*\?\s*30\s*:\s*0/);
});

test('the invisible touchscreen target retains its original dimensions', () => {
  const touch = qml.match(/id:\s*counterTouchArea([\s\S]*?)TapHandler/);
  assert.ok(touch);
  assert.match(touch[1], /width:\s*60/);
  assert.match(touch[1], /height:\s*48/);
});
