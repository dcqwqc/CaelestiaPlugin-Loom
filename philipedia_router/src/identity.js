import { createHash } from "node:crypto";
import { RouterError } from "./errors.js";

const CONVERSATION = /^[0-9a-zA-Z_-]{8,128}$/;
const PROJECT = /^(g-p-[0-9a-f]{32})(?:-|$)/i;

export function projectCoreId(value) {
  const match = String(value ?? "").trim().match(PROJECT);
  return match?.[1]?.toLowerCase() ?? "";
}

export function parseConversationUrl(value) {
  let url;
  try { url = new URL(String(value ?? "")); } catch { throw new RouterError("invalid-conversation-url", "A canonical ChatGPT conversation URL is required"); }
  if (url.origin !== "https://chatgpt.com" || url.username || url.password || url.search || url.hash) {
    throw new RouterError("invalid-conversation-url", "Conversation URL must use canonical https://chatgpt.com form without query or fragment");
  }
  const parts = url.pathname.split("/").filter(Boolean);
  let conversationId = "";
  let projectId = "";
  if (parts.length === 2 && parts[0] === "c") conversationId = parts[1];
  else if (parts.length === 4 && parts[0] === "g" && parts[2] === "c") {
    projectId = projectCoreId(parts[1]);
    conversationId = parts[3];
    if (!projectId) throw new RouterError("invalid-conversation-url", "Project conversation URL has an invalid project identity");
  }
  if (!CONVERSATION.test(conversationId)) throw new RouterError("invalid-conversation-url", "URL is not a canonical ChatGPT conversation route");
  return { conversationId, projectId, canonicalUrl: projectId
    ? `https://chatgpt.com/g/${parts[1]}/c/${conversationId}`
    : `https://chatgpt.com/c/${conversationId}` };
}

export function normalizeMessage(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

export function validateFingerprint(messages) {
  if (!Array.isArray(messages) || messages.length < 1 || messages.length > 3) {
    throw new RouterError("invalid-fingerprint", "origin_messages must contain 1 to 3 exact recent user messages");
  }
  const normalized = messages.map(normalizeMessage);
  if (normalized.some(message => !message || message.length > 20_000)) {
    throw new RouterError("invalid-fingerprint", "Fingerprint messages must be non-empty and at most 20,000 characters each");
  }
  if (!normalized.some(message => message.length >= 24) || normalized.join("").length < 32) {
    throw new RouterError("weak-fingerprint", "Fingerprint needs one message of at least 24 characters and 32 characters total");
  }
  return normalized;
}

export function fingerprintDigest(messages) {
  return createHash("sha256").update(JSON.stringify(messages)).digest("hex");
}
