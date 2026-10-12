import { readFile, writeFile, rename, mkdir, unlink } from "node:fs/promises";
import { dirname } from "node:path";

export class FileLedger {
  constructor(file) { this.file = file; }
  async get(conversationId) {
    const records = await this.#read();
    return records[conversationId] ?? null;
  }
  async put(conversationId, record) {
    const records = await this.#read();
    records[conversationId] = { ...record, updatedAt: new Date().toISOString() };
    await mkdir(dirname(this.file), { recursive: true, mode: 0o700 });
    const tmp = `${this.file}.${process.pid}.${Date.now()}.tmp`;
    try {
      await writeFile(tmp, JSON.stringify(records, null, 2) + "\n", { flag: "wx", mode: 0o600 });
      await rename(tmp, this.file);
    } finally { await unlink(tmp).catch(() => {}); }
    return records[conversationId];
  }
  async #read() {
    try {
      const content = JSON.parse(await readFile(this.file, "utf8"));
      if (!content || Array.isArray(content) || typeof content !== "object") throw new Error("Invalid router ledger");
      return content;
    } catch (error) {
      if (error.code === "ENOENT") return {};
      throw error;
    }
  }
}

export class MemoryLedger {
  constructor() { this.records = new Map(); }
  async get(id) { return this.records.get(id) ?? null; }
  async put(id, record) {
    const next = { ...record, updatedAt: new Date().toISOString() };
    this.records.set(id, next);
    return next;
  }
}
