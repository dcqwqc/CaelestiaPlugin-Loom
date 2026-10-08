# LOOM origin-aware mission handoff

When a ChatGPT/Loom worker creates a coding mission through loom_mission_create,
the MCP now registers a durable origin reference with the Philipedia handoff
daemon. An exact chat link can be supplied as origin_url, but the client may not
know it; no chat UUID is synthesized.

Added MCP tools:
- loom_handoff_status: read the daemon status (origin count, delivery backlog,
  queued continuation count).
- loom_handoff_register: attach or update a verified origin reference for an
  existing mission, specifying origin_ref, origin_url (optional), origin_source
  and auto_continuation.
- loom_mission_create now accepts origin_ref, origin_url, origin_source and
  auto_continuation. By default it registers an opaque loom-mcp:MISSION_ID
  origin and selects the verified-only Codex continuation policy.

Mission creation and origin registration are independent operations. If the
first succeeds but origin registration fails, the MCP returns the created
mission plus handoff_error rather than falsely claiming that no worker exists.

The remote helper is a fixed JSON-over-stdin command on Philipedia. It exposes
no arbitrary host or shell surface. The daemon on Philipedia watches LOOM's
canonical ledger events via inotify and reconciles at most every 60 seconds of
idle. Its private SQLite outbox retries notifications when Mirai is offline.

A continuation worker is a new isolated Codex mission after independent review
and verified success, not a new assistant response in the originating ChatGPT
consumer conversation. A separate user action or eligible ChatGPT Work trigger
is needed to start a genuine ChatGPT turn. No automatic production merge/push
is performed from the handoff daemon.

Verification:
  python3 -m unittest discover -s tests
  systemctl --user status loom-mcp.service
  systemctl --user status loom-handoff.service   # on Philipedia

