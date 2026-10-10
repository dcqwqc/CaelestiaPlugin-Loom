# Bundled Zen Voice Bridge

Zen/Sine browser-side bridge bundled with `CaelestiaPlugin-Loom`. It drives the real ChatGPT web Voice UI through a privileged `JSWindowActor` while keeping the companion engine isolated from the user's normal browsing window.

## Runtime architecture

Loom uses a **real Zen browser window**, not an embedded XUL `<browser>` WebView. The engine is marked persistently, routed to Hyprland `special:tabby`, and kept rendering while unfocused. This matters because Gecko/WebRTC would allow microphone permission in the old embedded WebView but `getUserMedia({audio:true})` could still stall indefinitely.

The current Voice startup path is:

`Loom backend -> Sine bridge -> hidden native Zen window -> internal content focus -> ChatGPT Voice control -> live WebRTC microphone stream`

The bridge grants the ChatGPT microphone permission for the engine principal, focuses the hidden content document internally, activates the semantic ChatGPT Voice control, waits for Voice or a live audio track, then allows the desktop integration to restore compositor focus to the user's previous window. The engine itself remains on `special:tabby`; it does not need to appear on the visible workspace.

## Conversation lifecycle

`continue-chat`, `new-chat`, and `open-chat` operate on the same dedicated engine window. `end` stops Voice and returns to the canonical `https://chatgpt.com/?tabby=1` composer. Engine identity is persisted through Firefox SessionStore so a restored Zen session can be recognized and repaired after restart.

The bridge also exposes the latest assistant response from the active conversation for Loom's `always / text-only / never` text-display modes. This does not create a second model request.

## Working windows

Background Working tasks still use separate isolated engine windows and are routed to `special:tabby-work`. They are distinct from the main Voice engine.

## Deployment

The Loom plugin deploys this directory into the active Zen profile automatically when its bundled bridge differs from the installed copy. For manual repair, run `bridge/zen/scripts/install.sh`. Restart Zen or toggle the Sine mod after JavaScript/actor code changes.

Current bridge generation: **0.11.0**. The engine window is a real Zen window rendered without Zen chrome (no sidebar or toolbars), so it looks like the bare worker windows. Windows are titled `Loom Engine · …` and `Loom Work · <task>`.

Legacy browser actor names and the tabby route are retained internally for compatibility. The assistant name is Loom.
