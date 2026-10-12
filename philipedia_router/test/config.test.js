import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { loadConfig } from "../src/config.js";

test("config refuses non-loopback listeners and duplicate lifecycle targets", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "loom-router-test-"));
  const token = path.join(dir, "token");
  const projectFile = path.join(dir, "projects.json");
  await writeFile(token, "a".repeat(40)); await chmod(token, 0o600);
  const base = { LOOM_ROUTER_PROFILE: path.join(dir, "profile"), LOOM_ROUTER_TOKEN_FILE: token, LOOM_ROUTER_PROJECTS_FILE: projectFile };
  await writeFile(projectFile, JSON.stringify({ projects: { new: "New", working: "Same", review: "Same", blocked: "Blocked", vault: "Vault", done: "Done" } }));
  await assert.rejects(loadConfig(base), error => error.code === "duplicate-project-target");
  await writeFile(projectFile, JSON.stringify({ projects: { new: "New", working: "Working", review: "Review", blocked: "Blocked", vault: "Vault", done: "Done" } }));
  await assert.rejects(loadConfig({ ...base, LOOM_ROUTER_HOST: "0.0.0.0" }), error => error.code === "unsafe-listen-address");
});
