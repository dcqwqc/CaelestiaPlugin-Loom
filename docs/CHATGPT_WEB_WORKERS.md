# ChatGPT web workers

Loom can create a background ChatGPT web conversation through the bundled Zen controller without selecting or restarting the user's browser. The public MCP lifecycle is:

1. `loom_web_worker_create` reserves a durable record before browser I/O. `request_id` is mandatory and must be reused for retries; the same key never creates a second chat. A key cannot be reused with different prompt text.
2. The controller opens a dedicated hidden worker window, sends the prompt through the visible ChatGPT composer, and accepts creation only after ChatGPT exposes a canonical `https://chatgpt.com/c/<id>` URL.
3. Projects are discovered from the current UI. Loom does not invent project IDs. Creation succeeds only after the requested `working_project` is found and the UI reports the move back as verified.
4. `loom_web_worker_inspect` returns durable and live state. An idle assistant response moves the task to `awaiting-review`; it is not evidence that the requested work is correct.
5. `loom_web_worker_review` requires a named independent reviewer, an `approved` or `rejected` decision, and evidence. Approval moves the conversation to the requested Done project and records `done` only when that UI state is verified.

The UI adapter intentionally uses accessible labels and normal controls rather than private ChatGPT endpoints. ChatGPT can change these affordances; a missing or unverifiable control blocks the worker rather than guessing. Inspect live controls with the existing bridge debug command when updating selectors.

## Reconciliation with the Mirai checkout

This branch is self-contained and must not be merged over Mirai's uncommitted October 9 work. First commit or stash that checkout's changes on Mirai, fetch this branch/commit into a clean worktree, inspect `git diff <mirai-base>...<this-commit>`, and then cherry-pick the commit. Resolve overlapping `backend.py`, `tabby/working.py`, `tabby/zen.py`, `loom_mcp.py`, and Zen bridge files manually; run the full test suite before installing. Never copy `.git` or replace Mirai's working tree with file sync.
