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

Board text and tasks widgets render on the board. The reserved Loom Tasks
module also renders in a native persistent Performance/floating surface with
saved monitor, anchor, offsets and size; it binds directly to Celestia colours
and live CPU/memory services. Other CPU, memory, battery, weather, storage and
user-created surface modules remain explicit skipped cases in `loom_space_show`.

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

Not yet complete: generic renderers for every saved module kind, workspace-
scoped Wayland layer surfaces, cross-device module sync, ChatGPT Project
automation, and any isolated coding executor on Philipedia.
