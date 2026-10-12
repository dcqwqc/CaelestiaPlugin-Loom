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

test("a manually created chat routes using only a unique exact message, never a guessed tab", async () => {
  const browser = mockBrowser();
  browser.snapshots = [{url: "https://chatgpt.com/c/conversation-one", conversationId: "conversation-one", messages: [message]}];
  const router = new Router({ browser, projects });
  const routed = await router.route({ origin_messages: [message], lifecycle: "working" });
  assert.equal(routed.conversationId, "conversation-one");
  assert.equal(browser.calls.filter(call => call[0] === "move").length, 1);
  browser.snapshots = [
    {url: "https://chatgpt.com/c/conversation-one", conversationId: "conversation-one", messages: [message]},
    {url: "https://chatgpt.com/c/conversation-two", conversationId: "conversation-two", messages: [message]},
  ];
  await assert.rejects(router.route({origin_messages: [message], lifecycle: "blocked"}), error => error.code === "origin-ambiguous");
  assert.equal(browser.calls.filter(call => call[0] === "move").length, 1);
});

test("Done is never reachable without recorded Review, independent reviewer and live Review membership", async () => {
  const browser = mockBrowser();
  const router = new Router({ browser, projects });
  const url = "https://chatgpt.com/c/conversation-one";
  await assert.rejects(router.route({url, lifecycle: "done", reviewer: "reviewer", evidence: "checked"}), e => e.code === "review-required");
  assert.equal(browser.calls.filter(c => c[0] === "move").length, 0);
  await router.route({url, lifecycle: "working", created_by: "builder"});
  await router.route({url, lifecycle: "review"});
  await assert.rejects(router.route({url, lifecycle: "done"}), e => e.code === "review-evidence-required");
  await assert.rejects(router.route({url, lifecycle: "done", reviewer: "builder", evidence: "checked"}), e => e.code === "reviewer-not-independent");
  browser.currentProjectId = idFor("Working");
  await assert.rejects(router.route({url, lifecycle: "done", reviewer: "reviewer", evidence: "checked"}), e => e.code === "review-route-unverified");
  browser.currentProjectId = idFor("Review");
  const done = await router.route({url, lifecycle: "done", reviewer: "reviewer", evidence: "independently checked"});
  assert.equal(done.lifecycle, "done");
  assert.equal(done.task.reviewer, "reviewer");
  assert.equal(done.task.reviewEvidence, "independently checked");
});

function mockBrowser() {
  return {
    calls: [], snapshots: [], badIdentity: false, currentProjectId: "",
    async status() { return { loggedIn: true }; },
    async conversationSnapshots() { return this.snapshots; },
    async resolveProject(name) {
      this.calls.push(["resolve", name]);
      const id = idFor(name);
      return { id, segment: `${id}-${name.toLowerCase()}`, name };
    },
    async inspectConversation(url) { return { conversationId: "conversation-one", projectId: this.currentProjectId }; },
    async moveConversation(url, target) {
      this.calls.push(["move", url, target.id]);
      this.currentProjectId = target.id;
      return { ok: true, result: "project-move-verified", conversationId: this.badIdentity ? "wrong-conversation" : "conversation-one", projectId: target.id, projectName: target.name, url: `https://chatgpt.com/g/${target.segment}/c/conversation-one` };
    },
  };
}

function idFor(name) {
  const digit = String("New Working Review Blocked Vault Done".split(" ").indexOf(name) + 1);
  return `g-p-${digit.repeat(32)}`;
}
