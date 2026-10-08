# CaelestiaPlugin-Loom

Loom is a standalone Caelestia companion that wraps the user's existing
ChatGPT session through a dedicated hidden Zen/Firefox tab.

Protocol7 is only the wake-word engine. When Protocol7 recognizes the configured
wake phrase it sends a small local `wake` event to Loom's mode-0600 Unix socket.
Loom owns everything after that point: fresh-chat creation, ChatGPT Voice,
state/animation, text input, image clipboard uploads, whiteboard/MCP and close /
auto-hide behavior.

## Interaction

- Say the Protocol7 wake phrase (default **Hey Loom**) for a fresh Voice chat.
- Press **Super+Shift+Space** to summon a fresh text chat. Hover Loom to reveal
  the composer.
- Press Enter to send text. Ctrl+V keeps normal text paste behavior and also
  attaches a clipboard image when one exists. The paperclip button does the same.
- Hover the face to reveal the close button.
- The mouth follows real PCM amplitude from the current PipeWire/Pulse output
  monitor while ChatGPT Voice is active.
- Thinking/tool work uses a separate orbit animation rather than speech motion.

## Browser engine

The Zen/Sine bridge is bundled directly in this plugin under `bridge/zen/`; it is
no longer a separately-versioned dependency. On backend startup Loom compares
the bundled bridge with the active Zen profile and deploys it automatically when
files are missing or outdated. `bridge/zen/scripts/install.sh` remains available
for manual repair. A Zen restart (or Sine mod reload) is required after privileged
browser-side JavaScript changes are deployed.

The bridge owns one dedicated Loom ChatGPT tab. In normal mode Firefox hides
that tab. Debug mode reveals/selects the same tab without changing the login
session. Every Loom summon navigates to a verified fresh `/` ChatGPT composer
before Voice or text input begins.

The bridge intentionally does not scrape conversation content. It uses semantic
controls to start/end Voice, set/send composer text, attach an explicitly pasted
image and expose only coarse status (`ready`, `active`, `working`, auth state).

## Local interfaces

`$XDG_RUNTIME_DIR/tabby.sock` is created with mode 0600. `loomctl.py` supports
`status`, `wake`, `close`, `toggle-input`, `send-text`, and `paste-clipboard`.

## Loom MCP (one server)

`loom_mcp.py` is the single Loom MCP server (stdlib only). It exposes Loom's
board (text, progress, status lines, cards, lists, shapes, clickable choices,
plus a generic `loom_display` for any future widget type) and Working task
cards (create, pin current chat, update, done, reopen, open). There is no
shell, file or browser access: every tool is one request on `tabby.sock`.

- Local clients: `python3 loom_mcp.py stdio` (`mcp_server.py` and
  `working_mcp_server.py` are kept as aliases).
- Remote (ChatGPT): `systemd/loom-mcp.service` (user unit, enabled) serves
  Streamable HTTP on `:8766`, accepts only loopback/Tailscale sources and needs
  the token from `~/.config/tabby/mcp-token` as `/mcp/<token>` or
  `Authorization: Bearer <token>`. Philipedia's Traefik publishes it as
  `https://tabby-mcp.qwqc.de/mcp/<token>`, allowlisted to OpenAI's connector
  egress ranges (refreshed daily).
- New widget: add a delegate in `Panel.qml`; unknown item types already render
  with the generic title/text fallback. No new MCP server needed.


## Current runtime

- `Super+Shift+Space` is a true summon toggle: start Loom Voice with the hover text input available; press it again to close Loom completely.
- Debug mode reveals a minimal standalone Gecko ChatGPT engine window, not a normal Zen tab or browser window.
- Voice activation is asynchronous: Loom clicks Voice, then tracks the fresh ChatGPT Voice surface until it becomes active.
- The speaking face uses live output audio amplitude.


## Session policy

Loom supports three session modes from plugin settings:

- **Smart** (default): continue the current conversation while it is recent; start a fresh chat after the configured idle timeout (default 60 minutes).
- **Continue**: always resume the current ChatGPT conversation when possible.
- **New**: start a fresh ChatGPT conversation on every summon.

The composer also has an explicit `+` action that forces a new conversation immediately. Closing Loom ends Voice but no longer destroys the current conversation. Session recency is stored locally in `~/.local/state/tabby/session.json`.

Optional **Startup instructions** are sent as the first user message only for genuinely new Loom conversations. Because Loom uses the normal ChatGPT web product rather than the API, these are conversation instructions rather than an API `system` role. Continuing an existing conversation never sends them again. Loom now prewarms this work while hidden: fresh chats are created, the instructions are acknowledged, and the conversation is persisted before a wake/hotkey consumes it.

**Text replies** can be `always`, `text-only` (default), or `never`. The bridge reads the latest assistant response from the same hidden ChatGPT conversation, so displaying text does not make a second model request.

The hover composer uses Caelestia's native text styling and vector-drawn controls, and its input uses the same Wayland `OnDemand` keyboard focus + Hyprland focus grab pattern as Caelestia's own interactive panels.

## Assistant identity

The assistant name, wake phrase, goodbye phrase, and comma-separated STT variants can be changed in Loom plugin settings. The ChatGPT startup guidance uses the configured name automatically. Legacy tabby_* MCP calls and the current token/socket/HTTP endpoint stay supported.

## Tasks tile

`LoomTasksCard.qml` is a native card showing Philipedia LOOM mission and idea
inbox states (ledger status/outcome only, no progress percentages). It
refreshes through `loom_tasks.py` while visible, persists its size in the saved
`tasks` module and can be shown in the panel with `qs ipc call loom toggleTasks`.
See `docs/TASKS_TILE.md`, including which QML parts are not runtime-verified.
