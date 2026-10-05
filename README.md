# CaelestiaPlugin-Tabby

Tabby is a standalone Caelestia companion that wraps the user's existing
ChatGPT session through a dedicated hidden Zen/Firefox tab.

Protocol7 is only the wake-word engine. When Protocol7 recognizes the configured
wake phrase it sends a small local `wake` event to Tabby's mode-0600 Unix socket.
Tabby owns everything after that point: fresh-chat creation, ChatGPT Voice,
state/animation, text input, image clipboard uploads, whiteboard/MCP and close /
auto-hide behavior.

## Interaction

- Say the Protocol7 wake phrase (default **Hey Tabby**) for a fresh Voice chat.
- Press **Super+Shift+Space** to summon a fresh text chat. Hover Tabby to reveal
  the composer.
- Press Enter to send text. Ctrl+V keeps normal text paste behavior and also
  attaches a clipboard image when one exists. The paperclip button does the same.
- Hover the face to reveal the close button.
- The mouth follows real PCM amplitude from the current PipeWire/Pulse output
  monitor while ChatGPT Voice is active.
- Thinking/tool work uses a separate orbit animation rather than speech motion.

## Browser engine

The Zen Sine bridge owns one dedicated Tabby ChatGPT tab. In normal mode Firefox
hides that tab. Debug mode reveals/selects the same tab without changing the
login session. Every Tabby summon navigates to a verified fresh `/` ChatGPT
composer before Voice or text input begins.

The bridge intentionally does not scrape conversation content. It uses semantic
controls to start/end Voice, set/send composer text, attach an explicitly pasted
image and expose only coarse status (`ready`, `active`, `working`, auth state).

## Local interfaces

`$XDG_RUNTIME_DIR/tabby.sock` is created with mode 0600. `tabbyctl.py` supports
`status`, `wake`, `close`, `toggle-input`, `send-text`, and `paste-clipboard`.

## Tabby MCP (one server)

`tabby_mcp.py` is the single Tabby MCP server (stdlib only). It exposes Tabby's
board (text, progress, status lines, cards, lists, shapes, clickable choices,
plus a generic `tabby_display` for any future widget type) and Working task
cards (create, pin current chat, update, done, reopen, open). There is no
shell, file or browser access: every tool is one request on `tabby.sock`.

- Local clients: `python3 tabby_mcp.py stdio` (`mcp_server.py` and
  `working_mcp_server.py` are kept as aliases).
- Remote (ChatGPT): `systemd/tabby-mcp.service` (user unit, enabled) serves
  Streamable HTTP on `:8766`, accepts only loopback/Tailscale sources and needs
  the token from `~/.config/tabby/mcp-token` as `/mcp/<token>` or
  `Authorization: Bearer <token>`. Philipedia's Traefik publishes it as
  `https://tabby-mcp.qwqc.de/mcp/<token>`, allowlisted to OpenAI's connector
  egress ranges (refreshed daily).
- New widget: add a delegate in `Panel.qml`; unknown item types already render
  with the generic title/text fallback. No new MCP server needed.


## Current runtime

- `Super+Shift+Space` is a true summon toggle: start Tabby Voice with the hover text input available; press it again to close Tabby completely.
- Debug mode reveals a minimal standalone Gecko ChatGPT engine window, not a normal Zen tab or browser window.
- Voice activation is asynchronous: Tabby clicks Voice, then tracks the fresh ChatGPT Voice surface until it becomes active.
- The speaking face uses live output audio amplitude.


## Session policy

Tabby supports three session modes from plugin settings:

- **Smart** (default): continue the current conversation while it is recent; start a fresh chat after the configured idle timeout (default 60 minutes).
- **Continue**: always resume the current ChatGPT conversation when possible.
- **New**: start a fresh ChatGPT conversation on every summon.

The composer also has an explicit `+` action that forces a new conversation immediately. Closing Tabby ends Voice but no longer destroys the current conversation. Session recency is stored locally in `~/.local/state/tabby/session.json`.

Optional **Startup instructions** are sent as the first user message only for genuinely new Tabby conversations. Because Tabby uses the normal ChatGPT web product rather than the API, these are conversation instructions rather than an API `system` role. Continuing an existing conversation never sends them again. Tabby now prewarms this work while hidden: fresh chats are created, the instructions are acknowledged, and the conversation is persisted before a wake/hotkey consumes it.

**Text replies** can be `always`, `text-only` (default), or `never`. The bridge reads the latest assistant response from the same hidden ChatGPT conversation, so displaying text does not make a second model request.

The hover composer uses Caelestia's native text styling and vector-drawn controls, and its input uses the same Wayland `OnDemand` keyboard focus + Hyprland focus grab pattern as Caelestia's own interactive panels.
