import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
globalThis.JSWindowActorChild = class {};
const { QwqcHeyTabbyChild } = await import("../bridge/zen/actors/QwqcHeyTabbyChild.sys.mjs");
const panel = readFileSync(new URL("../Panel.qml", import.meta.url), "utf8");

test("ordinary Loom reply stays in state but is drawn only on hover or touch", () => {
  assert.match(panel, /hasAssistantReply: Array\.isArray\(T\.LoomState\.items\)/);
  assert.match(panel, /replyRevealed: hover\.hovered \|\| messageTouchPinned/);
  assert.match(panel, /boardRevealed: T\.LoomState\.whiteboardVisible &&/);
  assert.match(panel, /hasOtherBoardItems \|\| \(hasAssistantReply && replyRevealed\)/);
  assert.match(panel, /item\.source !== "assistant-reply" \|\| root\.replyRevealed/);
  assert.match(panel, /visible: root\.boardRevealed/);
});
test("Loom face has no suitcase shortcut, close X is subtle and hover-only", () => {
  assert.doesNotMatch(panel, /id: workPinButton|id: workPinHover|id: workPinTap/);
  assert.match(panel, /id: closeButton\s+visible: root\.hovered/);
  assert.match(panel, /: "transparent"/);
});
test("draft remains until sent-confirmed state and error stays actionable", () => {
  assert.match(panel, /if \(sendPending\) return/);
  assert.match(panel, /pendingDraft = value/);
  assert.match(panel, /status === "sent" && composerField\.text === root\.pendingDraft/);
  assert.doesNotMatch(panel, /function submit\(\): void \{\s*const value = composerField\.text;\s*composerField\.text = "";/);
  assert.match(panel, /textSendStatus === "unknown"/);
});
function mockActor(delivers) {
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  let userCount = 2;
  let lastText = "old";
  const composer = {};
  let clicks = 0;
  let draft = "";
  actor.waitForComposer = async () => composer;
  actor.setComposerText = (_composer, value) => { draft = value; };
  actor.userTurns = () => ({ count:userCount,lastText });
  actor.findSendButton = () => ({ disabled:false, getAttribute: () => null });
  actor.trustedClick = () => {
    clicks++;
    if (delivers) { userCount++; lastText = draft; draft = ""; }
    return true;
  };
  actor.waitForUi = async () => {};
  actor.publicState = () => ({});
  return {actor, get clicks() {return clicks;}, get draft() {return draft;}};
}
test("click on Send without a user turn is NOT reported as success and is not repeated", async () => {
  const o = mockActor(false);
  const result = await o.actor.sendText("draft survives", 2);
  assert.equal(result.ok, false);
  assert.equal(result.sent, "unknown");
  assert.equal(result.result, "send-unconfirmed");
  assert.equal(o.clicks, 1);
  assert.equal(o.draft, "draft survives");
});
test("new verified user turn confirms exactly one submission", async () => {
  const o = mockActor(true);
  const result = await o.actor.sendText("test message", 2);
  assert.equal(result.ok, true);
  assert.equal(result.result, "sent-confirmed");
  assert.equal(o.clicks, 1);
});
test("contenteditable uses actual document editing transaction", () => {
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  const events = [];
  class Input {}
  class Area {}
  class Event {constructor(type) {this.type=type;}}
  class InputEvent extends Event {}
  const composer = {textContent:"", focus(){}, dispatchEvent(e){events.push(e.type);}};
  let inserted = null;
  const selection = {removeAllRanges(){}, addRange(){}};
  actor.contentWindow = {HTMLTextAreaElement:Area, HTMLInputElement:Input,
    InputEvent, Event, getSelection:() => selection};
  actor.document = {
    createRange: () => ({selectNodeContents(node){assert.equal(node,composer);}}),
    execCommand: (cmd,_show,text) => { inserted=[cmd,text]; return true; },
  };
  actor.setComposerText(composer,"message");
  assert.deepEqual(inserted,["insertText","message"]);
  assert.deepEqual(events,["input","change"]);
});

test("hidden chat sends through the DOM button rather than screen-coordinate mouse input", async () => {
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  let submitted=0, coordinateClicks=0, userCount=4, lastText='prior request';
  actor.waitForComposer=async()=>({});
  actor.userTurns=()=>({count:userCount,lastText});
  actor.promptSubmissionState=()=>({promptAcknowledged:false});
  actor.setComposerText=()=>{};
  actor.findSendButton=()=>({disabled:false,getAttribute:()=>null,click(){
    submitted++;
    userCount++;
    lastText='Loom hidden message';
  }});
  actor.trustedClick=()=>{coordinateClicks++;return true;};
  actor.waitForUi=async()=>{};
  actor.publicState=()=>({});
  const result=await actor.sendText('Loom hidden message',10);
  assert.equal(result.result,'sent-confirmed');
  assert.equal(result.sent,true);
  assert.equal(submitted,1);
  assert.equal(coordinateClicks,0);
});
