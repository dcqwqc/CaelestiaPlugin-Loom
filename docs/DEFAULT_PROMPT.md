# Loom default conversation guidance

`prompts/default.md` is the authoritative approved, concise Loom persona and operating guidance. `backend.py` loads it as the default, while `Settings.qml` embeds the same string so the plugin settings UI exposes an editable copy. A regression test checks they match.

The plugin sends this guidance **only when a genuinely new Loom conversation is created**. It does not create background chats, repeat guidance on an existing conversation, or require a LOOM_READY acknowledgment. Preserve `session_mode: continue` for normal wakeups; the explicit Loom newChat action creates a new conversation on request.

**Important:** ChatGPT's consumer browser application does not expose a supported API to set a true system-role message. This integration uses the first user message for Loom conversation guidance and accurately labels it that way. Existing conversations are not retroactively modified. New default settings apply to new installations; the user's existing Celestia plugin settings and the backend config need to be synchronized for immediate rollout.

Validation: `python3 -m unittest discover -s tests`; then inspect the new persisted conversation URL and the running backend's prompt settings before claiming a new session was created.

