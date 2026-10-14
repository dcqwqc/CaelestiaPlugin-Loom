import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
const bridge = readFileSync(new URL("../bridge/zen/.hey-tabby.uc.js",import.meta.url),"utf8");
const client = readFileSync(new URL("../tabby/zen.py",import.meta.url),"utf8");
test("main-engine diagnostics read real conversation turns and prompt acknowledgement",()=>{
  assert.match(bridge,/name === "main-turns"[\s\S]*?query\("conversationTurns", \{\}, 3500\)/);
  assert.match(bridge,/name === "main-prompt-status"[\s\S]*?query\("promptSubmissionState"/);
  assert.match(client,/'main-turns', 'main-prompt-status'/);
});
