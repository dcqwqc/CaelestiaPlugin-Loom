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
- The first visible card requests a refresh, and while at least one card remains
  visible (`LoomState.tasksViewers > 0`) it runs `refresh` every 60 s. Requests
  automatic requests are coalesced behind a 60 s minimum interval and a running
  refresh is never overlapped. The card's refresh button bypasses the automatic
  throttle while retaining the no-overlap guard.
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
  via `loom_tasks.py resize ID W H`. The same guarded command persists sizes
  for supported native Performance/floating modules (validated 240–4096 in
  the card, 80–4096 by the registry). Size survives restarts.
- `Main.qml` loads `FloatingWidgets.qml`; an `Instantiator` creates a native
  Quickshell `PanelWindow` for every saved tasks/CPU/memory/storage/battery/weather
  module on a Performance/floating surface, except the reserved hover-card tile.
  Native discovery is read-only and never creates a module; the reserved tile
  remains exclusively in the opt-in counter popover. Per-kind views bind to
  Caelestia's live services (and Quickshell UPower for battery).
- Registry polling runs every 10 s and reconciles a keyed `ListModel` in place,
  preserving each unchanged window and any active drag/resize gesture. Canonical
  key ordering avoids false changes, while a bounded placement-field overlay keeps
  an older poll from reverting geometry without masking concurrent content edits.
- The saved monitor, anchor, inward x/y offsets, width and height are applied.
  During a title-row drag or edge-aware resize, a non-overlapping, up-to-50 Hz query
  reads Hyprland's compositor cursor position and computes motion from the first
  valid sample. Polling stops with the gesture, and invalid data causes no move.
  The gestures persist
  through guarded `place`/`resize` commands. `free` maps to top-left; centered
  modules apply their saved offsets relative to screen center. Wayland
  layer surfaces are global, so the saved workspace remains reserved metadata.

## Hosts
- Loom shell panel (the only host in this repository): opt-in, hidden by
  default. `qs ipc call loom toggleTasks` toggles whether the ledger
  card is included **while hovering over the task-count chip**; leaving the
  popover always hides it. `refreshTasks` and `tasks` (text summary)
  are also exposed.
- Performance/floating host: explicitly saved supported modules are persistent
  native surfaces. The reserved hover-card tile is excluded. `loom_space_show`
  reports native modules as requested, without claiming that the independently
  polling shell has rendered them and without creating duplicate board cards.
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
python3 -m unittest tests.test_tasks_tile tests.test_spaces -v
  Ran 52 tests — OK
python3 -m unittest discover -s tests -v
  Ran 105 tests — 104 OK, 1 FAIL (host-dependent, unchanged:
  test_physical_left_alt_is_available_on_mirai needs the desktop's
  physical keyboard and is excluded from the Philipedia host-independent run).
host-independent discovery (the above hardware test excluded)
  Ran 104 tests — OK
python3 -m py_compile [all repository Python modules] -> exit 0
```

## Mirai runtime integration update
The native card is now an inline TasksView component in Panel.qml, avoiding
Quickshell's versioned plugin module URL filename case verification issue.
Static tests inspect the inline component used by the live renderer.
