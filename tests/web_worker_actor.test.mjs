import assert from "node:assert/strict";
import test from "node:test";

globalThis.JSWindowActorChild = class {};
const { QwqcHeyTabbyChild } = await import("../bridge/zen/actors/QwqcHeyTabbyChild.sys.mjs");

function element({ text = "", aria = "", href = "", testid = "" } = {}) {
  return {
    innerText: text, textContent: text, href,
    getAttribute(name) {
      return { "aria-label": aria, href, "data-testid": testid, "data-project-id": "" }[name] || "";
    },
  };
}

test("project choice matches a capitalised visible name without concatenated metadata", async () => {
  const opener = element({ aria: "Move to project", testid: "conversation-actions" });
  const choice = element({ text: "Working", aria: "Select Working project", testid: "project-choice" });
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
    if (target === choice) actor.contentWindow.location.href = "https://chatgpt.com/g/working-id/c/abc";
    return true;
  };
  const result = await actor.moveToProject("working-id", "Working");
  assert.equal(result.ok, true);
  assert.equal(result.projectId, "working-id");
  assert.equal(result.projectName, "Working");
});

test("project discovery uses the visible name and extracts the project id", () => {
  const link = element({ text: "Working", aria: "Working project", href: "https://chatgpt.com/g/working-id/project" });
  const actor = Object.create(QwqcHeyTabbyChild.prototype);
  actor.document = { querySelectorAll: () => [link] };
  actor.publicState = () => ({});
  assert.deepEqual(actor.discoverProjects().projects, [{ id: "working-id", name: "Working" }]);
});
