import { RouterError } from "./errors.js";
import { fingerprintDigest, parseConversationUrl, validateFingerprint } from "./identity.js";
import { LIFECYCLES } from "./config.js";
import { Mutex } from "./mutex.js";
import { MemoryLedger } from "./ledger.js";

export class Router {
  constructor({ browser, projects, ledger = new MemoryLedger() }) {
    this.browser = browser;
    this.projects = projects;
    this.ledger = ledger;
    this.mutex = new Mutex();
  }

  status() {
    return this.mutex.run(async () => ({ ok: true, service: "philipedia-router", ...(await this.browser.status()) }));
  }

  originResolve(originMessages) {
    return this.mutex.run(() => this.#resolveWithoutLock(originMessages));
  }

  async #resolveWithoutLock(originMessages) {
    const fingerprint = validateFingerprint(originMessages);
    const snapshots = await this.browser.conversationSnapshots();
    const matches = snapshots.filter(item => suffixEquals(item.messages, fingerprint));
    if (matches.length === 0) throw new RouterError("origin-not-found", "No conversation in the dedicated profile exactly matches the fingerprint", { fingerprint: fingerprintDigest(fingerprint) });
    if (matches.length !== 1) throw new RouterError("origin-ambiguous", "More than one conversation exactly matches the fingerprint", { candidates: matches.length, fingerprint: fingerprintDigest(fingerprint) });
    const verified = parseConversationUrl(matches[0].url);
    if (verified.conversationId !== matches[0].conversationId) throw new RouterError("origin-unverifiable", "Snapshot URL and conversation identity disagree");
    return { ok: true, result: "origin-resolved", conversationId: verified.conversationId, url: verified.canonicalUrl, fingerprint: fingerprintDigest(fingerprint) };
  }

  route({ url, origin_messages, lifecycle, reviewer, evidence, created_by } = {}) {
    return this.mutex.run(async () => {
      if (!LIFECYCLES.includes(lifecycle)) throw new RouterError("invalid-lifecycle", `lifecycle must be one of: ${LIFECYCLES.join(", ")}`);
      if (!url && !origin_messages) throw new RouterError("origin-required", "Supply a canonical conversation URL or exact origin_messages");
      const sourceUrl = url || (await this.#resolveWithoutLock(origin_messages)).url;
      const origin = parseConversationUrl(sourceUrl);
      const resolved = [];
      for (const candidateLifecycle of LIFECYCLES) {
        const candidate = await this.browser.resolveProject(this.projects[candidateLifecycle]);
        if (!candidate?.id || !candidate?.segment) throw new RouterError("project-identity-unverifiable", `Project '${this.projects[candidateLifecycle]}' lacks a browser-verified identity`);
        resolved.push({ ...candidate, lifecycle: candidateLifecycle });
      }
      const ids = resolved.map(project => project.id);
      if (new Set(ids).size !== ids.length) throw new RouterError("duplicate-project-target", "Two lifecycle names resolved to the same stable project ID");
      const target = resolved.find(project => project.lifecycle === lifecycle);
      const prior = await this.ledger.get(origin.conversationId);
      if (lifecycle === "done") {
        const reviewTarget = resolved.find(project => project.lifecycle === "review");
        if (!prior || prior.lifecycle !== "review" || prior.projectId !== reviewTarget.id)
          throw new RouterError("review-required", "Done requires a recorded and verified Review transition");
        if (!String(reviewer ?? "").trim() || !String(evidence ?? "").trim())
          throw new RouterError("review-evidence-required", "Done requires an independent reviewer and evidence");
        if (prior.createdBy && prior.createdBy.trim().toLocaleLowerCase() === String(reviewer).trim().toLocaleLowerCase())
          throw new RouterError("reviewer-not-independent", "The origin agent cannot approve its own work");
        const current = await this.browser.inspectConversation(origin.canonicalUrl);
        if (current.conversationId !== origin.conversationId || current.projectId !== reviewTarget.id)
          throw new RouterError("review-route-unverified", "The conversation is no longer in verified Review");
      }
      const result = await this.browser.moveConversation(origin.canonicalUrl, target);
      if (result.conversationId !== origin.conversationId || result.projectId !== target.id)
        throw new RouterError("route-verification-mismatch", "Browser result did not independently preserve both requested identities");
      const record = await this.ledger.put(origin.conversationId, {
        ...prior,
        conversationId: origin.conversationId,
        url: result.url,
        lifecycle, projectId: target.id,
        createdBy: prior?.createdBy || String(created_by ?? "").trim() || null,
        ...(lifecycle === "done" ? { reviewer: String(reviewer).trim(), reviewEvidence: String(evidence).trim() } : {}),
      });
      return { ...result, lifecycle, task: record };
    });
  }
}

function suffixEquals(actual, expected) {
  if (!Array.isArray(actual) || actual.length < expected.length) return false;
  return expected.every((message, i) => actual[actual.length - expected.length + i] === message);
}
