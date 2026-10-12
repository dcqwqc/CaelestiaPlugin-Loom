import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { handleMcp } from "../src/mcp.js";

test("HTTP Streamable MCP lists and invokes the three router tools", async () => {
  const router = {
    async status() { return { ok: true, loggedIn: false }; },
    async originResolve() { throw new Error("not invoked"); },
    async route() { throw new Error("not invoked"); },
  };
  const http = createServer(async (req, res) => {
    if (req.method !== "POST") { res.writeHead(405).end(); return; }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    await handleMcp(req, res, JSON.parse(Buffer.concat(chunks).toString("utf8")), router);
  });
  await new Promise(resolve => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  const transport = new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${address.port}/mcp`));
  const client = new Client({ name: "router-test", version: "1" });
  try {
    await client.connect(transport);
    const tools = await client.listTools();
    assert.deepEqual(tools.tools.map(tool => tool.name).sort(), ["loom_chat_route", "loom_origin_resolve", "loom_router_status"]);
    const result = await client.callTool({ name: "loom_router_status", arguments: {} });
    assert.equal(JSON.parse(result.content[0].text).loggedIn, false);
  } finally {
    await client.close();
    await new Promise(resolve => http.close(resolve));
  }
});
