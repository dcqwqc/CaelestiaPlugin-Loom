import test from "node:test";
import assert from "node:assert/strict";
import { parseConversationUrl, projectCoreId, validateFingerprint } from "../src/identity.js";

const PROJECT = "g-p-0123456789abcdef0123456789abcdef-working";

test("accepts only exact canonical conversation routes", () => {
  assert.deepEqual(parseConversationUrl("https://chatgpt.com/c/abcDEF_123"), {
    conversationId: "abcDEF_123", projectId: "", canonicalUrl: "https://chatgpt.com/c/abcDEF_123",
  });
  assert.equal(parseConversationUrl(`https://chatgpt.com/g/${PROJECT}/c/conversation-99`).projectId, "g-p-0123456789abcdef0123456789abcdef");
  for (const url of [
    "http://chatgpt.com/c/abcDEF_123", "https://evil.test/c/abcDEF_123",
    "https://chatgpt.com/c/abcDEF_123?x=1", "https://chatgpt.com/g/g-custom/c/abcDEF_123",
  ]) assert.throws(() => parseConversationUrl(url));
});

test("project core ID never guesses custom GPT identity", () => {
  assert.equal(projectCoreId(PROJECT), "g-p-0123456789abcdef0123456789abcdef");
  assert.equal(projectCoreId("g-abc"), "");
});

test("fingerprints require substantial exact context", () => {
  assert.deepEqual(validateFingerprint(["  A sufficiently long exact user message for matching  "]), ["A sufficiently long exact user message for matching"]);
  assert.throws(() => validateFingerprint(["too short"]), /Fingerprint needs/);
  assert.throws(() => validateFingerprint(new Array(4).fill("a sufficiently long exact message here")));
});
