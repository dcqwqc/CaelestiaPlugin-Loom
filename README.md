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

`mcp_server.py` exposes the whiteboard tools plus `tabby_wake`, `tabby_close`
and `tabby_send_text`.


## Current runtime

- `Super+Shift+Space` is a true toggle: open Tabby text input, press it again to close Tabby completely.
- Debug mode reveals a minimal standalone Gecko ChatGPT engine window, not a normal Zen tab or browser window.
- Voice activation is asynchronous: Tabby clicks Voice, then tracks the fresh ChatGPT Voice surface until it becomes active.
- The speaking face uses live output audio amplitude.
