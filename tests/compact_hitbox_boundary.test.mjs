import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
const q=readFileSync(new URL("../Panel.qml",import.meta.url),"utf8");
const hit=q.match(/panelHitY: chipVisible && !workingListVisible \? (\d+) : 0/);
const height=q.match(/panelHitHeight: chipVisible && !workingListVisible \? (\d+) : height/);
const fn=q.match(/function updateChipHover\(\): void \{([\s\S]*?)\n    \}/);
assert.ok(hit&&height&&fn);
test("hitbox occupies upper half of dots only",()=>{
 assert.equal(+hit[1],0); assert.equal(+height[1],12);
 assert.match(q,/panelHitWidth: chipVisible && !workingListVisible \? Math\.max\(32, counterChip\.implicitWidth - 4\) : width/);
 assert.match(q,/implicitHeight: 22/);
});
test("the oversized root hover never initiates task expansion",()=>{
 assert.match(fn[1],/!chipExpanded && chipVisible && hasWorking && !touchPinned &&\s*panelHostHovered/);
 assert.doesNotMatch(fn[1],/hover\.hovered|counterTargetHover\.hovered|taskDrawerHover\.hovered/);
 const state={chipExpanded:false,chipVisible:true,hasWorking:true,touchPinned:false,panelHostHovered:false,
  chipCollapseTimer:{stop(){}},checkChipDismissal(){}};
 const f=new Function("s","with(s){return function updateChipHover(){"+fn[1]+"\n};}")(state);
 for(const y of [12.1,20,34,55,100]){state.panelHostHovered=false;f(); assert.equal(state.chipExpanded,false,"outside y="+y);}
 state.panelHostHovered=true;f();assert.equal(state.chipExpanded,true);
});
test("preserve native window, full screen absence, center lock and click/tap",()=>{
 assert.match(q,/panelLiftShadow: panelVisible/);
 assert.match(q,/panelVisible: !panelFullscreen/);
 assert.match(q,/panelLockHorizontalCenter: true/);
 assert.match(q,/acceptedDevices: PointerDevice.TouchScreen \| PointerDevice.Mouse/);
});
