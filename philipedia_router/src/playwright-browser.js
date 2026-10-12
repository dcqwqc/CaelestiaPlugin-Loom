import { chromium } from "playwright";
import { RouterError } from "./errors.js";
import { normalizeMessage, parseConversationUrl, projectCoreId } from "./identity.js";

const CHATGPT = "https://chatgpt.com/";

export class PlaywrightBrowser {
  constructor({ profilePath, headless = true, launchPersistentContext = chromium.launchPersistentContext.bind(chromium) } = {}) {
    this.profilePath = profilePath;
    this.headless = headless;
    this.launchPersistentContext = launchPersistentContext;
    this.context = null;
  }

  async start() {
    if (this.context) return;
    this.context = await this.launchPersistentContext(this.profilePath, {
      headless: this.headless,
      channel: process.env.LOOM_ROUTER_CHROME_CHANNEL || "chromium",
      args: ["--disable-dev-shm-usage"],
    });
  }

  async close() {
    await this.context?.close();
    this.context = null;
  }

  async status() {
    await this.start();
    const page = await this.#probePage();
    try {
      await this.#ensureChatGpt(page);
      const loggedIn = await this.#authenticatedEvidence(page);
      return { loggedIn, profilePath: this.profilePath, origin: "https://chatgpt.com", pages: this.context.pages().length };
    } finally { if (page.__routerTemporary) await page.close(); }
  }

  async conversationSnapshots({ maxCandidates = 40 } = {}) {
    await this.#requireAuthenticated();
    const urls = new Set();
    for (const page of this.context.pages()) {
      try { urls.add(parseConversationUrl(page.url()).canonicalUrl); } catch {}
    }
    const index = await this.context.newPage();
    try {
      await this.#ensureChatGpt(index);
      const hrefs = await index.locator('a[href*="/c/"]').evaluateAll(elements => elements.map(el => el.href));
      for (const href of hrefs) {
        try { urls.add(parseConversationUrl(href).canonicalUrl); } catch {}
      }
    } finally { await index.close(); }
    if (urls.size > maxCandidates) throw new RouterError("origin-candidate-limit", `Dedicated profile exposes more than ${maxCandidates} conversation candidates; provide the canonical URL`);
    const snapshots = [];
    for (const url of urls) {
      const page = await this.context.newPage();
      try {
        await page.goto(url, { waitUntil: "domcontentloaded" });
        await page.waitForLoadState("networkidle", { timeout: 2_000 }).catch(() => {});
        const route = parseConversationUrl(page.url());
        const expected = parseConversationUrl(url);
        if (route.conversationId !== expected.conversationId) continue;
        const messages = await this.#userMessages(page);
        snapshots.push({ url: route.canonicalUrl, conversationId: route.conversationId, messages: messages.slice(-3) });
      } catch (error) {
        if (error instanceof RouterError) continue;
        throw error;
      } finally { await page.close(); }
    }
    return snapshots;
  }

  async inspectConversation(canonicalUrl) {
    await this.#requireAuthenticated();
    const origin = parseConversationUrl(canonicalUrl);
    const page = await this.context.newPage();
    try {
      await page.goto(origin.canonicalUrl, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 2_000 }).catch(() => {});
      const current = parseConversationUrl(page.url());
      if (current.conversationId !== origin.conversationId)
        throw new RouterError("conversation-identity-mismatch", "Read-only inspection did not preserve the exact conversation identity");
      return current;
    } finally { await page.close(); }
  }

  async resolveProject(projectName) {
    await this.#requireAuthenticated();
    const page = await this.context.newPage();
    try {
      await this.#ensureChatGpt(page);
      const wanted = `new chat in ${projectName}`.toLocaleLowerCase();
      const controls = page.locator('button,[role="button"]').filter({ visible: true });
      const count = await controls.count();
      const matches = [];
      for (let i = 0; i < count; i++) {
        const item = controls.nth(i);
        const label = normalizeMessage(await item.getAttribute("aria-label"));
        if (label.toLocaleLowerCase() === wanted) matches.push(item);
      }
      if (matches.length === 0) throw new RouterError("project-control-not-found", `Project '${projectName}' is not uniquely visible`);
      if (matches.length !== 1) throw new RouterError("project-control-ambiguous", `Project '${projectName}' has ${matches.length} matching controls`);
      await matches[0].click();
      await page.waitForURL(url => Boolean(parseProjectRoute(url.toString())), { timeout: 10_000 }).catch(() => {});
      const project = parseProjectRoute(page.url());
      if (!project) throw new RouterError("project-route-unverified", `Selecting '${projectName}' did not produce a verifiable project route`);
      return { id: project.projectId, segment: project.segment, name: projectName };
    } finally { await page.close(); }
  }

  async moveConversation(canonicalUrl, target) {
    await this.#requireAuthenticated();
    const expected = parseConversationUrl(canonicalUrl);
    const page = await this.context.newPage();
    try {
      await page.goto(expected.canonicalUrl, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 2_000 }).catch(() => {});
      const before = parseConversationUrl(page.url());
      if (before.conversationId !== expected.conversationId) throw new RouterError("conversation-identity-mismatch", "Browser did not load the requested conversation identity");
      if (before.projectId === target.id) return verifiedMove("already-in-project", before, target);

      const direct = page.getByRole("menuitem", { name: /^(move to project|add to project)$/i, exact: true });
      if (await direct.count() === 0) await this.#openExactChatActions(page, expected.conversationId);
      const move = page.getByRole("menuitem", { name: /^(move to project|add to project)$/i, exact: true });
      if (await move.count() !== 1) throw new RouterError("move-project-control-not-unique", "Exact Move to project control was not uniquely available");
      await move.click();

      const options = page.locator('[role="menuitem"],[role="option"]').filter({ hasText: target.name });
      const exact = [];
      for (let i = 0; i < await options.count(); i++) {
        const item = options.nth(i);
        const labels = [await item.getAttribute("aria-label"), await item.innerText()].map(normalizeMessage);
        if (labels.some(label => label.toLocaleLowerCase() === target.name.toLocaleLowerCase())) exact.push(item);
      }
      if (exact.length !== 1) throw new RouterError(exact.length ? "project-choice-ambiguous" : "project-choice-not-found", `Move target '${target.name}' was not unique`);
      await exact[0].click();
      await page.waitForURL(url => {
        try {
          const route = parseConversationUrl(url.toString());
          return route.conversationId === expected.conversationId && route.projectId === target.id;
        } catch { return false; }
      }, { timeout: 10_000 }).catch(() => {});
      const after = parseConversationUrl(page.url());
      if (after.conversationId !== expected.conversationId || after.projectId !== target.id) {
        throw new RouterError("project-move-unverified", "Post-selection browser route did not prove the exact conversation and project identities");
      }
      return verifiedMove("project-move-verified", after, target);
    } finally { await page.close(); }
  }

  async #openExactChatActions(page, conversationId) {
    const links = page.locator(`a[href*="/c/${cssEscape(conversationId)}"]`);
    const buttons = [];
    for (let i = 0; i < await links.count(); i++) {
      let row = links.nth(i);
      for (let depth = 0; depth < 7; depth++) {
        const found = row.locator('button[aria-label="Chat actions"]');
        if (await found.count() === 1) { buttons.push(found); break; }
        row = row.locator("..");
      }
    }
    if (buttons.length !== 1) throw new RouterError("chat-actions-not-unique", "Exact conversation row action was not uniquely available");
    await buttons[0].hover();
    await buttons[0].click();
  }

  async #userMessages(page) {
    const messages = await page.locator('[data-message-author-role="user"]').allInnerTexts();
    return messages.map(normalizeMessage).filter(Boolean);
  }

  async #probePage() {
    const existing = this.context.pages().find(page => page.url().startsWith(CHATGPT));
    if (existing) return existing;
    const page = await this.context.newPage();
    page.__routerTemporary = true;
    return page;
  }

  async #ensureChatGpt(page) {
    if (!page.url().startsWith(CHATGPT)) await page.goto(CHATGPT, { waitUntil: "domcontentloaded" });
  }

  async #authenticatedEvidence(page) {
    const login = page.getByRole("button", { name: /log in/i }).or(page.getByRole("link", { name: /log in/i }));
    if (await login.count()) return false;
    const evidence = page.locator('[data-testid="profile-button"],button[aria-label*="account" i],button[aria-label^="New chat in " i]');
    return (await evidence.count()) > 0;
  }

  async #requireAuthenticated() {
    const status = await this.status();
    if (!status.loggedIn) throw new RouterError("not-authenticated", "Dedicated ChatGPT profile is not verifiably signed in");
  }
}

function parseProjectRoute(value) {
  let url;
  try { url = new URL(value); } catch { return null; }
  if (url.origin !== "https://chatgpt.com") return null;
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts[0] !== "g" || parts.length < 2) return null;
  const projectId = projectCoreId(parts[1]);
  if (!projectId || (parts.length > 2 && parts[2] !== "project")) return null;
  return { projectId, segment: parts[1] };
}

function verifiedMove(result, route, target) {
  return { ok: true, result, conversationId: route.conversationId, projectId: target.id, projectName: target.name, url: route.canonicalUrl };
}

function cssEscape(value) {
  return String(value).replace(/["\\]/g, "\\$&");
}
