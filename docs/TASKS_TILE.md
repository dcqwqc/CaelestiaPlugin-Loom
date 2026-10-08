# Loom Tasks tile — October 2026

A live, reusable Tasks module that shows Philipedia LOOM mission and idea-inbox
states in a native Caelestia-styled card.

## Data path
`loom_tasks.py refresh` → `tabby/missions.py` (fixed host `philipedia`, fixed
bridge executable, actions `status` + `inbox`) → `tabby/tasks_tile.py`
projection → `~/.local/state/tabby/loom-tasks.json` (mode 0600, atomic replace)
→ one JSON line on stdout → `LoomState.tasks` → `inline TasksView in Panel.qml`.

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
- Each card registers itself as a viewer idempotently
  (`LoomState.setTasksViewer`, per-card `viewerRegistered` flag, only after
  construction), so a card created hidden counts 0 and counts 1 once shown.
- While at least one card is visible (`LoomState.tasksViewers > 0`) it runs
  `refresh` every 60 s; a running refresh is never overlapped and is stopped on
  plugin destruction. The card's refresh button requests one immediately.
- Backend restarts (`LoomState.reset()`) do not clear the tasks snapshot.
- Rows are synced into a `ListModel` by key (insert/move/set/remove), so a
  refresh updates changed rows without rebuilding the rest of the UI.

## Native persistent surface, placement and resize
- `loom_tasks.py tile` returns, or idempotently creates, the plugin-owned
  `tasks` module on the `performance` surface (default 360×300, data
  `{"source": "philipedia", "role": "loom-panel-tasks-tile"}`). It is resolved
  only through the reserved registry request ID `loom-tasks-tile-default`
  (`SpaceStore.module_for_request`); other tasks modules in the user's Spaces
  are never selected. If the tile is deleted, a fresh one is created.
- Dragging the card's bottom-right grip resizes live and on release persists
  via `loom_tasks.py resize ID W H`, which refuses any module other than the
  plugin-owned tile (validated 240–4096 in the card, 80–4096
  by the registry). Size survives restarts.
- `Main.qml` loads `FloatingWidgets.qml`, whose native Quickshell `PanelWindow`
  renders the reserved tile independently of the transient top panel. The
  window binds its background/text to live `Colours.palette`/`tPalette` and
  its compact CPU/RAM readout to Caelestia's `Cpu` and `Memory` services.
- The saved monitor, anchor, inward x/y offsets, width and height are applied.
  Dragging the body or bottom-right grip updates the window live and persists
  through the guarded `place`/`resize` commands. `free` maps to top-left;
  `center` is compositor-centered and intentionally ignores offsets. Wayland
  layer surfaces are global, so the saved workspace remains reserved metadata.

## Hosts
- Loom shell panel (the only host in this repository): opt-in, hidden by
  default. `qs ipc call loom toggleTasks` toggles whether the ledger
  card is included **while hovering over the task-count chip**; leaving the
  popover always hides it. `refreshTasks` and `tasks` (text summary)
  are also exposed.
- Performance/floating host: the plugin-owned tile is a persistent native
  surface. `loom_space_show` reports it as rendered without sending a duplicate
  board card. Other user-created Performance modules remain explicitly skipped.
- MCP: `loom_tasks_snapshot` (read-only, cached, no network). The MCP service
  has `ProtectHome=read-only`, so it only reads the cache.
- The counter hover card remains opt-in and transient; its mouse and touch
  behavior is unchanged by the persistent host.

## Not runtime-verified (headless Philipedia)
No Qt/QML runtime, `qmllint` or display exists on this host. The following
were checked only statically (brace balance, wiring, banned-content tests),
never rendered:
- `inline TasksView in Panel.qml` layout, colours, `Tokens` lookups (guarded, falling back
  to the literal sizes Panel.qml already uses), resize grip and DragHandler.
- `Main.qml` Process/Timer wiring and native window creation.
- Panel.qml integration and the implicit-size change while the card is shown.
- Viewer registration and geometry projection were run in node from extracted
  QML functions, not in a QML engine. This worktree has no Qt/QML runtime.
No visual validation is claimed.

## Test report
See the VERIFY section of the task report; reproduced here:

```
python3 -m unittest discover -s tests -v
  Ran 98 tests — 97 OK, 1 FAIL (pre-existing, unchanged:
  test_physical_left_alt_is_available_on_mirai needs the desktop's
  physical keyboard; fails identically on base eb3a8af).
python3 -m unittest tests.test_tasks_tile   -> Ran 36 tests, OK
python3 -m py_compile loom_tasks.py loom_mcp.py tabby/spaces.py -> exit 0
```

## Mirai runtime integration update
The native card is now an inline TasksView component in Panel.qml, avoiding
Quickshell's versioned plugin module URL filename case verification issue.
Static tests inspect the inline component used by the live renderer.
