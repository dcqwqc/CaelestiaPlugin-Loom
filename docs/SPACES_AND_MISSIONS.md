# Loom MCP saved Spaces and mission intake — October 2026

This milestone adds 17 MCP tools, without removing the original 23.
Legacy Tabby and Lume aliases remain supported.

## Persistent modules
A module has a stable ID, kind, title, data, visibility, and placement.
Its placement contains surface (board/performance/floating), anchor,
x/y, width/height, monitor and workspace. Reusable spaces reference module IDs.
The registry is versioned JSON, atomically written to
~/.config/tabby/modules.json with an exclusive lock, mode 0600 and fsync.
Deleting a module unlinks it from spaces; removing a space preserves modules.
Request IDs provide idempotent creates and reject conflicting retries.

MCP: loom_module_create/get/list/update/delete and
loom_space_save/get/list/show/delete.

Board text and tasks widgets render on the board. Every visible tasks, CPU,
memory, battery, weather, or storage module on a Performance/floating surface
is instantiated as an independent native window with saved monitor, anchor,
offsets and size. System modules bind directly to Caelestia/Quickshell services;
`loom_space_show` reports these module IDs in `native_requested_ids`; only board
IPC results are returned in `rendered_ids`, because MCP cannot observe whether
the independently polling shell has instantiated a native window.

## Philipedia tasks and idea capture
The mission bridge sends encoded JSON over authenticated SSH to a fixed
Philipedia host and fixed LOOM bridge executable. No user-provided shell
command, host name, executable, or SSH argument reaches the shell.

MCP: loom_idea_capture/list, loom_mission_list/activity/health/create/resume.
Durable idea capture works even when workers cannot run. Mission creation
and resumption require an affirmative worker sandbox health check. This
explicitly prevents failed launches while Philipedia bwrap cannot mount its
filesystem. Existing kernel verification/review and production approvals
remain unchanged.

Required coordinating Philipedia LOOM bridge update: capture, inbox, health.
The inbox stores each batch atomically with stable IDs and deduplicates
retries. Captured != executing != verified != accepted.

## Verify
Run Python unittest discovery under tests and compile loom_mcp.py.
After deployment verify read-only mission health, idea listing, and tool registry.
Changes are limited to the plugin; no modification of Sumi or the other
Loom office application is required.

Native floating Performance/tasks/system widget surfaces are implemented on a review branch,
but live deployment and visual testing remain pending. Native floating text modules,
workspace-scoped Wayland layer surfaces, cross-device module sync, ChatGPT Project
automation, and a healthy isolated coding executor on Philipedia remain unverified.


## Self-extending capability requests (2026-10-09)

`loom_capability_request` accepts a stable `request_id`, title, full requirement body,
Philipedia repository path, and optional priority. It makes one fixed-command SSH
request to the existing LOOM bridge. The bridge always stores the idea first; it
creates at most one Codex mission only if the isolated worker sandbox passes its
health check. On failure, it returns `state=blocked` and the durable idea ID.
Retries with the same request are idempotent; changed content under a reused key
is rejected. Task dispatch does **not** prove task execution, independent review,
deployment, or real desktop verification. It is not a ChatGPT browser-worker tool.

Code and focused MCP tests passed on Philipedia. Desktop activation on Mirai
and a real healthy-sandbox worker dispatch still need runtime verification.
