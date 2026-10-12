import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { RouterError } from "./errors.js";

export const LIFECYCLES = ["new", "working", "review", "blocked", "vault", "done"];

export async function loadConfig(env = process.env) {
  const profilePath = requiredAbsolute(env.LOOM_ROUTER_PROFILE, "LOOM_ROUTER_PROFILE");
  const tokenFile = requiredAbsolute(env.LOOM_ROUTER_TOKEN_FILE, "LOOM_ROUTER_TOKEN_FILE");
  const projectFile = requiredAbsolute(env.LOOM_ROUTER_PROJECTS_FILE, "LOOM_ROUTER_PROJECTS_FILE");
  const host = env.LOOM_ROUTER_HOST || "127.0.0.1";
  if (!["127.0.0.1", "::1", "localhost"].includes(host)) {
    throw new RouterError("unsafe-listen-address", "Router must listen on loopback; use an authenticated secure tunnel for remote clients");
  }
  const port = Number(env.LOOM_ROUTER_PORT || 4319);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new RouterError("invalid-port", "LOOM_ROUTER_PORT is invalid");
  const tokenStat = await stat(tokenFile);
  if (!tokenStat.isFile() || (tokenStat.mode & 0o077) !== 0) throw new RouterError("unsafe-token-file", "Token file must be a regular file with mode 0600 or stricter");
  const token = (await readFile(tokenFile, "utf8")).trim();
  if (token.length < 32) throw new RouterError("invalid-token", "Bearer token must contain at least 32 characters");
  const raw = JSON.parse(await readFile(projectFile, "utf8"));
  const projects = Object.fromEntries(LIFECYCLES.map(key => [key, String(raw?.projects?.[key] ?? "").replace(/\s+/g, " ").trim()]));
  if (Object.values(projects).some(name => !name || name.length > 160)) throw new RouterError("invalid-project-config", "All six lifecycle project names are required");
  const folded = Object.values(projects).map(name => name.toLocaleLowerCase());
  if (new Set(folded).size !== folded.length) throw new RouterError("duplicate-project-target", "Lifecycle project names must be unique");
  return { profilePath, token, projects, host, port, headless: env.LOOM_ROUTER_HEADLESS !== "false" };
}

function requiredAbsolute(value, name) {
  if (!value || !path.isAbsolute(value)) throw new RouterError("invalid-config", `${name} must be an explicit absolute path`);
  return path.resolve(value);
}
