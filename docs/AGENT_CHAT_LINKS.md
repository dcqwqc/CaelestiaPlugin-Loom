# Loom task ↔ agent ↔ ChatGPT conversation links

Loom persists every task's internal 16-character (or pre-existing) task ID in
`~/.local/state/tabby/working.json`, and a private cross-reference ledger in
`~/.local/share/tabby/agent-links.sqlite3` (mode 0600). The ledger stores:

- `task_id`: stable and immutable task/card ID
- `chat_id`: the ChatGPT conversation's canonical `/c/` identifier (NOT a guessed title)
- `chat_url`: the latest verified project-scoped URL, updated on project moves
- `task_name`: the name displayed in the Loom top task card
- `chat_name`, `sync_status`: last *verified* ChatGPT sidebar rename
- `agent_id`, `agent_name`: invoking agent handle/display name
- `origin_ref`, `origin_url`: stable origin session reference and optional return link

If the creator supplies an `origin_ref`, a stable `agent_id` is derived from that
origin. A ChatGPT canonical origin URL uses only its conversation ID, so moving
it between projects does not change the agent handle. Supply
`origin_agent_id` if the agent platform exposes a real stable identity.
When provenance is missing, Loom uses `unbound-TASK_ID` rather than guessing
another agent's identity. `loom_task_bind_origin` can establish that link once,
without letting later requests steal an already claimed origin.

## MCP workflow

Create future workers with `loom_web_worker_create(request_id, title, prompt,
origin_ref, origin_url, origin_agent_name, origin_agent_id)`; the final four are
optional. Reusing `request_id` with a different prompt or origin is rejected.
Do not start real work before the project route is verified.

Inspect `loom_agent_links` to see IDs and name-sync status. The Loom task name
is authoritative for a linked ChatGPT worker. A background monitor asks the
isolated Zen actor to open **that chat's own** actions → Rename and save the
name. Success requires exact canonical conversation identity and a visible
sidebar title match. A slow, ambiguous, missing, or unloaded browser UI leaves
`sync_status=pending` and retries later; it must never claim a successful
rename after merely clicking. `loom_task_update(title=...)` schedules an
additional sync, and `loom_chat_title_sync(task_id)` retries on demand.

On Review, Blocked, Vault, Done or uncertain creation outcomes, Loom records a
single durable event on transition. Workers can publish an explicit reply with
`loom_worker_reply(task_id,event_key,message)`; reusing `event_key` with a
different payload is rejected. The invoking agent reads
`loom_agent_events(agent_id)` on its next turn and only after handling the
result calls `loom_agent_event_ack(agent_id,event_id)`. These events survive
Loom, Zen, and operating system restarts. A native Loom notification is also
queued once per event so the user can resume work without polling.

**Important platform limitation:** these are **delivery and resumability**, not
a promise of automatic execution in an arbitrary external ChatGPT tab.
Generic MCP requests carry no first-party per-conversation identity and
MCP has no push mechanism that starts a new turn in a closed ChatGPT consumer
conversation. A connected agent/runtime may poll this inbox or explicitly
resume itself. Without that agent-side support, Loom notifies the user and
keeps the callback pending rather than sending an unsolicited message or
pretending that an agent woke up. Origin-agent IDs are routing handles, not
authorization credentials; the user's authenticated Loom MCP connection is a
shared trust boundary. Do not expose this MCP server to untrusted agents.

## Verification and deployment

- `python3 -m unittest discover -s tests -q`
- `node --test tests/*.test.mjs`
- `python3 -m unittest tests.test_agent_links tests.test_agent_title_sync -v`
- Confirm 0.13+ Zen bridge has loaded, not just been copied to the profile.
- Test a real eligible chat's rename, compare the task ledger `chat_name` and
  its actual sidebar label, and verify a callback event/ack over an MCP client.
- Preserve the active Zen browser/Voice session during deployment: actor code
  cannot be hot-replaced reliably by a QuickShell or Loom-MCP restart.
