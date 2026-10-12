import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { loadConfig } from "./config.js";
import { publicError, RouterError } from "./errors.js";
import { handleMcp } from "./mcp.js";
import { PlaywrightBrowser } from "./playwright-browser.js";
import { Router } from "./router.js";

const config = await loadConfig();
const browser = new PlaywrightBrowser(config);
const router = new Router({ browser, projects: config.projects });
const server = createServer(async (req, res) => {
  try {
    if (req.url === "/healthz" && req.method === "GET") return json(res, 200, { ok: true, service: "philipedia-router" });
    if (!authorized(req, config.token)) return json(res, 401, { ok: false, error: "unauthorized" }, { "www-authenticate": "Bearer" });
    if (req.url === "/v1/status" && req.method === "GET") return json(res, 200, await router.status());
    const body = await readJson(req);
    if (req.url === "/v1/origin/resolve" && req.method === "POST") return json(res, 200, await router.originResolve(body.origin_messages));
    if (req.url === "/v1/route" && req.method === "POST") return json(res, 200, await router.route(body));
    if (req.url === "/mcp" && req.method === "POST") return await handleMcp(req, res, body, router);
    return json(res, 404, { ok: false, error: "not-found" });
  } catch (error) {
    const status = error instanceof RouterError ? 400 : 500;
    if (!(error instanceof RouterError)) console.error("router request failed", error);
    if (!res.headersSent) json(res, status, publicError(error));
  }
});

server.listen(config.port, config.host, () => console.log(`Philipedia router listening on http://${config.host}:${config.port}`));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, async () => {
  server.close();
  await browser.close();
  process.exit(0);
});

function authorized(req, expected) {
  const supplied = String(req.headers.authorization || "").match(/^Bearer (.+)$/)?.[1] || "";
  const a = Buffer.from(supplied), b = Buffer.from(expected);
  return a.length === b.length && a.length > 0 && timingSafeEqual(a, b);
}

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1_000_000) throw new RouterError("request-too-large", "Request body exceeds 1 MB");
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw new RouterError("invalid-json", "Request body must be valid JSON"); }
}

function json(res, status, value, headers = {}) {
  const body = JSON.stringify(value);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), ...headers });
  res.end(body);
}
