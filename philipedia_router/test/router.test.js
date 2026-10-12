import test from "node:test";
import assert from "node:assert/strict";
import { Router } from "../src/router.js";

const projects = { new: "New", working: "Working", review: "Review", blocked: "Blocked", vault: "Vault", done: "Done" };
const message = "This exact origin message is definitely long enough";

test("mock page snapshots resolve one exact suffix and reject absent or ambiguous origins", async () => {
  const browser = mockBrowser();
  browser.snapshots = [{ url: "https://chatgpt.com/c/conversation-one", conversationId: "conversation-one", messages: ["older", message] }];
  const router = new Router({ browser, projects });
  assert.equal((await router.originResolve([message])).conversationId, "conversation-one");
  browser.snapshots = [];
  await assert.rejects(router.originResolve([message]), error => error.code === "origin-not-found");
  browser.snapshots = [
    { url: "https://chatgpt.com/c/conversation-one", conversationId: "conversation-one", messages: [message] },
    { url: "https://chatgpt.com/c/conversation-two", conversationId: "conversation-two", messages: [message] },
  ];
  await assert.rejects(router.originResolve([message]), error => error.code === "origin-ambiguous");
});

test("route uses browser-resolved project identity and independently checks result", async () => {
  const browser = mockBrowser();
  const router = new Router({ browser, projects });
  const result = await router.route({ url: "https://chatgpt.com/c/conversation-one", lifecycle: "working" });
  assert.equal(result.result, "project-move-verified");
  assert.deepEqual(browser.calls.map(call => call[0]), ["resolve", "resolve", "resolve", "resolve", "resolve", "resolve", "move"]);
  assert.deepEqual(browser.calls.at(-1), ["move", "https://chatgpt.com/c/conversation-one", idFor("Working")]);
  browser.badIdentity = true;
  await assert.rejects(router.route({ url: "https://chatgpt.com/c/conversation-one", lifecycle: "working" }), error => error.code === "route-verification-mismatch");
});

test("operations are serialized", async () => {
  const browser = mockBrowser();
  let active = 0, peak = 0;
  browser.status = async () => { active++; peak = Math.max(peak, active); await new Promise(resolve => setTimeout(resolve, 10)); active--; return { loggedIn: true }; };
  const router = new Router({ browser, projects });
  await Promise.all([router.status(), router.status(), router.status()]);
  assert.equal(peak, 1);
});

function mockBrowser() {
  return {
    calls: [], snapshots: [], badIdentity: false,
    async status() { return { loggedIn: true }; },
    async conversationSnapshots() { return this.snapshots; },
    async resolveProject(name) {
      this.calls.push(["resolve", name]);
      const id = idFor(name);
      return { id, segment: `${id}-${name.toLowerCase()}`, name };
    },
    async moveConversation(url, target) {
      this.calls.push(["move", url, target.id]);
      return { ok: true, result: "project-move-verified", conversationId: this.badIdentity ? "wrong-conversation" : "conversation-one", projectId: target.id, projectName: target.name, url: `https://chatgpt.com/g/${target.segment}/c/conversation-one` };
    },
  };
}

function idFor(name) {
  const digit = String("New Working Review Blocked Vault Done".split(" ").indexOf(name) + 1);
  return `g-p-${digit.repeat(32)}`;
}
