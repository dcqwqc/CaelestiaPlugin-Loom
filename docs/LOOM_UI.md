# Loom declarative interactive UI — October 2026

Agents build small interactive panels on Loom's board as a typed component
tree instead of free-form items. Classic board items (`loom_display`, cards,
choices, …) keep working and render above the views.

## Model (`tabby/ui_tree.py`)
A view is `{type, id, props, children, on}` nodes, at most 4 views, 64 nodes,
depth 6, and 24 KiB per view measured as compact UTF-8 JSON (so all views fit
one 128 KiB IPC message). Every type has a closed, typed prop schema; unknown props, types and
fields are rejected, and ids must be unique per view.

- Containers: `column{gap}`, `row{gap,align}`, `card{title,tone}`
- Display: `text{text,style,tone}`, `badge`, `progress{label,value 0..1}`,
  `divider`, `list{entries,ordered}`
- Interactive: `button` (press), `toggle` (change), `slider{min,max,step}`
  (change), `input{max_length}` (change, submit), `select{options}` (change)
- Every node has `hidden`; interactive nodes have `disabled`.

Colours are Caelestia theme tones only (`neutral|primary|secondary|tertiary|
success|warning|error`), mapped in `Panel.qml` onto `Colours.palette.m3*`.
All text renders as `Text.PlainText`.

## Safe bindings
`on: {event: [action, …]}` with at most 4 actions of:
`{do: emit, name}` (named intent for the agent), `{do: set, target, prop,
value | from_event: true}` and `{do: toggle, target}` (flips `hidden`).
Targets and props are checked at validation time, including types:
`from_event` is only accepted when every value the source can emit fits the
target prop (toggle → boolean; slider range inside the target range; input
`max_length` / select options within the target's length, enum or options).
Props other props depend on (slider min/max/step, input max_length, select
options) cannot be set by events, so a validated binding cannot fail when the
user interacts. There is no action that runs a
command, opens a URL or calls IPC. QML reports interactions only through
`loomctl.py ui-event <view> <node> <event> [json-value]`; the backend
re-validates the event and value (slider clamp/step, input max length, select
option, hidden/disabled) before changing state and logging it.

## Patches, undo, events
`loom_ui_patch` applies `set_props`, `set_on`, `insert`, `remove`, `move` and
`replace` atomically on a copy, then revalidates the whole tree. Optional
`base_revision` gives optimistic locking. Renders and patches are undoable
(`loom_ui_undo` / `loom_ui_redo`, 20 steps); user interactions are state, not
edits, and are not undo steps. `loom_ui_events(since, wait_seconds)` reads the
bounded (200) interaction log; a reply stops at 96 KiB with `more: true`.

## Templates
`loom_ui_template_save` stores a validated tree (or a live view via
`from_view`) in `~/.config/tabby/ui_templates.json` (lock, atomic 0600 write,
fsync, corrupt files never overwritten). `{{param}}` placeholders in strings are
filled once with plain strings by `loom_ui_template_render`.

## MCP tools
`loom_ui_schema`, `loom_ui_render`, `loom_ui_patch`, `loom_ui_undo`,
`loom_ui_redo`, `loom_ui_get`, `loom_ui_close`, `loom_ui_events`,
`loom_ui_template_save/list/get/delete/render`.

## Verify
`python3 -m unittest discover -s tests` (`tests/test_ui_tree.py`).
Limitation: no Qt/Quickshell runtime is available in CI, so the renderer is
checked statically and its JS helpers run under node; a live Quickshell check
is needed after deployment.
