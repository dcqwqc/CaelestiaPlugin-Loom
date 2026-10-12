import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import * as z from "zod/v4";
import { publicError } from "./errors.js";

export async function handleMcp(req, res, body, router) {
  const server = new McpServer({ name: "loom-philipedia-router", version: "0.1.0" });
  registerTools(server, router);
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, body);
  } finally {
    res.once("close", () => { transport.close().catch(() => {}); server.close().catch(() => {}); });
  }
}

function registerTools(server, router) {
  server.registerTool("loom_router_status", {
    description: "Read-only status for the dedicated Philipedia ChatGPT router profile.",
    inputSchema: {}, annotations: { readOnlyHint: true, openWorldHint: true },
  }, () => toolResult(() => router.status()));

  server.registerTool("loom_origin_resolve", {
    description: "Resolve 1-3 exact recent user messages to one unique canonical conversation in the dedicated profile. Never sends or routes.",
    inputSchema: { origin_messages: z.array(z.string()).min(1).max(3) },
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, ({ origin_messages }) => toolResult(() => router.originResolve(origin_messages)));

  server.registerTool("loom_chat_route", {
    description: "Move only the exact canonical ChatGPT conversation URL into the named lifecycle project and verify both IDs from the resulting route. Never sends messages.",
    inputSchema: {
      url: z.string().url(),
      lifecycle: z.enum(["new", "working", "review", "blocked", "vault", "done"]),
    }, annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  }, args => toolResult(() => router.route(args)));
}

async function toolResult(fn) {
  try {
    const result = await fn();
    return { content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result };
  } catch (error) {
    const result = publicError(error);
    return { isError: true, content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result };
  }
}
