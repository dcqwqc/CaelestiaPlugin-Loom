// Loads the real Zen controller script (.hey-tabby.uc.js) into a vm sandbox
// with minimal Firefox chrome globals, then drives it through its command
// file exactly as tabby/zen.py does. A fake ChatGPT "server" stands behind
// the hidden worker windows' actors.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../bridge/zen/.hey-tabby.uc.js", import.meta.url), "utf8");
const NEW = "g-p-" + "1".repeat(32);
const WORKING = "g-p-" + "3".repeat(32);

function harness() {
  const files = new Map();
  const prefs = new Map();
  const timers = [];
  const engineWindows = [];
  const server = { chats: new Map(), sends: [], loads: [], guarded: 0,
    projects: [{ id: NEW, segment: `${NEW}-new`, name: "New" }, { id: WORKING, segment: `${WORKING}-working`, name: "Working" }] };

  const core = seg => (String(seg).match(/^(g-p-[0-9a-f]{32})/i) || [])[1] || String(seg);
  const segFor = id => server.projects.find(p => p.id === id)?.segment || id;
  const chatHref = conv => {
    const project = server.chats.get(conv).project;
    return project ? `https://chatgpt.com/g/${segFor(project)}/c/${conv}` : `https://chatgpt.com/c/${conv}`;
  };

  function makePage() {
    const page = { href: "about:blank" };
    page.actor = {
      async sendQuery(name, data = {}) {
        const path = new URL(page.href).pathname.split("/").filter(Boolean);
        const conv = path.length === 4 ? path[3] : path.length === 2 && path[0] === "c" ? path[1] : "";
        if (name === "voiceStatus") return { ok: true, href: page.href, composerReady: true, working: false };
        if (name === "discoverProjects") return { ok: true, projects: server.projects };
        if (name === "openProject") {
          const hits = server.projects.filter(p => p.name.toLowerCase() === String(data.name).toLowerCase());
          if (hits.length !== 1) return { ok: false, result: hits.length ? "project-control-ambiguous" : "project-control-not-found" };
          const previousUrl = page.href;
          page.href = `https://chatgpt.com/g/${hits[0].segment}/project`;
          return { ok: true, result: "project-open-requested", via: "new-chat-control", previousUrl };
        }
        if (name === "sendText") {
          server.sends.push(data.text);
          if (path.length === 3 && path[2] === "project") {
            const id = `conv-${server.chats.size + 1}`;
            server.chats.set(id, { project: core(path[1]), users: [data.text] });
            page.href = chatHref(id);
          } else if (conv) server.chats.get(conv).users.push(data.text);
          return { ok: true, result: "sent" };
        }
        if (name === "moveToProject") {
          if (data.conversationId && data.conversationId !== conv) return { ok: false, result: "conversation-mismatch" };
          server.chats.get(conv).project = data.projectId;
          page.href = chatHref(conv);
          return { ok: true, result: "project-move-verified", projectId: data.projectId, conversationId: conv };
        }
        if (name === "sendPromptGuarded") {
          server.guarded++;
          const chat = server.chats.get(conv);
          if (!chat || conv !== data.conversationId || chat.project !== data.projectId)
            return { ok: false, sent: false, result: "route-mismatch" };
          if (data.expectedUserCount >= 0 && chat.users.length !== data.expectedUserCount)
            return { ok: false, sent: false, result: "user-count-mismatch" };
          chat.users.push(data.text);
          server.sends.push(data.text);
          return { ok: true, sent: true, result: "prompt-sent" };
        }
        if (name === "conversationTurns") {
          const users = server.chats.get(conv)?.users || [];
          return { ok: true, userCount: users.length, lastUserText: users.at(-1) || "" };
        }
        return { ok: false, result: `unhandled ${name}` };
      },
    };
    const browser = {
      get currentURI() { return { spec: page.href }; },
      loadURI(uri) {
        server.loads.push(uri.spec);
        // Unknown project pages bounce to the home page, like ChatGPT does.
        const parts = new URL(uri.spec).pathname.split("/").filter(Boolean);
        page.href = parts[2] === "project" && !server.projects.some(p => p.id === core(parts[1]))
          ? "https://chatgpt.com/" : uri.spec;
      },
      browsingContext: { currentWindowGlobal: {
        get documentURI() { return { spec: page.href }; },
        getActor: () => page.actor,
      } },
    };
    return { page, browser };
  }

  const normalTabs = [];
  const host = {
    closed: false,
    gBrowser: {
      get tabs() { return normalTabs.map(linkedBrowser => ({linkedBrowser})); },
      get selectedBrowser() { return normalTabs[0] || null; },
    },
    openDialog() {
      const { page, browser } = makePage();
      const win = { closed: false, page, windowState: 1,
        document: { readyState: "complete", title: "", getElementById: id => (id === "tabby-browser" ? browser : null) },
        close() { this.closed = true; } };
      engineWindows.push(win);
      return win;
    },
  };
  // The controller races every actor query against a long sleep; unref those
  // timers so a finished test does not keep node alive until they expire.
  const lazyTimeout = (cb, ms) => { const t = setTimeout(cb, ms); t.unref?.(); return t; };
  const enumerator = list => { let i = 0; return { hasMoreElements: () => i < list.length, getNext: () => list[i++] }; };
  const sandbox = {
    console: { debug() {}, error() {}, log() {} },
    setTimeout: lazyTimeout, clearTimeout, URL, Date, JSON, Math, Promise, Number, String, Set, Map, Error,
    encodeURIComponent,
    window: { windowUtils: { outerWindowID: 7 }, setTimeout: lazyTimeout, addEventListener() {} },
    document: { readyState: "complete" },
    Cc: { "@mozilla.org/timer;1": { createInstance: () => ({
      initWithCallback(cb, delay, type) { this.cb = cb; this.delay = delay; this.type = type; timers.push(this); },
      cancel() { this.cancelled = true; } }) } },
    Ci: { nsITimer: { TYPE_REPEATING_SLACK: 1, TYPE_ONE_SHOT: 0 }, nsIPermissionManager: { ALLOW_ACTION: 1, EXPIRE_NEVER: 0 } },
    ChromeUtils: { registerWindowActor() {}, unregisterWindowActor() {}, importESModule() { throw new Error("no"); } },
    PathUtils: { profileDir: "/profile", join: (...a) => a.join("/"), dirname: p => p.split("/").slice(0, -1).join("/") },
    IOUtils: {
      exists: async p => files.has(p),
      readJSON: async p => JSON.parse(files.get(p)),
      writeJSON: async (p, v) => { files.set(p, JSON.stringify(v)); },
      makeDirectory: async () => {},
    },
    Services: {
      prefs: {
        getStringPref: (k, d = "") => (prefs.has(k) ? prefs.get(k) : d),
        setStringPref: (k, v) => prefs.set(k, String(v)),
        setBoolPref: (k, v) => prefs.set(k, v),
      },
      wm: { getEnumerator: type => enumerator(type === "navigator:browser" ? [host] : engineWindows.filter(w => !w.closed)) },
      io: { newURI: spec => ({ spec }) },
      scriptSecurityManager: { getSystemPrincipal: () => ({}), createContentPrincipal: () => ({ origin: "https://chatgpt.com", originAttributes: {} }) },
      perms: { addFromPrincipal() {}, testPermissionFromPrincipal: () => 1 },
      focus: { activeWindow: null },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);

  let seq = 1000;
  async function ready() {
    for (let i = 0; i < 200 && !timers.some(t => t.delay === 150); i++) await new Promise(r => setTimeout(r, 5));
    assert.ok(timers.some(t => t.delay === 150), "controller poll timer never started");
  }
  async function send(command, extra = {}) {
    const mySeq = ++seq;
    files.set("/profile/tabby-bridge-command.json", JSON.stringify({ seq: mySeq, command, ...extra }));
    timers.find(t => t.delay === 150 && !t.cancelled).cb();
    for (let i = 0; i < 2000; i++) {
      const raw = files.get("/profile/tabby-bridge-state.json");
      const state = raw ? JSON.parse(raw) : {};
      if (state.seq === mySeq) return state;
      await new Promise(r => setTimeout(r, 5));
    }
    throw new Error(`no reply for ${command}`);
  }
  function addNormalTab(url, userMessages, opts = {}) {
    const browser = {
      currentURI: {spec:url},
      browsingContext: opts.unavailable ? null : {currentWindowGlobal: {
        getActor: () => ({sendQuery: async name => name === "originSnapshot"
          ? {ok:true,href:opts.actorHref || url,userMessages}
          : {ok:false,result:"unhandled"}}),
      }},
    };
    normalTabs.push(browser);
    return browser;
  }
  return { send, ready, server, engineWindows, files, addNormalTab };
}

test("controller reports the move-first bridge version", async () => {
  const h = harness();
  await h.ready();
  const state = JSON.parse(h.files.get("/profile/tabby-bridge-state.json"));
  assert.equal(state.version, "0.18.3");
  assert.equal(state.bridgeLoaded, true);
});

test("prepare opens a hidden blank composer and lists projects without sending", async () => {
  const h = harness();
  await h.ready();
  const result = await h.send("worker-prepare", { taskId: "t1" });
  assert.equal(result.ok, true);
  assert.equal(result.href, "");
  assert.deepEqual(result.projects.map(p => p.name), ["New", "Working"]);
  assert.deepEqual(h.server.loads, ["https://chatgpt.com/?loom-worker=1"]);
  assert.deepEqual(h.server.sends, []);
  assert.equal(h.engineWindows[0].document.title, "Loom Work · t1");
});

test("bootstrap creates the chat inside the New project's own composer", async () => {
  const h = harness();
  await h.ready();
  await h.send("worker-prepare", { taskId: "t1" });
  const result = await h.send("worker-bootstrap", { taskId: "t1", projectSegment: `${NEW}-new`, projectId: NEW, text: "setup only" });
  assert.equal(result.ok, true);
  assert.equal(result.result, "bootstrap-created");
  assert.equal(result.route.projectId, NEW);
  assert.equal(result.href, `https://chatgpt.com/g/${NEW}-new/c/conv-1`);
  assert.deepEqual(h.server.sends, ["setup only"]);
  assert.ok(h.server.loads.includes(`https://chatgpt.com/g/${NEW}-new/project`));
});

test("bootstrap after an interrupted run adopts the existing chat and never sends again", async () => {
  const h = harness();
  await h.ready();
  await h.send("worker-prepare", { taskId: "t1" });
  const args = { taskId: "t1", projectSegment: `${NEW}-new`, projectId: NEW, text: "setup only" };
  await h.send("worker-bootstrap", args);
  const loads = h.server.loads.length;
  const again = await h.send("worker-bootstrap", args);
  assert.equal(again.result, "bootstrap-existing");
  assert.equal(again.href, `https://chatgpt.com/g/${NEW}-new/c/conv-1`);
  assert.deepEqual(h.server.sends, ["setup only"]);
  assert.equal(h.server.loads.length, loads);
  const prepared = await h.send("worker-prepare", { taskId: "t1" });
  assert.equal(prepared.result, "worker-existing-chat");
});

test("bootstrap never navigates a worker window whose page cannot be read", async () => {
  const h = harness();
  await h.ready();
  await h.send("worker-prepare", { taskId: "t1" });
  const win = h.engineWindows[0];
  const browser = win.document.getElementById("tabby-browser");
  browser.browsingContext.currentWindowGlobal.getActor = () => null;  // actor not attached
  const loads = h.server.loads.length;
  const result = await h.send("worker-bootstrap", { taskId: "t1", projectSegment: `${NEW}-new`, projectId: NEW, text: "x" });
  assert.equal(result.result, "worker-existing-unverified");
  assert.equal(result.sent, false);
  assert.equal(h.server.loads.length, loads);
  assert.deepEqual(h.server.sends, []);
});

test("bootstrap refuses path-like project segments before opening anything", async () => {
  const h = harness();
  await h.ready();
  const result = await h.send("worker-bootstrap", { taskId: "t1", projectSegment: "../c/x", projectId: NEW, text: "x" });
  assert.equal(result.result, "missing-bootstrap-identity");
  assert.equal(result.sent, false);
  assert.equal(h.engineWindows.length, 0);
});

test("bootstrap does not type into a page that is not the requested project", { timeout: 20000 }, async () => {
  const h = harness();
  await h.ready();
  await h.send("worker-prepare", { taskId: "t1" });
  const missing = "g-p-" + "9".repeat(32);
  const result = await h.send("worker-bootstrap", { taskId: "t1", projectSegment: missing, projectId: missing, text: "x" });
  assert.equal(result.result, "project-page-unverified");
  assert.equal(result.sent, false);
  assert.deepEqual(h.server.sends, []);
});

test("move, turns and guarded send flow; a repeated send key is suppressed", async () => {
  const h = harness();
  await h.ready();
  await h.send("worker-prepare", { taskId: "t1" });
  await h.send("worker-bootstrap", { taskId: "t1", projectSegment: `${NEW}-new`, projectId: NEW, text: "setup" });
  const moved = await h.send("worker-move-project", { taskId: "t1", projectId: WORKING, projectName: "Working", conversationId: "conv-1" });
  assert.equal(moved.ok, true);
  assert.equal(moved.conversationId, "conv-1");
  const turns = await h.send("worker-turns", { taskId: "t1" });
  assert.equal(turns.userCount, 1);
  const args = { taskId: "t1", conversationId: "conv-1", projectId: WORKING, prompt: "real task",
    sendKey: "t1:1", expectedUserCount: 1 };
  const sent = await h.send("worker-send-prompt", args);
  assert.equal(sent.sent, true);
  const duplicate = await h.send("worker-send-prompt", args);
  assert.equal(duplicate.result, "duplicate-send-suppressed");
  assert.equal(duplicate.sent, "unknown");
  assert.equal(h.server.sends.filter(t => t === "real task").length, 1);
  assert.equal(h.server.guarded, 1);
});

test("a definite pre-click refusal frees the send key for a later attempt", async () => {
  const h = harness();
  await h.ready();
  await h.send("worker-prepare", { taskId: "t1" });
  await h.send("worker-bootstrap", { taskId: "t1", projectSegment: `${NEW}-new`, projectId: NEW, text: "setup" });
  const args = { taskId: "t1", conversationId: "conv-1", projectId: WORKING, prompt: "real task",
    sendKey: "t1:1", expectedUserCount: 1 };
  const early = await h.send("worker-send-prompt", args);  // still in New: refused
  assert.equal(early.sent, false);
  assert.equal(early.result, "route-mismatch");
  await h.send("worker-move-project", { taskId: "t1", projectId: WORKING, projectName: "Working", conversationId: "conv-1" });
  const later = await h.send("worker-send-prompt", args);
  assert.equal(later.sent, true);
  assert.equal(h.server.sends.filter(t => t === "real task").length, 1);
});

test("send without a key is refused", async () => {
  const h = harness();
  await h.ready();
  const result = await h.send("worker-send-prompt", { taskId: "t1", conversationId: "c", projectId: WORKING, prompt: "x" });
  assert.equal(result.result, "missing-send-key");
  assert.equal(h.server.guarded, 0);
});

test("routing an existing chat by URL uses its own hidden window", async () => {
  const h = harness();
  await h.ready();
  h.server.chats.set("user-chat", { project: "", users: ["hello"] });
  const opened = await h.send("worker-open", { taskId: "route-abc", url: "https://chatgpt.com/c/user-chat" });
  assert.equal(opened.ok, true);
  const moved = await h.send("worker-move-project", { taskId: "route-abc", projectId: WORKING, projectName: "Working", conversationId: "user-chat" });
  assert.equal(moved.ok, true);
  assert.equal(h.server.chats.get("user-chat").project, WORKING);
  const closed = await h.send("worker-close", { taskId: "route-abc" });
  assert.equal(closed.ok, true);
  assert.equal(h.engineWindows.every(w => w.closed), true);
});

test("resolve learns exact ids from project routes and reports unknown names", async () => {
  const h = harness();
  await h.ready();
  await h.send("worker-prepare", { taskId: "projects-x" });
  const result = await h.send("worker-resolve-projects", { taskId: "projects-x", names: ["new", "WORKING", "vault"] });
  assert.equal(result.ok, true);
  assert.deepEqual(result.projects.map(p => [p.name, p.id, p.segment]),
    [["new", NEW, `${NEW}-new`], ["WORKING", WORKING, `${WORKING}-working`]]);
  assert.deepEqual(result.errors, [{ name: "vault", result: "project-control-not-found" }]);
  assert.deepEqual(h.server.sends, []);
});

test("resolve refuses to navigate a window that shows a chat", async () => {
  const h = harness();
  await h.ready();
  h.server.chats.set("user-chat", { project: "", users: ["hello"] });
  await h.send("worker-open", { taskId: "t1", url: "https://chatgpt.com/c/user-chat" });
  const result = await h.send("worker-resolve-projects", { taskId: "t1", names: ["new"] });
  assert.equal(result.result, "refusing-to-navigate-chat-window");
  assert.equal(h.engineWindows[0].page.href, "https://chatgpt.com/c/user-chat");
});


test("unique exact recent user text resolves a normal Zen chat without navigating",async()=>{
  const h=harness(); await h.ready();
  h.addNormalTab("https://chatgpt.com/c/a-unique", ["Earlier context about the software", "Please fix the automatic routing in this conversation"]);
  h.addNormalTab("https://chatgpt.com/c/other", ["Completely different text"]);
  const result=await h.send("chat-origin-resolve",{messages:["Please fix the automatic routing in this conversation"]});
  assert.equal(result.ok,true);
  assert.equal(result.url,"https://chatgpt.com/c/a-unique");
  assert.equal(result.conversationId,"a-unique");
  assert.equal(h.engineWindows.length,0);
  assert.deepEqual(h.server.sends,[]);
});

test("short newest user message is resolvable with exact prior user turn",async()=>{
  const h=harness(); await h.ready();
  h.addNormalTab("https://chatgpt.com/c/doall", ["Implement the precise origin discovery without guessing", "do all"]);
  const result=await h.send("chat-origin-resolve",{messages:["Implement the precise origin discovery without guessing","do all"]});
  assert.equal(result.url,"https://chatgpt.com/c/doall");
});

test("ambiguous and generic origins fail closed",async()=>{
  const h=harness(); await h.ready();
  const msg="Please fix the automatic routing in this conversation";
  h.addNormalTab("https://chatgpt.com/c/a",[msg]);
  h.addNormalTab("https://chatgpt.com/c/b",[msg]);
  assert.equal((await h.send("chat-origin-resolve",{messages:[msg]})).result,"origin-ambiguous");
  assert.equal((await h.send("chat-origin-resolve",{messages:["do all"]})).result,"origin-fingerprint-too-weak");
  assert.equal(h.engineWindows.length,0);
});

test("unavailable or inconsistent normal tab identity prevents routing",async()=>{
  const h=harness(); await h.ready();
  h.addNormalTab("https://chatgpt.com/c/a", ["Exact user message identifying the correct conversation"]);
  h.addNormalTab("https://chatgpt.com/c/b", ["different"], {actorHref:"https://chatgpt.com/c/another"});
  assert.equal((await h.send("chat-origin-resolve",{messages:["Exact user message identifying the correct conversation"]})).result,"origin-tabs-unavailable");
});

test("background tab is discoverable and non-chat tabs ignored",async()=>{
  const h=harness(); await h.ready();
  const msg="Find the origin of the external chat without touching Voice";
  h.addNormalTab("https://chatgpt.com/",[msg]);
  h.addNormalTab("https://chatgpt.com/c/background",[msg]);
  const result=await h.send("chat-origin-resolve",{messages:[msg]});
  assert.equal(result.url,"https://chatgpt.com/c/background");
});
