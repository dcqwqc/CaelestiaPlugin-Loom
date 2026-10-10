# Loom Agent Input / Workspace Service

Isolated graphical workspaces for AI agents (Loom, Claude Code, Codex, ChatGPT
web workers, future agents). Every graphical worker gets its own private
display with its own pointer, keyboard focus and clipboard. Agents can click,
type and read screens at the same time as the user, and as each other, without
ever touching the user's mouse, keyboard focus, workspace or Zen/Voice session.

## Hard invariant

AI input never reaches the user's Hyprland session. Hyprland has a single
seat: any injected pointer or key event there (ydotool, wtype, `hyprctl
dispatch`, virtual-pointer protocols) moves the user's real cursor or steals
their focus. The service therefore **does not offer** input to the user's
desktop at all. If something cannot be isolated, it is reported as
unavailable (`loom_gui_capabilities`) and the call fails closed.

## Backends

| Backend | Default | What the agent gets | How the user sees it |
|---|---|---|---|
| **nested** | yes | A complete nested Hyprland (own seat, cursor and keyboard focus, tiling, native Wayland and X11 apps), started with `start-hyprland` and a minimal config whose border is the agent's pastel color | Natively, as a window on `special:loom-agents` (SUPER+A): fullscreen for one agent, a three-across grid for several. The user can click into it and use it too (shared with the agent) |
| **xvfb** | fallback | A private X display, X11-capable apps only, no window manager | Streamed into the "Loom Agents" viewer window on the same special workspace |

The service picks `desktop_backend` (default `nested`) and falls back to Xvfb when nested is not available (no Hyprland, grim or wtype, or no host compositor). It never falls back to the user's session.

Nested isolation:
* Input goes through the nested compositor's own `zwlr_virtual_pointer_v1` and virtual keyboard (`wtype`). Measured on Mirai: the host cursor stayed at one position across 349 samples while the agent moved, clicked and typed, and host focus and workspace did not change.
* Private runtime dir `/tmp/L<random>` (0700; short because Hyprland's socket path must fit AF_UNIX's 108 bytes). The host compositor is reached by its absolute socket path. No PipeWire/Pulse (no audio or microphone, so Voice can't be disturbed). Private D-Bus for the compositor and for every app, so single-instance apps (Ghostty, Zen) cannot hand a launch to the user's running copy. GVFS FUSE and desktop portals are disabled so nothing gets mounted in the runtime dir.
* Host placement: window rule `class ^aquamarine$` puts the window on `special:loom-agents silent`, never focused, with `render_unfocused` so hidden desktops don't stall. The service also moves it explicitly and lays out the grid.
* The output size follows the host window (fullscreen alone, a grid cell next to others). The agent's coordinate space is refreshed before every action, and screenshots are delivered at logical size, so image pixels equal click coordinates.
* Cost (measured): about 193 MB and 0% CPU idle per nested desktop, plus its apps.
* Browsers in nested desktops run Wayland Firefox. Element positions add the browser window's place in the nested desktop to Firefox's in-window coordinates.

## Architecture

```
agent ── loom_gui_* (MCP, loom_mcp.py) ──┐
CLI ──── loom_agent_input.py ctl ────────┤   $XDG_RUNTIME_DIR/loom-agent-input.sock (0600)
AI Workspaces launcher (release-owner) ──┤
AgentCursors.qml controls panel ─────────┘
                                          │
                     loom_agent_input.py serve  (systemd: loom-agent-input.service)
                     tabby/agent_workspaces.py  registry · auth · lifecycle · audit · overlay feed
                                          │  one per workspace, survives service restarts
                     tabby/xinput.py broker ── XTEST + capture ──▶ private Xvfb :N (cookie auth)
                                          └── WebDriver BiDi ────▶ private Firefox (loopback port)
                                          │
                     $XDG_RUNTIME_DIR/loom/agent-cursors.json ──▶ AI Workspaces AgentCursors.qml
```

* **Separate from the Voice backend.** `backend.py` (Voice, ChatGPT engine) is
  started by the Loom QML plugin; any change to Loom QML restarts it. The
  agent-input service is Python-only and runs as its own user unit, so it can be
  deployed or restarted without touching an active Voice session.
* **Private display per workspace:** `Xvfb -displayfd … -nolisten tcp -auth
  <cookie>`. Only holders of that workspace's MIT cookie can connect, so one
  agent's broker cannot inject into another agent's display.
* **Minimal app environment:** apps in a workspace get no `WAYLAND_DISPLAY`, no
  Hyprland signature, a private `XDG_RUNTIME_DIR` (no PipeWire/Pulse sockets,
  so no audio or microphone and no way to disturb Voice), `PULSE_SERVER`
  pointing nowhere, and a private session bus via `dbus-run-session`, which
  stops single-instance apps from handing off to the user's running copy.
  Toolkits are forced to X11 (`GDK_BACKEND=x11`, `QT_QPA_PLATFORM=xcb`,
  `MOZ_ENABLE_WAYLAND=0`, …). An app that only speaks Wayland therefore fails
  to start instead of appearing on the user's desktop.
* **Broker process per workspace** (`python3 -m tabby.xinput serve <sock>`)
  owns the X connection (libX11 exits the process on an I/O error, so this must
  not be the service) and the browser's BiDi session (Firefox cannot hand a
  BiDi-only session to a new connection). The service re-adopts brokers after a
  restart through their sockets.
* **Browser workspaces** run Firefox with a persistent private profile
  (`~/.local/state/loom/agent-input/profiles/<id>`, kept across crashes and
  recoveries), `--no-remote --new-instance`, and WebDriver BiDi on a loopback
  port. The user's Zen browser is never started, stopped or reused by this
  service.
* **Input is always real X input** inside the private display. Selector and
  text actions find the element through BiDi, scroll it into view, check it is
  not covered (`[obscured]` instead of clicking the wrong thing), and then move
  the agent's pointer there with XTEST. The overlay therefore always shows the
  true position.

## When should an agent request a graphical workspace?

Use the least graphical tool that can do the job:

| Situation | Use |
|---|---|
| Files, builds, git, services, APIs, CLIs | terminal commands; no graphical workspace |
| Reading or operating a web page / web app | `kind=browser`, then `loom_gui_navigate` and selector/text actions |
| Testing a desktop GUI app you are building | `kind=desktop` + `loom_gui_launch` (several apps tile natively), screenshots, coordinates |
| Checking how something looks on the *user's* Hyprland desktop | AI Workspaces' headless monitor (`ai-workspace gui` / `screenshot`); look only, no input |
| ChatGPT conversation work | `loom_web_worker_*` (Zen bridge, semantic controls) |
| Anything that needs the user's own windows, sessions or logged-in browser | **unsupported**: ask the user |

Terminal-only agents never get a cursor: one appears only after
`loom_gui_acquire`, and it hides again once the agent goes idle.

## Unsupported situations and the safe fallback

| Unsupported | Why | Safe fallback |
|---|---|---|
| Input into the user's Hyprland session or windows | one shared seat: it would move the user's cursor and focus | do not inject; ask the user, or reproduce in a private workspace |
| Wayland-only apps on the **xvfb fallback** | that display is X11, and a Wayland connection would land on the user's desktop | supported on the default nested backend; on the fallback, report unavailable |
| The user's logged-in Zen profile / cookies | sessions are deliberately separate | ChatGPT via `loom_web_worker_*`; otherwise ask the user to share data or log in inside the private browser |
| Audio, microphone, camera, screen-sharing | private runtime dir has no PipeWire/Pulse | report unavailable |
| Accessibility tree (AT-SPI) | not wired into private displays yet | browser selectors, else screenshot + coordinates |
| Remote (HTTP/ChatGPT connector) GUI control | disabled by default | user enables `allow_remote_mcp`, else ask a local agent |
| Several agents driving one display at the same instant | X has one core pointer per display | separate workspaces, or `grant` + `take_control` turn-taking |

Whenever a `loom_gui_*` call returns `[unavailable]`, stop and report it.
Never fall back to `ydotool`, `wtype`, `hyprctl dispatch` or similar on the
user's session.

## MCP tools (`loom` server)

Identity comes from the agent environment: `AI_SESSION_ID` (or
`LOOM_AGENT_ID`) is the agent id, `LOOM_TASK_ID` the Loom Working card, and
the MCP process's parent pid is the owner process. Tokens never leave the MCP
process. After an MCP restart the owner is re-attached automatically.

| Tool | Purpose |
|---|---|
| `loom_gui_capabilities` | what is available and why not |
| `loom_gui_acquire {request_key, kind, url?, label?, size?}` | create or recover your workspace (idempotent per agent + key) |
| `loom_gui_list` | all workspaces, no secrets |
| `loom_gui_attach {workspace_id}` | reconnect (owner) or join (grantee) |
| `loom_gui_grant {workspace_id, grantee}` | owner allows another agent id |
| `loom_gui_take_control {workspace_id}` | take the shared display's pointer/keyboard |
| `loom_gui_launch {workspace_id, command[]}` | desktop: start an app |
| `loom_gui_navigate {workspace_id, url}` | browser: load a page |
| `loom_gui_click {selector \| text \| x,y, button?, count?}` | click |
| `loom_gui_type {text, selector?}` | type (any Unicode) |
| `loom_gui_key {keys}` | `Enter`, `ctrl+l`, `ctrl+shift+t`, `alt+F4` |
| `loom_gui_scroll {dx?, dy?, selector? \| x,y}` | wheel scroll |
| `loom_gui_move {x, y, duration_ms?}` | move the visible pointer |
| `loom_gui_screenshot {max_width?}` | inline PNG + pointer |
| `loom_gui_state {include_text?, selector?}` | pointer, windows, url/title/focus, text |
| `loom_gui_appearance {color?, name?}` | persistent pastel color (`lavender mint peach rose sky butter lilac sage coral aqua` or `#RRGGBB`) |
| `loom_gui_pause` / `loom_gui_resume` / `loom_gui_cancel` | flow control |
| `loom_gui_release {keep_profile?}` | close apps and display |

Every side-effecting tool takes an optional `action_id`. The MCP layer adds a
fresh one when it is missing, so a retried call returns the recorded result
(`"replayed": true`) instead of clicking or typing twice.

Errors carry a stable code: `[invalid] [forbidden] [not_found] [paused]
[busy] [released] [crashed] [timeout] [cancelled] [obscured] [limit]
[unavailable] [failed]`.

### Example

```text
loom_gui_acquire  {"request_key": "pr-1234-docs", "kind": "browser", "url": "https://example.org", "label": "docs check"}
loom_gui_click    {"workspace_id": "gws-…", "text": "More information"}
loom_gui_state    {"workspace_id": "gws-…", "include_text": true}
loom_gui_screenshot {"workspace_id": "gws-…", "max_width": 1024}
loom_gui_release  {"workspace_id": "gws-…"}
```

### Local CLI (same socket)

```sh
python3 ~/.local/share/caelestia/plugins/loom/loom_agent_input.py list
python3 ~/.local/share/caelestia/plugins/loom/loom_agent_input.py ctl capabilities
python3 ~/.local/share/caelestia/plugins/loom/loom_agent_input.py ctl user-pause '{"workspace_id":"gws-…"}'
python3 ~/.local/share/caelestia/plugins/loom/loom_agent_input.py ctl audit '{"limit":20}'
```

User commands (`user-pause`, `user-resume`, `user-pause-all`,
`user-resume-all`, `user-terminate`, `user-hide-agent`, `user-show-agent`,
`user-inspect`) need no token and are audit-logged as `actor=user`.

## Permissions and authorization

* Socket mode 0600 (same user only). Tokens are random per agent and workspace,
  only their SHA-256 is persisted, and they are never listed or written to the
  overlay.
* Input requires the token of the **current controller**. Other agents get
  `[forbidden]`. Granted agents get `[busy]` until they `take_control`.
* `pause` by the user blocks all input (observation stays allowed), and only
  the user can resume it.
* Remote MCP (HTTP) clients are refused unless `allow_remote_mcp` is true.
* Audit log: `~/.local/state/loom/agent-input/audit.jsonl` (0600, rotated at
  5 MB). Typed text is never logged, only its length.
* Limitation: all of this runs as the same Unix user. It prevents accidental
  and cross-agent interference, but it is not a security boundary against
  hostile code running as that user.

## Lifecycle, recovery and cleanup

* `acquire` is idempotent on `(agent_id, request_key)`. The workspace id is
  derived from that pair, so reconnecting, retrying or recovering never creates
  a duplicate.
* **Crash:** a dead display or browser is detected within about 1 s (`tick`) and
  marked `crashed`. The next `acquire` with the same key rebuilds it with the
  same id and browser profile (`"result": "recovered"`).
* **Service restart:** `KillMode=process` keeps displays, brokers and browsers
  running. The new instance re-adopts them (`reconcile → adopted`) and agents
  continue with the same tokens.
* **Reboot:** everything left is marked `lost`. Workspace identity, colors,
  grants and browser profiles persist, so the same `acquire` recovers it.
* **Owner exit:** when the owner process is gone for `owner_grace_seconds`
  (90), the workspace is released. AI Workspaces also calls `release-owner` when
  an agent session ends. Workspaces without an owner pid are released after
  `idle_release_minutes` (45).
* **Release** kills the process groups, then sweeps `/proc` for every process
  tagged `LOOM_AGENT_WORKSPACE=<id>` (SIGTERM, then SIGKILL), and removes the
  runtime directory and preview. Exited children are reaped every tick.

## Overlay (AI Workspaces plugin, `AgentCursors.qml`)

* Fed by `$XDG_RUNTIME_DIR/loom/agent-cursors.json` (written on change, watched
  by QML, never polled). Previews are refreshed only while an agent is active:
  after each action, then every 1.5 s for 8 s.
* Drawn on the focused physical monitor only (never on `AI-*` headless
  outputs), as a click-through layer: `mask: Region {}` and no keyboard focus.
* Pointers sit at their true coordinates *inside the preview of the agent's
  own display*. Nothing implies an agent is clicking the user's windows.
* Settings (Nexus → Plugins → AI Workspaces): show/hide, labels, opacity,
  animation (`full`/`subtle`/`off`), preview width, corner. Per-agent hide and
  the controls panel: `qs -c caelestia ipc call loomAgents toggleControls`
  (also `pauseAll`, `resumeAll`, `hideAll`, `showAll`, `list`).

## Configuration

`~/.config/loom/agent-input.json` (0600): `enabled`, `allow_remote_mcp`,
`max_workspaces` (6), `default_size` (`1280x800`), `owner_grace_seconds`,
`idle_release_minutes`, `cursor_idle_seconds`, `browser_command`
(`firefox`), `agents` (persistent colors and names), `hidden_agents`.

## Deployment

```sh
install -m644 systemd/loom-agent-input.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now loom-agent-input.service
systemctl --user restart loom-mcp.service   # HTTP MCP picks up the tool list
```

Requires `Xvfb`, `libXtst`, Python Pillow, and `firefox` for browser
workspaces. `dbus-run-session` is optional but recommended.

## Tests

```sh
python3 -m unittest tests.test_agent_workspaces            # logic, auth, recovery, overlay, MCP (fake runtime)
python3 -m unittest tests.test_agent_input_integration     # real Xvfb/Firefox; skipped if missing
python3 tests/e2e_agent_input_isolation.py                 # live-desktop isolation proof (see below)
```

`tests/e2e_agent_input_isolation.py` runs two agents in separate workspaces
while sampling the Hyprland cursor, active window and workspace, and the
overlay's layer input region. It fails if the physical cursor ever lands on an
agent's path or if focus or workspace change because of the agents.
