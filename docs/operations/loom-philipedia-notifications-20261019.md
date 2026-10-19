# Philipedia headless Loom notification routing (staged, not deployed)

## Root cause
Philipedia's Loom headless core implements missions, but lacks local implementations
of loom_notify and loom_notification_get/list. A frozen Mirai tool catalog
advertises these tools and forwards their calls through a desktop adapter.
When Mirai went offline, notifications failed despite Philipedia being healthy.

The patch registers local Philipedia tools backed by a private SQLite ledger,
and reuses the independently tested notification module. No desktop IPC is
needed. Provider acceptance does not establish handset display.

## Rollout gates
1. Confirm live source ancestry and rebase if necessary.
2. Prepare secure Phi EnvironmentFile with LOOM_NTFY_URL (HTTPS topic),
   optional LOOM_NTFY_TOKEN_FILE and LOOM_NOTIFICATIONS_DB, with credentials
   outside Git and restricted permissions. Confirm recipient phone subscribes.
3. Back up installed source and any existing notification database; don't
   overwrite or silently merge the separate Mirai notifications database.
4. Run unit tests and compile checks in an isolated worktree.
5. Require explicit human production approval before restarting only the
   loom-mcp-philipedia.service unit, with backup and rollback plan.
6. Create a harmless test notification via authenticated local MCP endpoint;
   verify phone provider acknowledgement, persistent readback, and phone receipt,
   then verify independence with Mirai offline.
7. QWQC Hub loom-core currently errors with Connection closed even for
   pre-existing headless tools; separately repair Hub-to-upstream routing.
8. After tests, make Philipedia the authoritative notification endpoint; retain
   desktop notification surfaces as optional clients.

## Rollback
Restore backed-up code and service environment; restart only
loom-mcp-philipedia.service. Preserve both host notification databases.
