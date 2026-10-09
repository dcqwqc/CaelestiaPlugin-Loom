// ==UserScript==
// @name QWQC Tabby Voice Engine Bridge
// @description Dedicated standalone ChatGPT Voice engine window for Tabby.
// @author qwqc
// ==/UserScript==

(() => {
  "use strict";

    const ACTOR_NAME = "QwqcHeyTabby";
  const VERSION = "0.10.12";
  const TABBY_URL = "https://chatgpt.com/?tabby=1";
  const ENGINE_CHROME_URL = "chrome://userscripts/content/tabby-engine.xhtml";
  const COMMAND_PATH = PathUtils.join(PathUtils.profileDir, "tabby-bridge-command.json");
  const STATE_PATH = PathUtils.join(PathUtils.profileDir, "tabby-bridge-state.json");
  function browserWindows() {
    const out = [];
    try {
      const it = Services.wm.getEnumerator("navigator:browser");
      while (it.hasMoreElements()) out.push(it.getNext());
    } catch (_) {}
    return out;
  }

  // Every normal Zen chrome window keeps a tiny takeover watchdog. Exactly one
  // window owns the Tabby command controller at a time. If that host window is
  // later closed, another already-open Zen window notices the stale heartbeat
  // and takes over without requiring a browser restart.
  const HEARTBEAT_PREF = "qwqc.hey_tabby.runtime.controller_heartbeat";
  const OWNER_PREF = "qwqc.hey_tabby.runtime.controller_owner";
  const TAKEOVER_AFTER_MS = 3200;
  const makeTimer = (callback, delay, repeating = false) => {
    const t = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
    t.initWithCallback(callback, delay, repeating ? Ci.nsITimer.TYPE_REPEATING_SLACK : Ci.nsITimer.TYPE_ONE_SHOT);
    return t;
  };
  const readHeartbeat = () => {
    try { return Number(Services.prefs.getStringPref(HEARTBEAT_PREF, "0")); } catch (_) { return 0; }
  };
  const readOwner = () => {
    try { return Services.prefs.getStringPref(OWNER_PREF, ""); } catch (_) { return ""; }
  };
  const ownerToken = `${Date.now()}-${Math.random().toString(36).slice(2)}-${window.windowUtils?.outerWindowID || "w"}`;
  let localController = null;
  let takeoverTimer = null;

  function createController(controllerToken) {
    const sleep = ms => new Promise(resolve => window.setTimeout(resolve, ms));
    const withTimeout = (promise, timeoutMs, fallback) => Promise.race([
      promise,
      sleep(timeoutMs).then(() => (typeof fallback === "function" ? fallback() : fallback)),
    ]);
    let destroyed = false;
    let actorRegisteredHere = false;
    let timer = null;
    let heartbeatTimer = null;
    let pollBusy = false;
    let lastSeq = -1;
    const abandonedSeq = new Set();
    let debugVisible = false;
    let engineWindow = null;
    const workerWindows = new Map();

    function log(...args) { console.debug("[Tabby Engine]", ...args); }

    function ensureActorRegistration() {
      try {
        ChromeUtils.registerWindowActor(ACTOR_NAME, {
          child: { esModuleURI: "chrome://userscripts/content/actors/QwqcHeyTabbyChild.sys.mjs" },
          matches: ["https://chatgpt.com/*"],
          allFrames: false,
          safeForUntrustedWebProcess: true,
        });
        actorRegisteredHere = true;
      } catch (error) {
        if (error?.name !== "NotSupportedError") throw error;
      }
    }

    function grantMicPermission(browser = null) {
      const principals = [];
      try {
        principals.push(Services.scriptSecurityManager.createContentPrincipal(
          Services.io.newURI("https://chatgpt.com/"), {}
        ));
      } catch (_) {}
      try { if (browser?.contentPrincipal) principals.push(browser.contentPrincipal); } catch (_) {}
      try {
        const p = browser?.browsingContext?.currentWindowGlobal?.documentPrincipal;
        if (p) principals.push(p);
      } catch (_) {}

      const seen = new Set();
      const rows = [];
      for (const principal of principals) {
        try {
          const origin = String(principal.origin || "");
          const attrs = JSON.stringify(principal.originAttributes || {});
          const key = origin + "|" + attrs;
          if (seen.has(key)) continue;
          seen.add(key);
          Services.perms.addFromPrincipal(
            principal,
            "microphone",
            Ci.nsIPermissionManager.ALLOW_ACTION,
            Ci.nsIPermissionManager.EXPIRE_NEVER,
            0
          );
          rows.push({
            origin,
            originAttributes: principal.originAttributes || {},
            permission: Services.perms.testPermissionFromPrincipal(principal, "microphone"),
          });
        } catch (error) {
          rows.push({ error: String(error) });
        }
      }
      return rows;
    }

    async function micPermissionState() {
      const { browser } = await ensureEngineWindow(2200);
      if (!browser) return { ok:false, result:"engine-browser-unavailable" };
      const rows = grantMicPermission(browser);
      return { ok:true, result:"microphone-permission", rows };
    }

    function sessionStore() {
      try {
        return ChromeUtils.importESModule(
          "resource:///modules/sessionstore/SessionStore.sys.mjs"
        ).SessionStore;
      } catch (_) { return null; }
    }

    function isNativeEngineWindow(win) {
      if (!win || win.closed || !win.gBrowser) return false;
      try { if (win._qwqcTabbyEngine === true) return true; } catch (_) {}
      try {
        if (sessionStore()?.getCustomWindowValue(win, "qwqcTabbyEngine") === "1")
          return true;
      } catch (_) {}
      try {
        const tab = win.gBrowser?.selectedTab;
        if (tab?.getAttribute("qwqc-tabby-engine") === "true") return true;
      } catch (_) {}
      try {
        const uri = String(win.gBrowser?.selectedBrowser?.currentURI?.spec || "");
        if (uri.startsWith("https://chatgpt.com/") && uri.includes("tabby=1")) return true;
      } catch (_) {}
      return false;
    }

    function styleEngineWindow(win) {
      if (!win || win.closed || !win.gBrowser) return;
      try { win._qwqcTabbyEngine = true; } catch (_) {}
      try { win.document.documentElement.setAttribute("titlepreface", "Tabby Engine · "); } catch (_) {}
      try { win.gBrowser.selectedTab?.setAttribute("qwqc-tabby-engine", "true"); } catch (_) {}
      try { sessionStore()?.setCustomWindowValue(win, "qwqcTabbyEngine", "1"); } catch (_) {}
      try {
        const tab = win.gBrowser?.selectedTab;
        if (tab) sessionStore()?.setCustomTabValue(tab, "qwqcTabbyEngine", "1");
      } catch (_) {}
      try { win.gBrowser?.updateTitlebar?.(); } catch (_) {}
    }

    function findEngineWindow() {
      if (engineWindow && !engineWindow.closed && isNativeEngineWindow(engineWindow))
        return engineWindow;
      for (const existing of browserWindows()) {
        if (!isNativeEngineWindow(existing)) continue;
        engineWindow = existing;
        styleEngineWindow(existing);
        return existing;
      }
      return null;
    }

    function safeChatUrl(raw) {
      try {
        const url = new URL(String(raw || ""));
        if (url.origin !== "https://chatgpt.com" ||
            !(/^\/c\/[^/]+\/?$/.test(url.pathname) || /^\/g\/[^/]+\/c\/[^/]+\/?$/.test(url.pathname))) return null;
        return url.href;
      } catch (_) { return null; }
    }

    function safeWorkerUrl(raw) {
      try {
        const url = new URL(String(raw || ""));
        if (url.origin !== "https://chatgpt.com") return null;
        if (url.pathname === "/" || url.pathname.startsWith("/c/") || url.pathname.startsWith("/g/")) return url.href;
      } catch (_) {}
      return null;
    }

    function safeTaskId(raw) {
      return String(raw || "").replace(/[^A-Za-z0-9_.:-]/g, "").slice(0, 80);
    }

    function workerTitle(taskId) { return `Tabby Work · ${safeTaskId(taskId)}`; }

    function styleWorkerWindow(win, taskId) {
      if (!win || win.closed) return;
      try { win.document.title = workerTitle(taskId); } catch (_) {}
    }

    function findWorkerWindow(taskId) {
      taskId = safeTaskId(taskId);
      const cached = workerWindows.get(taskId);
      if (cached && !cached.closed) return cached;
      try {
        const it = Services.wm.getEnumerator("qwqc:tabby-engine");
        while (it.hasMoreElements()) {
          const win = it.getNext();
          if (!win || win.closed) continue;
          if (String(win.document?.title || "") === workerTitle(taskId)) {
            workerWindows.set(taskId, win);
            return win;
          }
        }
      } catch (_) {}
      return null;
    }

    async function createWorkerWindow(taskId, rawUrl) {
      taskId = safeTaskId(taskId);
      const url = safeWorkerUrl(rawUrl);
      if (!taskId || !url) return null;
      const host = hostWindow();
      if (!host) return null;
      let win = null;
      try {
        win = host.openDialog(
          ENGINE_CHROME_URL,
          "_blank",
          "chrome,dialog=no,resizable,width=900,height=760,left=-10000,top=-10000"
        );
      } catch (error) {
        log("worker openDialog failed", error);
        return null;
      }
      if (!await waitForEngineWindow(win)) return null;
      styleWorkerWindow(win, taskId);
      workerWindows.set(taskId, win);
      const browser = win.document.getElementById("tabby-browser");
      try {
        browser.loadURI(Services.io.newURI(url), {
          triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal(),
        });
      } catch (error) {
        log("worker loadURI failed", error);
        return null;
      }
      return win;
    }

    async function ensureWorkerWindow(taskId, rawUrl = "", timeoutMs = 3000, reload = false) {
      taskId = safeTaskId(taskId);
      const url = rawUrl ? safeWorkerUrl(rawUrl) : null;
      if (!taskId) return { win:null, browser:null, actor:null };
      let win = findWorkerWindow(taskId);
      if (!win && url) win = await createWorkerWindow(taskId, url);
      if (!win || win.closed) return { win:null, browser:null, actor:null };
      if (!await waitForEngineWindow(win, Math.min(timeoutMs, 3000))) return { win,browser:null,actor:null };
      styleWorkerWindow(win, taskId);
      const browser = win.document.getElementById("tabby-browser");
      if (!browser) return { win,browser:null,actor:null };
      let current = "";
      try { current = browser.currentURI?.spec || ""; } catch (_) {}
      if (url && (reload || current !== url)) {
        try {
          browser.loadURI(Services.io.newURI(url), {
            triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal(),
          });
        } catch (_) {}
      }
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        try {
          const global = browser?.browsingContext?.currentWindowGlobal;
          const uri = String(global?.documentURI?.spec || global?.documentURI || "");
          if (uri.startsWith("https://chatgpt.com/")) {
            const actor = global.getActor?.(ACTOR_NAME);
            if (actor) return { win,browser,actor };
          }
        } catch (_) {}
        await sleep(100);
      }
      return { win,browser,actor:null };
    }

    async function queryWorker(taskId, name, data = {}, timeoutMs = 2500) {
      const { win, actor } = await ensureWorkerWindow(taskId, "", timeoutMs, false);
      if (!actor) return { ok:false, result:"worker-actor-unavailable", taskId:safeTaskId(taskId) };
      try {
        const result = await Promise.race([
          actor.sendQuery(name, data),
          sleep(timeoutMs).then(() => ({ ok:false, result:"actor-query-timeout" })),
        ]);
        return { ...result, taskId:safeTaskId(taskId), workerWindow:true,
          engineWindowState:(() => { try { return win.windowState; } catch (_) { return -1; } })() };
      } catch (error) {
        return { ok:false, result:String(error), taskId:safeTaskId(taskId), workerWindow:true };
      }
    }

    async function openWorker(taskId, rawUrl, reload = false) {
      const url = safeChatUrl(rawUrl);
      if (!url) return { ok:false, result:"invalid-chat-url" };
      const { actor } = await ensureWorkerWindow(taskId, url, 9000, reload);
      if (!actor) return { ok:false, result:"worker-open-timeout", taskId:safeTaskId(taskId) };
      const result = await withTimeout(
        actor.sendQuery("voiceStatus", {}),
        2200,
        { ok:false, result:"actor-query-timeout" },
      );
      return { ...result, ok:Boolean(result?.ok), result:result?.ok ? "worker-ready" : String(result?.result || "worker-status-failed"), taskId:safeTaskId(taskId), workerWindow:true };
    }

    async function createWorker(taskId, prompt) {
      const fresh = "https://chatgpt.com/?loom-worker=1";
      const existing = await ensureWorkerWindow(taskId, "", 1800, false);
      if (existing.win) {
        if (!existing.actor) {
          return { ok:false, result:"worker-existing-unverified", taskId:safeTaskId(taskId) };
        }
        const current = await existing.actor.sendQuery("voiceStatus", {});
        const href = safeChatUrl(current?.href);
        if (href) {
          const discovered = await queryWorker(taskId, "discoverProjects", {}, 1800);
          return { ok:true, result:"worker-existing", href, projects:discovered?.projects || [], taskId:safeTaskId(taskId) };
        }
        // An existing task window may contain a submitted prompt whose route
        // has not canonicalised yet. Never navigate it or submit again.
        return { ok:false, result:"worker-existing-awaiting-canonical", taskId:safeTaskId(taskId) };
      }
      const { actor } = await ensureWorkerWindow(taskId, fresh, 9000, false);
      if (!actor) return { ok:false, result:"worker-create-timeout", taskId:safeTaskId(taskId) };
      const before = await actor.sendQuery("voiceStatus", {});
      if (safeChatUrl(before?.href)) return { ok:false, result:"fresh-worker-route-not-confirmed" };
      const sent = await actor.sendQuery("sendText", { text:String(prompt || "") });
      if (!sent?.ok) return { ...sent, ok:false, result:String(sent?.result || "task-send-failed") };
      const deadline = Date.now() + 18000;
      while (Date.now() < deadline) {
        await sleep(160);
        const status = await queryWorker(taskId, "voiceStatus", {}, 1200);
        const href = safeChatUrl(status?.href);
        if (href) {
          const discovered = await queryWorker(taskId, "discoverProjects", {}, 1800);
          return { ok:true, result:"worker-created", href, projects:discovered?.projects || [], taskId:safeTaskId(taskId) };
        }
      }
      return { ok:false, result:"canonical-conversation-timeout", taskId:safeTaskId(taskId) };
    }

    async function recoverWorker(taskId) {
      const deadline = Date.now() + 18000;
      while (Date.now() < deadline) {
        const existing = await ensureWorkerWindow(taskId, "", 1800, false);
        if (!existing.win || existing.win.closed) return { ok:false, result:"worker-window-missing", taskId:safeTaskId(taskId) };
        if (existing.actor) {
          const status = await existing.actor.sendQuery("voiceStatus", {});
          const href = safeChatUrl(status?.href);
          if (href) {
            const discovered = await existing.actor.sendQuery("discoverProjects", {});
            return { ok:true, result:"worker-recovered", href, projects:discovered?.projects || [], taskId:safeTaskId(taskId) };
          }
        }
        await sleep(200);
      }
      return { ok:false, result:"canonical-conversation-recovery-timeout", taskId:safeTaskId(taskId) };
    }

    async function closeWorker(taskId) {
      taskId = safeTaskId(taskId);
      const win = findWorkerWindow(taskId);
      workerWindows.delete(taskId);
      if (win && !win.closed) {
        try { win.close(); } catch (_) {}
      }
      return { ok:true, result:"worker-closed", taskId };
    }

    async function waitForEngineWindow(win, timeoutMs = 10000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        try {
          if (win && !win.closed && win.document?.readyState === "complete" &&
              win.document.getElementById("tabby-browser"))
            return true;
        } catch (_) {}
        await sleep(100);
      }
      return false;
    }

    function hostWindow() {
      for (const candidate of browserWindows()) {
        if (candidate && !candidate.closed) return candidate;
      }
      return (!window.closed) ? window : null;
    }

    async function waitForNativeEngineWindow(win, timeoutMs = 10000) {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        try {
          if (win && !win.closed && win.document?.readyState === "complete" &&
              win.gBrowser?.selectedBrowser)
            return true;
        } catch (_) {}
        await sleep(100);
      }
      return false;
    }

    async function createEngineWindow() {
      const host = hostWindow();
      if (!host) return null;
      let win = null;
      try {
        const { BrowserWindowTracker } = ChromeUtils.importESModule(
          "resource:///modules/BrowserWindowTracker.sys.mjs"
        );
        const args = Cc["@mozilla.org/supports-string;1"].createInstance(Ci.nsISupportsString);
        args.data = TABBY_URL;
        win = BrowserWindowTracker.openWindow({
          openerWindow: host,
          args,
          features: "width=900,height=760,resizable,suppressanimation",
          zenSyncedWindow: false,
        });
      } catch (error) {
        log("native engine window failed", error);
        return null;
      }
      engineWindow = win;
      if (!await waitForNativeEngineWindow(win)) return null;
      styleEngineWindow(win);
      return win;
    }

    async function loadEngineUrl(win, url, timeoutMs = 4500) {
      const deadline = Date.now() + timeoutMs;
      let lastError = "";
      while (Date.now() < deadline) {
        const browser = win?.gBrowser?.selectedBrowser;
        if (!browser) { await sleep(90); continue; }
        try {
          // Restored Zen tabs briefly expose a selectedBrowser before Gecko has
          // attached its frameLoader/remoteTab. loadURI during that gap throws.
          if (!browser.frameLoader || !browser.browsingContext) {
            await sleep(90);
            continue;
          }
          browser.loadURI(Services.io.newURI(url), {
            triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal(),
          });
          return { ok:true, browser };
        } catch (error) {
          lastError = String(error);
          await sleep(100);
        }
      }
      return {
        ok:false,
        browser:win?.gBrowser?.selectedBrowser || null,
        error:lastError || "engine-browser-not-navigable",
      };
    }

    async function ensureEngineWindow(timeoutMs = 2200) {
      let win = findEngineWindow();
      if (!win) win = await createEngineWindow();
      if (!win || win.closed) return { win: null, browser: null, actor: null };
      if (!await waitForNativeEngineWindow(win, Math.min(Math.max(timeoutMs, 1200), 3000)))
        return { win, browser: null, actor: null };

      styleEngineWindow(win);
      let browser = win.gBrowser?.selectedBrowser;
      if (!browser) return { win, browser: null, actor: null };

      let current = "";
      try { current = browser.currentURI?.spec || ""; } catch (_) {}
      if (!current.startsWith("https://chatgpt.com/")) {
        const loaded = await loadEngineUrl(win, TABBY_URL, Math.min(Math.max(timeoutMs, 1800), 4500));
        if (!loaded.ok) return { win, browser:loaded.browser, actor:null };
        browser = loaded.browser;
      }

      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        try {
          browser = win.gBrowser?.selectedBrowser || browser;
          const global = browser?.browsingContext?.currentWindowGlobal;
          const uri = global?.documentURI?.spec || global?.documentURI || "";
          if (String(uri).startsWith("https://chatgpt.com/")) {
            grantMicPermission(browser);
            const actor = global.getActor?.(ACTOR_NAME);
            if (actor) return { win, browser, actor };
          }
        } catch (_) {}
        await sleep(100);
      }
      return { win, browser, actor: null };
    }

    function setEngineVisible(win, visible) {
      // Visibility is handled exclusively by Hyprland (active workspace vs
      // special:tabby). Never minimize/restore the Gecko window here: on
      // Wayland that caused fullscreen flips and repeated geometry churn.
      debugVisible = Boolean(visible);
      if (!win || win.closed) return;
      styleEngineWindow(win);
    }

    async function queryNormalSelected(name, data = {}, timeoutMs = 6000) {
      for (const win of browserWindows()) {
        if (!win || win.closed) continue;
        const browser = win.gBrowser?.selectedBrowser;
        if (!browser) continue;
        let uri = "";
        try { uri = browser.currentURI?.spec || ""; } catch (_) {}
        if (!String(uri).startsWith("https://chatgpt.com/")) continue;
        try {
          grantMicPermission(browser);
          const global = browser.browsingContext?.currentWindowGlobal;
          const actor = global?.getActor?.(ACTOR_NAME);
          if (!actor) continue;
          const result = await Promise.race([
            actor.sendQuery(name, data),
            sleep(timeoutMs).then(() => ({ ok:false, result:"actor-query-timeout" })),
          ]);
          return { ...result, normalBrowser:true, normalHref:uri };
        } catch (error) {
          return { ok:false, result:"normal-browser-query-failed", error:String(error), normalHref:uri };
        }
      }
      return { ok:false, result:"normal-chatgpt-tab-not-found" };
    }

    async function query(name, data = {}, timeoutMs = 2200) {
      const { win, actor } = await ensureEngineWindow(timeoutMs);
      if (!actor) {
        let engineHref = "";
        try { engineHref = String(win?.gBrowser?.selectedBrowser?.currentURI?.spec || ""); } catch (_) {}
        return { ok: false, result: "actor-unavailable", active: false, debugVisible, engineHref };
      }
      try {
        const result = await Promise.race([
          actor.sendQuery(name, data),
          sleep(timeoutMs).then(() => ({ ok:false, result:"actor-query-timeout", active:false })),
        ]);
        return {
          ...result,
          debugVisible,
          engineWindow: true,
          engineWindowState: (() => { try { return win.windowState; } catch (_) { return -1; } })(),
        };
      } catch (error) {
        return { ok: false, result: String(error), active: false, debugVisible, engineWindow: true };
      }
    }

    async function focusEngineContent() {
      const { win, browser, actor } = await ensureEngineWindow(3000);
      if (!win || !browser) return { ok:false, result:"engine-browser-unavailable" };
      try { win.focus(); } catch (_) {}
      try { Services.focus.activeWindow = win; } catch (_) {}
      try { Services.focus.setFocus(browser, Services.focus.FLAG_NOSCROLL); } catch (_) {
        try { browser.focus(); } catch (_) {}
      }
      try { browser.focus(); } catch (_) {}
      await sleep(60);
      let actorResult = {};
      if (actor) {
        try {
          actorResult = await withTimeout(
            actor.sendQuery("focusPage", {}),
            1200,
            { ok:false, result:"focus-page-timeout", documentHasFocus:false },
          );
        } catch (_) {}
      }
      let focused = null;
      try { focused = Services.focus.focusedElement; } catch (_) {}
      const ok = Boolean(actorResult?.documentHasFocus || actorResult?.ok);
      return {
        ok,
        result: ok ? "engine-content-focused" : "engine-content-focus-failed",
        actorResult,
        chromeActiveTitle: String(Services.focus.activeWindow?.document?.title || ""),
        chromeFocusedTag: String(focused?.tagName || focused?.localName || ""),
        chromeFocusedId: String(focused?.id || ""),
        debugVisible,
      };
    }

    function restoreBrowserFocus(win) {
      if (!win || win.closed || !win.gBrowser) return false;
      try { Services.focus.activeWindow = win; } catch (_) {}
      const browser = win.gBrowser?.selectedBrowser;
      if (!browser) return false;
      try { Services.focus.setFocus(browser, Services.focus.FLAG_NOSCROLL); } catch (_) {}
      try { browser.focus(); } catch (_) {}
      return true;
    }

    async function navigateEngineToMarker() {
      const { win, browser } = await ensureEngineWindow(3000);
      if (!win || !browser) return { ok:false, result:"engine-browser-unavailable" };
      const loaded = await loadEngineUrl(win, TABBY_URL, 4500);
      return loaded.ok
        ? { ok:true, result:"navigating" }
        : { ok:false, result:"navigation-failed", error:loaded.error };
    }

    async function openChat(rawUrl) {
      const url = safeChatUrl(rawUrl);
      if (!url) return { ok:false, result:"invalid-chat-url" };
      const { win, browser } = await ensureEngineWindow(3000);
      if (!win || !browser) return { ok:false, result:"engine-browser-unavailable" };
      try {
        const status = await query("voiceStatus", {}, 900);
        if (status?.active) await query("endVoice", {}, 1200);
      } catch (_) {}
      const loaded = await loadEngineUrl(win, url, 5000);
      if (!loaded.ok)
        return { ok:false, result:"navigation-failed", error:loaded.error };
      let stable = 0;
      let lastHref = "";
      const openDeadline = Date.now() + 12000;
      while (Date.now() < openDeadline) {
        await sleep(120);
        const status = await query("voiceStatus", {}, 650);
        if (!status.ok) { stable=0; continue; }
        if (status.loggedOut) return { ...status, result:"needs-login" };
        const usable = status.composerReady && !status.working &&
          String(status.href || "").startsWith(url.split("?")[0]);
        if (usable && status.href === lastHref) stable += 1;
        else stable = usable ? 1 : 0;
        lastHref = status.href || "";
        if (stable >= 2) return { ...status, ok:true, result:"chat-open-ready" };
      }
      return { ok:false, result:"open-chat-timeout" };
    }

    async function continueChat() {
      let status = await query("voiceStatus", {}, 1000);
      if (status?.active) {
        await query("endVoice", {}, 1200);
        await sleep(180);
        status = await query("voiceStatus", {}, 1000);
      }
      if (status?.loggedOut) return { ...status, result: "needs-login" };

      // Voice cannot reliably enter while ChatGPT is still generating. Give a
      // healthy response a short grace period to finish. If the page explicitly
      // reports a connection interruption, stop the orphaned generation at once.
      const naturalFinishDeadline = Date.now() + 2200;
      while (status?.working && !status?.connectionInterrupted && Date.now() < naturalFinishDeadline) {
        await sleep(160);
        status = await query("voiceStatus", {}, 700);
      }
      if (status?.working) {
        await query("stopResponse", {}, 1400);
        const stopDeadline = Date.now() + 3200;
        while (Date.now() < stopDeadline) {
          await sleep(140);
          status = await query("voiceStatus", {}, 700);
          if (status?.ok && !status?.working) break;
        }
      }

      const continueDeadline = Date.now() + 9000;
      while (Date.now() < continueDeadline) {
        if (status?.ok && status?.composerReady && !status?.working) {
          let fresh = false;
          try {
            const url = new URL(status.href || "");
            fresh = url.origin === "https://chatgpt.com" && url.pathname === "/";
          } catch (_) {}
          return { ...status, ok: true, fresh, result: "continue-ready" };
        }
        await sleep(120);
        status = await query("voiceStatus", {}, 650);
        if (status?.loggedOut) return { ...status, result: "needs-login" };
      }
      return { ...status, ok: false, result: status?.working ? "continue-still-working" : "continue-timeout" };
    }

    async function freshChat() {
      let current = {};
      try {
        current = await query("voiceStatus", {}, 900);
        if (current.active) {
          await query("endVoice", {}, 1200);
          current = await query("voiceStatus", {}, 900);
        }
      } catch (_) {}

      // end() already parks Tabby on the canonical fresh composer. Reusing
      // that route avoids a full ChatGPT reload on every wake while still
      // guaranteeing that each session starts from a new conversation.
      try {
        const url = new URL(current.href || "");
        const alreadyFresh = url.origin === "https://chatgpt.com" &&
          url.pathname === "/" && url.searchParams.get("tabby") === "1" &&
          current.composerReady && !current.working;
        if (alreadyFresh) {
          try { await query("clearComposer", {}, 900); } catch (_) {}
          return { ...current, ok: true, result: "new-chat-ready" };
        }
      } catch (_) {}

      const navigation = await navigateEngineToMarker();
      if (!navigation.ok) return navigation;

      let stable = 0;
      let lastHref = "";
      const freshDeadline = Date.now() + 15000;
      while (Date.now() < freshDeadline) {
        await sleep(120);
        const status = await query("voiceStatus", {}, 550);
        if (!status.ok) { stable = 0; continue; }
        if (status.loggedOut) return { ...status, result: "needs-login" };
        let fresh = false;
        try {
          const url = new URL(status.href);
          fresh = url.pathname === "/" && url.searchParams.get("tabby") === "1";
        } catch (_) {}
        const usable = Boolean(fresh && status.composerReady);
        if (usable && status.href === lastHref) stable += 1;
        else stable = usable ? 1 : 0;
        lastHref = status.href || "";
        if (stable >= 2) {
          try { await query("clearComposer", {}, 900); } catch (_) {}
          return { ...status, ok: true, result: "new-chat-ready" };
        }
      }
      return { ok: false, result: "new-chat-timeout" };
    }

    async function resetEngineToMarker() {
      return navigateEngineToMarker();
    }

    async function writeState(seq, command, result) {
      const payload = {
        seq, command, ...result,
        bridgeLoaded: true,
        version: VERSION,
        timestamp: Date.now(),
      };
      try { await IOUtils.writeJSON(STATE_PATH, payload, { tmpPath: STATE_PATH + ".tmp" }); }
      catch (error) { log("write state", error); }
      return payload;
    }

    async function handleCommand(command) {
      const seq = Number(command?.seq ?? -1);
      if (seq < 0 || seq === lastSeq) return;
      lastSeq = seq;
      const name = String(command?.command || "status");
      let result;
      try {

      if (name === "show") {
        let win = findEngineWindow();
        if (!win) win = await createEngineWindow();
        setEngineVisible(win, true);
        result = { ok: Boolean(win), result: win ? "shown" : "engine-window-unavailable", active: false, debugVisible: true };
      } else if (name === "hide") {
        let win = findEngineWindow();
        if (!win) win = await createEngineWindow();
        setEngineVisible(win, false);
        result = { ok: Boolean(win), result: win ? "hidden" : "engine-window-unavailable", active: false, debugVisible: false };
      } else if (name === "new-chat") {
        let win = findEngineWindow();
        if (!win) win = await createEngineWindow();
        setEngineVisible(win, Boolean(command.debug));
        result = await freshChat();
      } else if (name === "continue-chat") {
        let win = findEngineWindow();
        if (!win) win = await createEngineWindow();
        setEngineVisible(win, Boolean(command.debug));
        result = await continueChat();
      } else if (name === "open-chat") {
        result = await openChat(String(command.url || ""));
      } else if (name === "worker-open") {
        result = await openWorker(command.taskId, command.url, Boolean(command.reload));
      } else if (name === "worker-create") {
        result = await createWorker(command.taskId, command.prompt);
      } else if (name === "worker-recover") {
        result = await recoverWorker(command.taskId);
      } else if (name === "worker-discover-projects") {
        result = await queryWorker(command.taskId, "discoverProjects", {}, 3500);
      } else if (name === "worker-move-project") {
        result = await queryWorker(command.taskId, "moveToProject", { projectId:command.projectId, projectName:command.projectName }, 9000);
      } else if (name === "worker-status") {
        result = await queryWorker(command.taskId, "voiceStatus", {}, 1800);
      } else if (name === "worker-latest-response") {
        result = await queryWorker(command.taskId, "latestAssistantResponse", {}, 1800);
      } else if (name === "worker-close") {
        result = await closeWorker(command.taskId);
      } else if (name === "activate") {
        const previousWindow = (() => { try { return Services.focus.activeWindow; } catch (_) { return null; } })();
        const { win } = await ensureEngineWindow();
        setEngineVisible(win, Boolean(command.debug));
        let focusResult = await focusEngineContent();
        if (!focusResult?.ok) {
          await sleep(140);
          focusResult = await focusEngineContent();
        }
        if (!focusResult?.ok) {
          if (previousWindow && previousWindow !== win) restoreBrowserFocus(previousWindow);
          result = { ok:false, result:"engine-content-focus-failed", active:false, focusResult };
          await writeState(seq, name, result);
          return;
        }

        // The Voice button can be focus-gated in a parked native Zen window.
        // Wait for it only after the transparent compositor + Gecko focus
        // handshake has succeeded; preload/continue must never wait on it.
        let preActivationStatus = await query("voiceStatus", {}, 800);
        const readyDeadline = Date.now() + 4200;
        while (!preActivationStatus?.ready && !preActivationStatus?.active && Date.now() < readyDeadline) {
          await sleep(100);
          preActivationStatus = await query("voiceStatus", {}, 700);
        }
        if (!preActivationStatus?.ready && !preActivationStatus?.active) {
          if (previousWindow && previousWindow !== win) restoreBrowserFocus(previousWindow);
          result = { ...preActivationStatus, ok:false, result:"voice-control-not-ready", focusResult };
          await writeState(seq, name, result);
          return;
        }
        const activation = preActivationStatus?.active
          ? preActivationStatus
          : await query("activateVoice", {}, 3000);

        // Return at the same semantic transition that produces ChatGPT's
        // Voice-on cue: the Voice surface itself becoming active. Microphone
        // recovery is deliberately handled by Tabby's backend afterwards, so
        // the green face never waits on a slower WebRTC track handshake.
        let activationStatus = {};
        const deadline = Date.now() + 5200;
        while (Date.now() < deadline) {
          await sleep(35);
          activationStatus = await query("voiceStatus", {}, 650);
          if (activationStatus?.active) break;
        }
        if (previousWindow && previousWindow !== win) restoreBrowserFocus(previousWindow);
        const micLive = Array.from(activationStatus?.audioTracks || []).some(
          track => track?.kind === "audio" && track?.readyState === "live" && track?.enabled !== false
        );
        result = {
          ...activation,
          ...activationStatus,
          ok: Boolean(activationStatus?.ok && activationStatus?.active),
          result: activationStatus?.active ? "voice-active" : String(activationStatus?.result || activation?.result || "voice-start-timeout"),
          focusResult,
          activationStatus,
          micLive,
        };
      } else if (name === "arm-mic-probe") {
        result = await query("armMicEventProbe", {}, 1200);
      } else if (name === "mic-probe-state") {
        result = await query("micEventProbeState", {}, 1200);
      } else if (name === "audio-track-state") {
        result = await query("audioTrackState", {}, 1200);
      } else if (name === "force-audio-tracks-on") {
        result = await query("forceAudioTracksOn", {}, 1600);
      } else if (name === "probe-mic-media") {
        result = await query("probeMicrophoneMedia", {}, 8000);
      } else if (name === "mic-permission") {
        result = await micPermissionState();
      } else if (name === "normal-media-environment") {
        result = await queryNormalSelected("mediaEnvironment", {}, 5000);
      } else if (name === "normal-probe-mic-media") {
        result = await queryNormalSelected("probeMicrophoneMedia", {}, 7000);
      } else if (name === "media-environment") {
        const { browser } = await ensureEngineWindow(2200);
        const actorState = await query("mediaEnvironment", {}, 5000);
        result = {
          ...actorState,
          browserDocShellIsActive: (() => { try { return Boolean(browser?.docShellIsActive); } catch (_) { return null; } })(),
          browsingContextIsActive: (() => { try { return Boolean(browser?.browsingContext?.isActive); } catch (_) { return null; } })(),
          remoteType: String(browser?.remoteType || ""),
        };
      } else if (name === "focus-engine-content") {
        result = await focusEngineContent();
      } else if (name === "focus-mic") {
        result = await query("focusMicControl", {}, 1200);
      } else if (name === "mic-on") {
        result = await query("ensureMicrophoneOn", {}, 2200);
      } else if (name === "send-text") {
        result = await query("sendText", { text: String(command.text || "") });
      } else if (name === "latest-response") {
        result = await query("latestAssistantResponse", {}, 1200);
      } else if (name === "read-aloud") {
        result = await query("readLatestAloud", {}, 1600);
      } else if (name === "paste-image") {
        result = await query("pasteImage", {
          base64: String(command.base64 || ""),
          mime: String(command.mime || "image/png"),
          name: String(command.name || "tabby-paste.png"),
        });
      } else if (name === "end") {
        result = await query("endVoice", {}, 1200);
        if (Boolean(command.reset)) await resetEngineToMarker();
        const win = findEngineWindow();
        setEngineVisible(win, Boolean(command.debug));
      } else if (name === "debug-mic") {
        result = await query("debugMicControl", {}, 1500);
      } else if (name === "debug-dom") {
        result = await query("debugComposer");
      } else if (name === "debug-all") {
        result = await query("debugAllControls");
      } else {
        result = await query("voiceStatus");
      }
      } catch (error) {
        result = { ok: false, result: "bridge-error", error: String(error), debugVisible };
        try { Services.prefs.setStringPref("qwqc.hey_tabby.runtime.last_poll_error", String(error)); } catch (_) {}
      }
      if (!abandonedSeq.delete(seq))
        await writeState(seq, name, result || { ok: false, result: "empty-result", debugVisible });
    }

    function commandWatchdogMs(name) {
      if (name === "new-chat") return 22000;
      if (name === "open-chat" || name === "worker-open") return 18000;
      if (name === "continue-chat") return 9500;
      if (name === "activate") return 8500;
      if (name === "probe-mic-media" || name === "normal-probe-mic-media") return 11000;
      return 10000;
    }

    async function poll() {
      if (destroyed || pollBusy || readOwner() !== controllerToken) return;
      pollBusy = true;
      try {
        const now = Date.now();
        Services.prefs.setStringPref("qwqc.hey_tabby.runtime.last_poll_ms", String(now));
        if (readOwner() === controllerToken)
          Services.prefs.setStringPref(HEARTBEAT_PREF, String(now));
        if (await IOUtils.exists(COMMAND_PATH)) {
          const command = await IOUtils.readJSON(COMMAND_PATH);
          const seq = Number(command?.seq ?? -1);
          const name = String(command?.command || "status");
          try {
            Services.prefs.setStringPref("qwqc.hey_tabby.runtime.current_command", name);
            Services.prefs.setStringPref("qwqc.hey_tabby.runtime.current_command_started_ms", String(Date.now()));
          } catch (_) {}
          const timeoutMs = commandWatchdogMs(name);
          const outcome = await Promise.race([
            handleCommand(command).then(() => "done"),
            sleep(timeoutMs).then(() => "timeout"),
          ]);
          if (outcome === "timeout") {
            abandonedSeq.add(seq);
            await writeState(seq, name, {
              ok:false, result:"command-watchdog-timeout", active:false, debugVisible, timeoutMs,
            });
          }
          try {
            Services.prefs.setStringPref("qwqc.hey_tabby.runtime.current_command", "");
            Services.prefs.setStringPref("qwqc.hey_tabby.runtime.current_command_started_ms", "0");
          } catch (_) {}
        }
      } catch (error) {
        try { Services.prefs.setStringPref("qwqc.hey_tabby.runtime.last_poll_error", String(error)); } catch (_) {}
        log("poll", error);
      } finally {
        pollBusy = false;
      }
    }

    async function init() {
      ensureActorRegistration();
      grantMicPermission();
      Services.prefs.setBoolPref("qwqc.hey_tabby.runtime.loaded", true);
      Services.prefs.setStringPref("qwqc.hey_tabby.runtime.version", VERSION);
      try { Services.prefs.setStringPref("qwqc.hey_tabby.runtime.last_poll_error", ""); } catch (_) {}
      try { Services.prefs.setStringPref("qwqc.hey_tabby.runtime.bootstrap_error", ""); } catch (_) {}
      try { await IOUtils.makeDirectory(PathUtils.dirname(COMMAND_PATH), { ignoreExisting: true }); } catch (_) {}
      try {
        if (await IOUtils.exists(COMMAND_PATH)) {
          const stale = await IOUtils.readJSON(COMMAND_PATH);
          lastSeq = Number(stale?.seq ?? -1);
        }
      } catch (_) {}

      // v0.10 uses a real browser window so Gecko/WebRTC gives it the same
      // microphone path as a normal Zen tab. Close only legacy XUL engine
      // windows; never remove the native Tabby browser window/tab here.
      try {
        const legacy = Services.wm.getEnumerator("qwqc:tabby-engine");
        while (legacy.hasMoreElements()) {
          const oldWin = legacy.getNext();
          try { oldWin.close(); } catch (_) {}
        }
      } catch (_) {}

      Services.prefs.setStringPref(OWNER_PREF, controllerToken);
      Services.prefs.setStringPref(HEARTBEAT_PREF, String(Date.now()));
      heartbeatTimer = makeTimer(() => {
        try {
          if (!destroyed && readOwner() === controllerToken)
            Services.prefs.setStringPref(HEARTBEAT_PREF, String(Date.now()));
        } catch (_) {}
      }, 500, true);
      timer = makeTimer(() => { poll(); }, 150, true);
      await writeState(0, "init", {
        ok: true, result: "ready", active: false, debugVisible: false,
        standaloneEngineWindow: true,
      });
      log("initialized", VERSION);
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      if (timer) { try { timer.cancel(); } catch (_) {} }
      if (heartbeatTimer) { try { heartbeatTimer.cancel(); } catch (_) {} }
      if (actorRegisteredHere) {
        try { ChromeUtils.unregisterWindowActor(ACTOR_NAME); } catch (_) {}
      }
      if (readOwner() === controllerToken) {
        Services.prefs.setBoolPref("qwqc.hey_tabby.runtime.loaded", false);
        try { Services.prefs.setStringPref(HEARTBEAT_PREF, "0"); } catch (_) {}
        try { Services.prefs.setStringPref(OWNER_PREF, ""); } catch (_) {}
      }
    }

    init().catch(error => {
      try { Services.prefs.setStringPref("qwqc.hey_tabby.runtime.bootstrap_error", String(error)); } catch (_) {}
      console.error("[Tabby Engine] init failed", error);
    });
    return { version: VERSION, destroy, token: controllerToken };
  }

  const readRuntimeNumber = (name) => {
    try { return Number(Services.prefs.getStringPref(name, "0")); } catch (_) { return 0; }
  };
  const readRuntimeString = (name) => {
    try { return Services.prefs.getStringPref(name, ""); } catch (_) { return ""; }
  };
  const stalledCommandLimit = (name) => {
    if (name === "continue-chat") return 12000;
    if (name === "activate") return 12000;
    if (name === "new-chat") return 27000;
    if (name === "open-chat" || name === "worker-open") return 23000;
    return 15000;
  };
  const controllerPollStalled = now => {
    const current = readRuntimeString("qwqc.hey_tabby.runtime.current_command");
    const started = readRuntimeNumber("qwqc.hey_tabby.runtime.current_command_started_ms");
    const lastPoll = readRuntimeNumber("qwqc.hey_tabby.runtime.last_poll_ms");
    if (current && started > 0 && now - started > stalledCommandLimit(current)) return true;
    return lastPoll > 0 && now - lastPoll > 18000;
  };

  const tryTakeover = () => {
    try {
      const now = Date.now();
      const heartbeat = readHeartbeat();
      const pollStalled = controllerPollStalled(now);
      if (now - heartbeat < TAKEOVER_AFTER_MS && !pollStalled) return false;
      // Firefox chrome-window callbacks run on the parent main thread. Tear
      // down this window's stale controller first, then atomically claim the
      // shared owner token. Clear stale command diagnostics so another window
      // does not immediately race a second takeover in the same tick.
      if (localController) {
        try { localController.destroy(); } catch (_) {}
      }
      Services.prefs.setStringPref(OWNER_PREF, ownerToken);
      Services.prefs.setStringPref(HEARTBEAT_PREF, String(now));
      Services.prefs.setStringPref("qwqc.hey_tabby.runtime.last_poll_ms", String(now));
      Services.prefs.setStringPref("qwqc.hey_tabby.runtime.current_command", "");
      Services.prefs.setStringPref("qwqc.hey_tabby.runtime.current_command_started_ms", "0");
      localController = createController(ownerToken);
      return true;
    } catch (error) {
      try { Services.prefs.setStringPref("qwqc.hey_tabby.runtime.bootstrap_error", String(error)); } catch (_) {}
      console.error("[Tabby Engine] takeover failed", error);
      return false;
    }
  };

  const start = () => {
    tryTakeover();
    takeoverTimer = makeTimer(() => {
      // Also check command/poll liveness: the heartbeat can remain fresh while
      // a JSWindowActor request has wedged the controller's command loop.
      tryTakeover();
    }, 1000, true);
  };

  window.addEventListener("unload", () => {
    if (takeoverTimer) { try { takeoverTimer.cancel(); } catch (_) {} }
    if (localController) { try { localController.destroy(); } catch (_) {} }
  }, { once: true });

  if (document.readyState === "complete") start();
  else window.addEventListener("load", start, { once: true });
})();
