import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
const source=readFileSync(new URL("../Panel.qml",import.meta.url),"utf8");

test("idle Loom tab keeps the 22px indicator and 48px independent touch target",()=>{
  assert.match(source,/counterStripHeight: chipVisible \? 30 : 0/);
  assert.match(source,/id: counterChip[\s\S]*?implicitHeight: 22/);
  assert.match(source,/id: counterTouchArea[\s\S]*?width: 60\s*height: 48/);
});

test("expanded drawer is offset by the compact chip strip",()=>{
  assert.match(source,/anchors.topMargin: root.chipVisible \? root.counterStripHeight : 0/);
});
