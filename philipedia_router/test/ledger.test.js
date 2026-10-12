import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, stat, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { FileLedger } from "../src/ledger.js";

test("verified review state survives process reconstruction in a private atomic ledger", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "loom-ledger-"));
  try {
    const file = path.join(root, "routes.json");
    const first = new FileLedger(file);
    assert.equal(await first.get("conversation-one"), null);
    await first.put("conversation-one", { lifecycle: "review", projectId: "g-p-123" });
    const second = new FileLedger(file);
    assert.equal((await second.get("conversation-one")).lifecycle, "review");
    assert.equal((await second.get("conversation-one")).projectId, "g-p-123");
    assert.equal((await stat(file)).mode & 0o077, 0);
    assert.ok((await readFile(file, "utf8")).includes("conversation-one"));
  } finally { await rm(root, { recursive: true, force: true }); }
});
