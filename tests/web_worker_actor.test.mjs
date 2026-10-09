import assert from "node:assert/strict";
import test from "node:test";

globalThis.JSWindowActorChild = class {};
const { QwqcHeyTabbyChild } = await import("../bridge/zen/actors/QwqcHeyTabbyChild.sys.mjs");

function element({ text = "", aria = "", href = "", testid = "", role = "" } = {}) {
  return {
    innerText: text, textContent: text, href,
    getAttribute(name) {
      return { "aria-label": aria, href, "data-testid": testid, "data-project-id": "", role }[name] || "";
    },
  };
}

test("project choice matches a capitalised visible name without concatenated metadata", async () => {
  const opener = element({ aria: "Move to project", testid: "conversation-actions" });
  const choice = element({ text: "Working", aria: "Select Working project", testid: "project-choice", role: "menuitem" });
  let menuOpen = false;
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = {
    querySelectorAll(selector) {
      if (selector.includes("role=\"button\"")) return menuOpen ? [opener, choice] : [opener];
      return [];
    },
  };
  actor.contentWindow = {
    location: { href: "https://chatgpt.com/c/abc" },
    setTimeout(callback) { callback(); },
  };
  actor.visible = () => true;
  actor.publicState = () => ({});
  actor.trustedClick = target => {
    if (target === opener) menuOpen = true;
    if (target === choice) actor.contentWindow.location.href = "https://chatgpt.com/g/g-p-working-id/c/abc";
    return true;
  };
  const result = await actor.moveToProject("g-p-working-id", "Working");
  assert.equal(result.ok, true);
  assert.equal(result.projectId, "g-p-working-id");
  assert.equal(result.projectName, "Working");
});

test("project discovery uses the visible name and extracts the project id", () => {
  const link = element({ text: "Working", aria: "Working project", href: "https://chatgpt.com/g/g-p-working-id/project" });
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = { querySelectorAll: () => [link] };
  actor.publicState = () => ({});
  assert.deepEqual(actor.discoverProjects().projects, [{ id: "g-p-working-id", name: "Working" }]);
});


test("project discovery never mislabels a project with a chat title", () => {
  const chat = element({text:"Fix Loom Tracking Bug",
    href:"https://chatgpt.com/g/g-p-working-id/c/6ac936de-3ccc-83eb-a6e3-b7b15536fc14"});
  const project = element({text:"Working",
    href:"https://chatgpt.com/g/g-p-working-id/project"});
  const actor=Object.create(QwqcHeyTabbyChild.prototype);
  actor.document={querySelectorAll:()=>[chat,project]};
  actor.publicState=()=>({});
  assert.deepEqual(actor.discoverProjects().projects,[{id:"g-p-working-id",name:"Working"}]);
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
