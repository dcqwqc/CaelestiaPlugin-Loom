import assert from "node:assert/strict";
import test from "node:test";

globalThis.JSWindowActorChild = class {};
const { QwqcHeyTabbyChild } = await import("../bridge/zen/actors/QwqcHeyTabbyChild.sys.mjs");

const NEW = "g-p-" + "1".repeat(32);
const WORKING = "g-p-" + "3".repeat(32);
const DONE = "g-p-" + "5".repeat(32);

function element({ text = "", aria = "", href = "", testid = "", role = "" } = {}) {
  return {
    innerText: text, textContent: text, href,
    getAttribute(name) {
      return { "aria-label": aria, href, "data-testid": testid, "data-project-id": "", role }[name] || "";
    },
  };
}

// A conversation page with a "Move to project" menu. Clicking a project
// choice changes the route the way ChatGPT does (unless `routeFollows` is off).
function pageActor({ href, projects = [], routeFollows = true, extraChoices = [] }) {
  const opener = element({ aria: "Move to project", testid: "conversation-actions" });
  const choices = projects.map(p => ({ el: element({ text: p.name, aria: `Select ${p.name}`, role: "menuitem" }), p }));
  let menuOpen = false;
  const clicks = [];
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = {
    querySelectorAll(selector) {
      if (!selector.includes("role=\"button\"")) return [];
      return menuOpen ? [opener, ...choices.map(c => c.el), ...extraChoices] : [opener];
    },
  };
  actor.contentWindow = { location: { href }, setTimeout(cb) { cb(); } };
  actor.visible = () => true;
  actor.publicState = () => ({ href: actor.contentWindow.location.href });
  actor.trustedClick = target => {
    clicks.push(target);
    if (target === opener) menuOpen = true;
    const hit = choices.find(c => c.el === target);
    if (hit && routeFollows) {
      const conv = QwqcHeyTabbyChild.parseChatRoute(actor.contentWindow.location.href).conversationId;
      actor.contentWindow.location.href = `https://chatgpt.com/g/${hit.p.segment}/c/${conv}`;
    }
    return true;
  };
  return { actor, clicks, opener };
}

test("project menu appearing after hydration is polled before failing", async () => {
  const { actor, opener, clicks } = pageActor({href:"https://chatgpt.com/c/delayed",
    projects:[{name:"Working",segment:WORKING}]});
  const query = actor.document.querySelectorAll.bind(actor.document);
  let attempts = 0;
  actor.document.querySelectorAll = selector => (++attempts < 5 ? [] : query(selector));
  const result = await actor.moveToProject(WORKING,"Working","delayed",0);
  assert.equal(result.ok,true);
  assert.ok(attempts >= 5);
  assert.equal(clicks[0],opener);
});

test("route switching during menu hydration aborts without clicks", async () => {
  const {actor,clicks} = pageActor({href:"https://chatgpt.com/c/expected",projects:[]});
  actor.document.querySelectorAll = () => [];
  actor.contentWindow.setTimeout = cb => {actor.contentWindow.location.href="https://chatgpt.com/c/other";cb();};
  const result = await actor.moveToProject(WORKING,"Working","expected",0);
  assert.equal(result.result,"conversation-changed-during-menu-wait");
  assert.equal(clicks.length,0);
});

test("project core id ignores the rename slug and route parsing is strict", () => {
  assert.equal(QwqcHeyTabbyChild.projectCoreId(`${WORKING}-working`), WORKING);
  assert.equal(QwqcHeyTabbyChild.projectCoreId(WORKING.toUpperCase().replace("G-P-", "g-p-")), WORKING);
  assert.equal(QwqcHeyTabbyChild.projectCoreId("legacy-id"), "legacy-id");
  assert.deepEqual(QwqcHeyTabbyChild.parseChatRoute(`https://chatgpt.com/g/${WORKING}-w/c/abc`),
    { conversationId: "abc", projectSegment: `${WORKING}-w`, projectId: WORKING });
  assert.deepEqual(QwqcHeyTabbyChild.parseChatRoute("https://chatgpt.com/c/abc"),
    { conversationId: "abc", projectSegment: "", projectId: "" });
  for (const bad of ["https://evil.example/c/abc", "https://chatgpt.com/", `https://chatgpt.com/g/${WORKING}/project`, "nope"])
    assert.equal(QwqcHeyTabbyChild.parseChatRoute(bad), null);
});

test("project discovery uses the first visible line, dedupes by core id and skips custom GPTs", () => {
  const links = [
    element({ text: "Working\n3 chats", href: `https://chatgpt.com/g/${WORKING}-working/project` }),
    element({ text: "Working", href: `https://chatgpt.com/g/${WORKING}-renamed/project` }),
    element({ text: "Code Copilot", href: "https://chatgpt.com/g/g-abc123-code-copilot/project" }),
    element({ text: "Done", href: `https://chatgpt.com/g/${DONE}/project` }),
  ];
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = { querySelectorAll: () => links };
  actor.publicState = () => ({});
  assert.deepEqual(actor.discoverProjects().projects, [
    { id: WORKING, segment: `${WORKING}-working`, name: "Working" },
    { id: DONE, segment: DONE, name: "Done" },
  ]);
});

test("legacy project ids still match a capitalised visible name", async () => {
  const { actor } = pageActor({ href: "https://chatgpt.com/c/abc",
    projects: [{ name: "Working", segment: "working-id" }] });
  const result = await actor.moveToProject("working-id", "Working");
  assert.equal(result.ok, true);
  assert.equal(result.projectId, "working-id");
});

test("move is verified only from the conversation route with the same conversation id", async () => {
  const { actor } = pageActor({ href: `https://chatgpt.com/g/${NEW}-new/c/conv-1`,
    projects: [{ name: "Working", segment: `${WORKING}-working` }] });
  const result = await actor.moveToProject(WORKING, "Working", "conv-1", 0);
  assert.equal(result.ok, true);
  assert.equal(result.result, "project-move-verified");
  assert.equal(result.projectId, WORKING);
  assert.equal(result.conversationId, "conv-1");
});

test("a click that does not change the route is reported unverified", async () => {
  const { actor } = pageActor({ href: `https://chatgpt.com/g/${NEW}/c/conv-1`, routeFollows: false,
    projects: [{ name: "Working", segment: WORKING }] });
  const result = await actor.moveToProject(WORKING, "Working", "conv-1", 0);
  assert.equal(result.ok, false);
  assert.equal(result.result, "project-move-unverified");
  assert.equal(result.projectId, "");
});

test("already filed conversations are verified without touching the menu", async () => {
  const { actor, clicks } = pageActor({ href: `https://chatgpt.com/g/${WORKING}-w/c/conv-1` });
  const result = await actor.moveToProject(WORKING, "Working", "conv-1");
  assert.equal(result.result, "already-in-project");
  assert.equal(clicks.length, 0);
});

test("wrong conversation and non-conversation pages are refused before any click", async () => {
  const other = pageActor({ href: `https://chatgpt.com/c/someone-else`, projects: [{ name: "Working", segment: WORKING }] });
  assert.equal((await other.actor.moveToProject(WORKING, "Working", "conv-1")).result, "conversation-mismatch");
  assert.equal(other.clicks.length, 0);
  const home = pageActor({ href: "https://chatgpt.com/", projects: [{ name: "Working", segment: WORKING }] });
  assert.equal((await home.actor.moveToProject(WORKING, "Working")).result, "not-a-conversation");
});

test("two menu entries with the requested name are refused as ambiguous", async () => {
  const { actor, clicks, opener } = pageActor({ href: "https://chatgpt.com/c/conv-1",
    projects: [{ name: "Working", segment: WORKING }],
    extraChoices: [element({ text: "Working", aria: "Select Working", role: "menuitem" })] });
  const result = await actor.moveToProject(WORKING, "Working", "conv-1", 0);
  assert.equal(result.result, "project-choice-ambiguous");
  assert.deepEqual(clicks, [opener]);
});

// sendPromptGuarded -----------------------------------------------------------

function sendActor({ href = `https://chatgpt.com/g/${WORKING}-w/c/conv-1`, users = 1, working = false,
  delivers = true, onFill = null } = {}) {
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  const log = { clicks: 0, filled: [] };
  const send = { disabled: false, getAttribute: () => "" };
  let count = users;
  actor.contentWindow = { location: { href }, setTimeout(cb) { cb(); } };
  actor.state = () => ({ working });
  actor.publicState = () => ({});
  actor.waitForComposer = async () => ({});
  actor.setComposerText = (_composer, text) => { log.filled.push(text); if (text && onFill) onFill(actor); };
  actor.findSendButton = () => send;
  actor.userTurns = () => ({ count, lastText: "" });
  actor.trustedClick = target => { if (target === send) { log.clicks++; if (delivers) count++; } return true; };
  return { actor, log };
}

const SEND = { conversationId: "conv-1", projectId: WORKING, text: "the real task", expectedUserCount: 1,
  idleTimeoutMs: 0, confirmTimeoutMs: 500 };

test("guarded send delivers once in the verified project", async () => {
  const { actor, log } = sendActor();
  const result = await actor.sendPromptGuarded(SEND);
  assert.equal(result.sent, true);
  assert.equal(result.result, "prompt-sent");
  assert.equal(log.clicks, 1);
  assert.deepEqual(log.filled, ["the real task"]);
});

test("guarded send refuses a conversation outside the verified project", async () => {
  for (const href of [`https://chatgpt.com/g/${NEW}/c/conv-1`, `https://chatgpt.com/g/${WORKING}/c/conv-2`,
    "https://chatgpt.com/c/conv-1"]) {
    const { actor, log } = sendActor({ href });
    const result = await actor.sendPromptGuarded(SEND);
    assert.equal(result.sent, false, href);
    assert.equal(result.result, "route-mismatch");
    assert.equal(log.clicks, 0);
  }
});

test("guarded send refuses when the chat already has more user turns than expected", async () => {
  const { actor, log } = sendActor({ users: 2 });
  const result = await actor.sendPromptGuarded(SEND);
  assert.equal(result.sent, false);
  assert.equal(result.result, "user-count-mismatch");
  assert.equal(log.clicks, 0);
});

test("guarded send waits for the bootstrap reply and refuses if it never settles", async () => {
  const { actor, log } = sendActor({ working: true });
  const result = await actor.sendPromptGuarded(SEND);
  assert.equal(result.result, "assistant-still-working");
  assert.equal(log.clicks, 0);
});

test("a navigation between filling and clicking aborts and clears the composer", async () => {
  const { actor, log } = sendActor({ onFill: a => { a.contentWindow.location.href = `https://chatgpt.com/g/${NEW}/c/conv-1`; } });
  const result = await actor.sendPromptGuarded(SEND);
  assert.equal(result.sent, false);
  assert.equal(result.result, "route-mismatch");
  assert.equal(log.clicks, 0);
  assert.deepEqual(log.filled, ["the real task", ""]);
});

test("a click without a visible new turn is reported as unknown, not as failure", async () => {
  const { actor, log } = sendActor({ delivers: false });
  const result = await actor.sendPromptGuarded({ ...SEND, confirmTimeoutMs: 1 });
  assert.equal(result.sent, "unknown");
  assert.equal(result.result, "prompt-send-unconfirmed");
  assert.equal(log.clicks, 1);
});

test("missing identity never sends", async () => {
  const { actor, log } = sendActor();
  for (const bad of [{ ...SEND, conversationId: "" }, { ...SEND, projectId: "" }, { ...SEND, text: "  " }])
    assert.equal((await actor.sendPromptGuarded(bad)).result, "missing-send-identity");
  assert.equal(log.clicks, 0);
});

test("user turns fall back to the screen-reader headings of the virtualised renderer", () => {
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = {
    querySelectorAll: () => [],
    body: { innerText: "You said:\nsetup\nChatGPT said:\nREADY\nYou said:\nthe real task\nChatGPT said:\nworking" },
  };
  assert.deepEqual(actor.userTurns(), { count: 2, lastText: "the real task" });
});

test("receiveMessage routes the new worker queries", async () => {
  const { actor } = sendActor();
  actor.conversationTurns = () => ({ ok: true, result: "conversation-turns" });
  assert.equal((await actor.receiveMessage({ name: "conversationTurns" })).result, "conversation-turns");
  assert.equal((await actor.receiveMessage({ name: "sendPromptGuarded", data: SEND })).result, "prompt-sent");
});

// Real sidebar markup (Mirai Zen, 2026-10-09): project rows are buttons with
// "New chat in <name>" / "Project actions for <name>" siblings and no links.
function sidebarActor(rows) {
  const clicks = [];
  const els = rows.flatMap(name => [
    element({ text: name }),
    element({ aria: `Project actions for ${name}` }),
    element({ aria: `New chat in ${name}` }),
  ]);
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = { querySelectorAll: () => els };
  actor.contentWindow = { location: { href: "https://chatgpt.com/?loom-worker=1" } };
  actor.visible = () => true;
  actor.publicState = () => ({});
  actor.trustedClick = el => { clicks.push(el); return true; };
  return { actor, clicks, els };
}

test("discovery lists link-less sidebar projects by name without inventing ids", () => {
  const { actor } = sidebarActor(["new", "working", "Docklys/Sumi etc"]);
  assert.deepEqual(actor.discoverProjects().projects, [
    { id: "", segment: "", name: "new" }, { id: "", segment: "", name: "working" },
    { id: "", segment: "", name: "Docklys/Sumi etc" }]);
});

test("openProject clicks the unique New-chat-in control, case-insensitively", () => {
  const { actor, clicks, els } = sidebarActor(["new", "working"]);
  const result = actor.openProject("Working");
  assert.equal(result.ok, true);
  assert.equal(result.via, "new-chat-control");
  assert.deepEqual(clicks, [els[5]]);
});

test("openProject refuses ambiguous names and unknown projects", () => {
  const dup = sidebarActor(["working", "Working"]);
  assert.equal(dup.actor.openProject("working").result, "project-control-ambiguous");
  assert.equal(dup.clicks.length, 0);
  const none = sidebarActor(["new"]);
  assert.equal(none.actor.openProject("vault").result, "project-control-not-found");
  assert.equal(none.actor.openProject("").result, "invalid-project-name");
});

test("openProject falls back to the row itself, never to its action menu", () => {
  const row = element({ text: "vault" });
  const actions = element({ aria: "Project actions for vault" });
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = { querySelectorAll: () => [actions, row] };
  actor.contentWindow = { location: { href: "https://chatgpt.com/" } };
  actor.visible = () => true;
  const clicks = [];
  actor.trustedClick = el => { clicks.push(el); return true; };
  const result = actor.openProject("vault");
  assert.equal(result.via, "sidebar-row");
  assert.deepEqual(clicks, [row]);
});

test("project discovery never mislabels a project with a chat title", () => {
  const chat = element({text:"Fix Loom Tracking Bug",
    href:"https://chatgpt.com/g/g-p-working-id/c/6ac936de-3ccc-83eb-a6e3-b7b15536fc14"});
  const project = element({text:"Working",
    href:"https://chatgpt.com/g/g-p-working-id/project"});
  const actor=Object.create(QwqcHeyTabbyChild.prototype);
  actor.document={querySelectorAll:()=>[chat,project]};
  actor.publicState=()=>({});
  assert.deepEqual(actor.discoverProjects().projects,[{id:"g-p-working-id",segment:"g-p-working-id",name:"Working"}]);
});

test("prompt acknowledgement requires a visible user turn, not a draft", () => {
  const actor=Object.create(QwqcHeyTabbyChild.prototype);
  const prompt="Reply with exactly hi and nothing else.";
  actor.publicState=()=>({});
  actor.document={
    body:{innerText:""},
    querySelectorAll:()=>[]
  };
  assert.equal(actor.promptSubmissionState(prompt).promptAcknowledged,false);
  actor.document.body.innerText="You said: "+prompt+"\nChatGPT said: hi";
  assert.equal(actor.promptSubmissionState(prompt).promptAcknowledged,true);
  actor.document.body.innerText="";
  actor.document.querySelectorAll=()=>[element({text:prompt})];
  assert.equal(actor.promptSubmissionState(prompt).promptAcknowledged,true);
  actor.document.querySelectorAll=()=>[element({text:"Different prompt"})];
  assert.equal(actor.promptSubmissionState(prompt).promptAcknowledged,false);
});

test("Voice button discovery accepts the new bare Voice label and semantic test-id", () => {
  const voice = element({ aria:"Voice", testid:"composer-voice-button" });
  voice.tagName = "BUTTON";
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = {
    location: { href:"https://chatgpt.com/c/abc" },
    title:"ChatGPT",
    body: { innerText:"" },
    querySelectorAll(selector) {
      if (selector.includes("button, [role=")) return [voice];
      return [];
    },
    querySelector() { return null; },
  };
  actor.visible = () => true;
  actor.findComposer = () => element();
  assert.equal(actor.state().ready,true);
  assert.equal(actor.state().active,false);
});

test("move opens the hidden Chat actions menu on this conversation's own sidebar row", async () => {
  const actions = element({ aria: "Chat actions" });
  const otherActions = element({ aria: "Chat actions" });
  const opener = element({ aria: "Move to project", role: "menuitem" });
  const choice = element({ text: "Working", aria: "Working", role: "menuitem" });
  const row = { querySelectorAll: sel => (sel.includes("Chat actions") ? [actions] : []) };
  const otherRow = { querySelectorAll: sel => (sel.includes("Chat actions") ? [otherActions] : []) };
  const link = { ...element({ href: "/c/conv-1" }), parentElement: row };
  const otherLink = { ...element({ href: "/c/conv-2" }), parentElement: otherRow };
  link.querySelectorAll = otherLink.querySelectorAll = () => [];
  actions.getBoundingClientRect = () => ({ left: 0, top: 0, width: 10, height: 10 });
  let menu = [];
  const clicks = [];
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = { querySelectorAll: sel => (sel.startsWith("a[") ? [otherLink, link] : menu) };
  actor.contentWindow = { location: { href: "https://chatgpt.com/c/conv-1" }, setTimeout(cb) { cb(); },
    windowUtils: { sendMouseEvent() {} } };
  actor.visible = () => true;
  actor.publicState = () => ({});
  actor.trustedClick = target => {
    clicks.push(target);
    if (target === actions) menu = [opener];
    if (target === opener) menu = [opener, choice];
    if (target === choice) actor.contentWindow.location.href = `https://chatgpt.com/g/${WORKING}/c/conv-1`;
    return true;
  };
  const result = await actor.moveToProject(WORKING, "Working", "conv-1", 0);
  assert.equal(result.result, "project-move-verified");
  assert.deepEqual(clicks, [actions, opener, choice]);
  assert.ok(!clicks.includes(otherActions), "must not open another chat's menu");
});


test("origin snapshot reads only this canonical chat's recent user turns",()=>{
  const a=Object.create(QwqcHeyTabbyChild.prototype);
  a.contentWindow={location:{href:"https://chatgpt.com/c/snapshot-test"}};
  a.document={querySelectorAll:()=>Array.from({length:5},(_,i)=>({innerText:`  User message ${i}: details of our routing problem.  `})),body:{innerText:""}};
  const origin=a.originSnapshot();
  assert.equal(origin.ok,true);
  assert.equal(origin.conversationId,"snapshot-test");
  assert.equal(origin.userMessages.length,3);
  assert.match(origin.userMessages[0],/User message 2/);
  a.contentWindow.location.href="https://chatgpt.com/g/g-p-xxx/project";
  assert.equal(a.originSnapshot().ok,false);
});


test("project sidebar native IDs match exact labels without page navigation",()=>{
  const a=Object.create(QwqcHeyTabbyChild.prototype);
  const projectId="g-p-"+"b".repeat(32);
  const row={getAttribute:(k)=>k==="data-app-action-sidebar-project-id"?projectId:
      k==="data-app-action-sidebar-project-label"?"Working":""};
  a.document={querySelectorAll:sel=>sel==="[data-app-action-sidebar-project-id]"?[row]:[]};
  a.contentWindow={location:{href:"https://chatgpt.com/c/check"}};
  a.publicState=()=>({});
  const found=a.discoverProjects();
  assert.equal(found.ok,true);
  assert.deepEqual(found.projects,[{id:projectId,segment:projectId,name:"Working",via:"sidebar-row-id"}]);
});

test("Radix pointerdown dispatches to exactly the supplied menu control",()=>{
  const actor=Object.create(QwqcHeyTabbyChild.prototype);
  let events=[];
  actor.contentWindow={PointerEvent:class {constructor(type,opts){this.type=type;this.opts=opts}}};
  const expected={getBoundingClientRect:()=>({left:10,top:12,width:20,height:20}),dispatchEvent:ev=>events.push(ev)};
  actor.trustedClick=()=>{throw Error('must not use pointer click for Radix')};
  assert.equal(actor.radixPointerDown(expected),true);
  assert.equal(events.length,1);
  assert.equal(events[0].type,'pointerdown');
  assert.equal(events[0].opts.pointerType,'mouse');
  assert.equal(events[0].opts.button,0);
});

test("Radix project menu item is activated once by exact-item click",()=>{
  const actor=Object.create(QwqcHeyTabbyChild.prototype);
  actor.contentWindow={PointerEvent:class {}};
  let clicks=0;
  const exact={click:()=>clicks++};
  actor.trustedClick=()=>{throw Error('must not click another item')};
  assert.equal(actor.selectRadixItem(exact),true);
  assert.equal(clicks,1);
});

test("Loom Voice startup never clicks a chat whose title contains voice and startup", async () => {
  const wrong = element({ text:"Fix Loom Voice Startup", aria:"Fix Loom Voice Startup", role:"button" });
  wrong.tagName = "BUTTON";
  const voice = element({ aria:"Start voice", role:"button" });
  voice.tagName = "BUTTON";
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = {
    location:{href:"https://chatgpt.com/g/g-p-loom/c/own-chat"},
    title:"Loom",
    body:{innerText:""},
    querySelectorAll(selector) {
      return selector.includes("button, [role=") ? [wrong,voice] : [];
    },
    querySelector() { return null; },
  };
  actor.visible=()=>true;
  actor.findComposer=()=>element();
  actor.contentWindow={setTimeout:cb=>cb()};
  actor.publicState=()=>({active:false});
  const clicks=[];
  actor.trustedClick=el=>{clicks.push(el);return true};
  assert.equal(actor.state().startControl,voice);
  const result=await actor.activateVoice();
  assert.equal(result.ok,true);
  assert.deepEqual(clicks,[voice]);
});

test("Loom Voice startup refuses to click a control covered by another element", () => {
  const el={
    contains:()=>false,
    getBoundingClientRect:()=>({left:12,top:30,width:50,height:30}),
  };
  const actor=Object.create(QwqcHeyTabbyChild.prototype);
  actor.document={elementFromPoint:()=>({})};
  actor.contentWindow={windowUtils:{sendMouseEvent(){throw Error("should not click")}}};
  assert.equal(actor.trustedClick(el),false);
});
