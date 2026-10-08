# Loom Tasks tile — October 2026

A live, reusable Tasks module that shows Philipedia LOOM mission and idea-inbox
states in a native Caelestia-styled card.

## Data path
`loom_tasks.py refresh` → `tabby/missions.py` (fixed host `philipedia`, fixed
bridge executable, actions `status` + `inbox`) → `tabby/tasks_tile.py`
projection → `~/.local/state/tabby/loom-tasks.json` (mode 0600, atomic replace)
→ one JSON line on stdout → `LoomState.tasks` → `LoomTasksCard.qml`.

No host, command or path is accepted from the CLI, QML or MCP. No remote
token, port, auth or service unit changed.

## Status projection (ledger fields only)
| Ledger | Tile state |
| --- | --- |
| `status` queued/pending/new | queued |
| running/working/executing/active | running |
| review/awaiting_review/reviewing/verifying | review |
| paused/blocked/decision/waiting | blocked ("Needs decision") |
| failed/error/rejected | failed |
| done + `autonomous_verified_success` / `reviewed_verified_success` | verified |
| done + `accepted_by_human` | accepted |
| done with any other or no outcome | done ("Done · unverified") |
| anything else | unknown, raw status shown verbatim |
| inbox idea | captured, or linked when it carries a mission id |

Progress, percent, ETA and other bridge fields are dropped. When a refresh
fails, the snapshot keeps the last good rows per source, sets `stale: true`
and lists the errors; `fetched_at` only advances when a source was read.

## Lifecycle
- `Main.qml` (custom entry point) loads the cached `snapshot` and the saved
  `tile` module on start (no network).
- While at least one card is visible (`LoomState.tasksViewers > 0`) it runs
  `refresh` every 60 s; a running refresh is never overlapped and is stopped on
  plugin destruction. The card's refresh button requests one immediately.
- Backend restarts (`LoomState.reset()`) do not clear the tasks snapshot.
- Rows are synced into a `ListModel` by key (insert/move/set/remove), so a
  refresh updates changed rows without rebuilding the rest of the UI.

## Placement and resize
- `loom_tasks.py tile` returns, or idempotently creates, the saved `tasks`
  module on the `performance` surface (default 360×300).
- Dragging the card's bottom-right grip resizes live and on release persists
  via `loom_tasks.py resize ID W H` (validated 240–4096 in the card, 80–4096
  by the registry). Size survives restarts.
- x/y, anchor, monitor and workspace are stored but **not applied**: the shell
  panel is compositor-anchored and no floating window exists yet.

## Hosts
- Performance / other hosts of the custom entry point: `Main.qml` exposes
  `tasksCard` (a `Component` of `LoomTasksCard`). A host instantiates it; set
  `resizable: false` if the host owns geometry.
- Loom shell panel: opt-in, hidden by default. `qs ipc call loom toggleTasks`
  shows/hides it; `refreshTasks` and `tasks` (text summary) are also exposed.
- MCP: `loom_tasks_snapshot` (read-only, cached, no network). The MCP service
  has `ProtectHome=read-only`, so it only reads the cache.
- `loom_space_show` reports a performance tasks module as rendered by the
  native card instead of "surface renderer not installed".

## Not runtime-verified (headless Philipedia)
No Qt/QML runtime, `qmllint` or display exists on this host. The following
were checked only statically (brace balance, wiring, banned-content tests),
never rendered:
- `LoomTasksCard.qml` layout, colours, `Tokens` lookups (guarded, falling back
  to the literal sizes Panel.qml already uses), resize grip and DragHandler.
- `Main.qml` Process/Timer wiring and new IPC functions.
- Panel.qml integration and the implicit-size change while the card is shown.
- Whether the Caelestia Performance view instantiates `tasksCard`; that host
  hook is not part of this repository.
No visual validation is claimed.

## Test report
See the VERIFY section of the task report; reproduced here:

```
python3 -m unittest discover -s tests -v
  Ran 87 tests — 86 OK, 1 FAIL (pre-existing, unchanged:
  test_physical_left_alt_is_available_on_mirai needs the desktop's
  physical keyboard; fails identically on base eb3a8af).
python3 -m unittest tests.test_tasks_tile   -> Ran 26 tests, OK
python3 -m py_compile loom_tasks.py loom_mcp.py tabby/tasks_tile.py -> exit 0
```
