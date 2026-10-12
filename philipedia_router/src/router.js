import { RouterError } from "./errors.js";
import { fingerprintDigest, parseConversationUrl, validateFingerprint } from "./identity.js";
import { LIFECYCLES } from "./config.js";
import { Mutex } from "./mutex.js";

export class Router {
  constructor({ browser, projects }) {
    this.browser = browser;
    this.projects = projects;
    this.mutex = new Mutex();
  }

  status() {
    return this.mutex.run(async () => ({ ok: true, service: "philipedia-router", ...(await this.browser.status()) }));
  }

  originResolve(originMessages) {
    return this.mutex.run(async () => {
      const fingerprint = validateFingerprint(originMessages);
      const snapshots = await this.browser.conversationSnapshots();
      const matches = snapshots.filter(item => suffixEquals(item.messages, fingerprint));
      if (matches.length === 0) throw new RouterError("origin-not-found", "No conversation in the dedicated profile exactly matches the fingerprint", { fingerprint: fingerprintDigest(fingerprint) });
      if (matches.length !== 1) throw new RouterError("origin-ambiguous", "More than one conversation exactly matches the fingerprint", { candidates: matches.length, fingerprint: fingerprintDigest(fingerprint) });
      const verified = parseConversationUrl(matches[0].url);
      if (verified.conversationId !== matches[0].conversationId) throw new RouterError("origin-unverifiable", "Snapshot URL and conversation identity disagree");
      return { ok: true, result: "origin-resolved", conversationId: verified.conversationId, url: verified.canonicalUrl, fingerprint: fingerprintDigest(fingerprint) };
    });
  }

  route({ url, lifecycle }) {
    return this.mutex.run(async () => {
      if (!LIFECYCLES.includes(lifecycle)) throw new RouterError("invalid-lifecycle", `lifecycle must be one of: ${LIFECYCLES.join(", ")}`);
      const origin = parseConversationUrl(url);
      const resolved = [];
      for (const candidateLifecycle of LIFECYCLES) {
        const candidate = await this.browser.resolveProject(this.projects[candidateLifecycle]);
        if (!candidate?.id || !candidate?.segment) throw new RouterError("project-identity-unverifiable", `Project '${this.projects[candidateLifecycle]}' lacks a browser-verified identity`);
        resolved.push({ ...candidate, lifecycle: candidateLifecycle });
      }
      const ids = resolved.map(project => project.id);
      if (new Set(ids).size !== ids.length) throw new RouterError("duplicate-project-target", "Two lifecycle names resolved to the same stable project ID");
      const target = resolved.find(project => project.lifecycle === lifecycle);
      if (!target?.id || !target?.segment) throw new RouterError("project-identity-unverifiable", "Target project lacks a browser-verified identity");
      const result = await this.browser.moveConversation(origin.canonicalUrl, target);
      if (result.conversationId !== origin.conversationId || result.projectId !== target.id) throw new RouterError("route-verification-mismatch", "Browser result did not independently preserve both requested identities");
      return { ...result, lifecycle };
    });
  }
}

function suffixEquals(actual, expected) {
  if (actual.length < expected.length) return false;
  return expected.every((message, i) => actual[actual.length - expected.length + i] === message);
}
