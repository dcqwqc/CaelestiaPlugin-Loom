import Caelestia.Plugins

SettingsObject {
    property bool enabled: true
    SettingMeta on enabled {
        label: "Enable Loom"
        description: "Show the Loom companion when summoned by Protocol7 or the input hotkey."
        icon: "smart_toy"
        inputType: SettingMeta.Switch
    }

    property string previousAssistantName: "Loom"
    property string assistantName: "Loom"
    onAssistantNameChanged: {
        const oldName = previousAssistantName.trim() || "Loom";
        const newName = assistantName.trim() || "Loom";
        if (wakePhrase === "Hey " + oldName) wakePhrase = "Hey " + newName;
        if (closePhrase === "Bye " + oldName) closePhrase = "Bye " + newName;
        if (newName !== "Loom") {
            if (wakeAliases === "Hey Lume, Hey Lumi, Hey Lum, Hey Loam, Hello Loom") wakeAliases = "";
            if (closeAliases === "Bye Lume, Bye Lum, Goodbye Loom, By Loom") closeAliases = "";
        }
        previousAssistantName = newName;
    }
    SettingMeta on assistantName {
        label: "Assistant name"
        description: "Display name and identity used in new conversations."
        icon: "badge"
        inputType: SettingMeta.TextField
    }

    property string wakePhrase: "Hey Loom"
    SettingMeta on wakePhrase {
        label: "Wake phrase"
        description: "Spoken phrase recognized by Protocol7. Example: Hey Loom."
        icon: "mic"
        inputType: SettingMeta.TextField
    }

    property string closePhrase: "Bye Loom"
    SettingMeta on closePhrase {
        label: "Goodbye phrase"
        description: "Spoken phrase to end the session. Leave blank to disable."
        icon: "mic_off"
        inputType: SettingMeta.TextField
    }

    property string wakeAliases: "Hey Lume, Hey Lumi, Hey Lum, Hey Loam, Hello Loom"
    SettingMeta on wakeAliases {
        label: "Wake phrase variations"
        description: "Comma-separated pronunciation and transcription alternatives. Exact phrases trigger immediately."
        icon: "record_voice_over"
        inputType: SettingMeta.TextField
    }

    property string closeAliases: "Bye Lume, Bye Lum, Goodbye Loom, By Loom"
    SettingMeta on closeAliases {
        label: "Goodbye variations"
        description: "Comma-separated alternative goodbye phrases."
        icon: "record_voice_over"
        inputType: SettingMeta.TextField
    }

    property bool debugEngine: false
    SettingMeta on debugEngine {
        label: "Show Voice engine (Debug)"
        description: "Reveal Loom's minimal standalone ChatGPT engine window for debugging. Normally this can stay off."
        icon: "bug_report"
        inputType: SettingMeta.Switch
    }


    property string hotkeyMode: "double-left-alt"
    SettingMeta on hotkeyMode {
        label: "Summon hotkey"
        description: "Choose Double Left Alt, Double Fn, Super+Shift+Space, or Both. Mirai supports Double Left Alt natively; bare Fn is hidden by Lenovo firmware."
        icon: "keyboard_command_key"
        inputType: SettingMeta.SplitButton
        options: ["double-left-alt", "double-fn", "super-shift-space", "both"]
    }

    property int doubleTapMs: 350
    SettingMeta on doubleTapMs {
        label: "Double-tap window"
        description: "Maximum milliseconds between two standalone Left Alt or Fn taps."
        icon: "speed"
        inputType: SettingMeta.SpinBox
        min: 150
        max: 800
        step: 25
    }

    property string sessionMode: "continue"
    SettingMeta on sessionMode {
        label: "Chat session mode"
        description: "Smart continues recent chats, Continue always resumes the current chat, New always starts fresh."
        icon: "forum"
        inputType: SettingMeta.SplitButton
        options: ["smart", "continue", "new"]
    }

    property int smartNewChatMinutes: 60
    SettingMeta on smartNewChatMinutes {
        label: "Smart new-chat timeout"
        description: "In Smart mode, start a fresh chat after this many idle minutes."
        icon: "schedule"
        inputType: SettingMeta.SpinBox
        min: 5
        max: 1440
        step: 5
    }

    property bool backgroundPrewarmEnabled: false
    SettingMeta on backgroundPrewarmEnabled {
        label: "Prepare chat while hidden"
        description: "Optional navigation-only prewarm. Never sends messages or creates chats in background."
        icon: "speed"
        inputType: SettingMeta.Switch
    }

    property bool startupPromptEnabled: true
    SettingMeta on startupPromptEnabled {
        label: "Startup instructions"
        description: "Send Loom's guidance as the first message only when a genuinely new chat is created."
        icon: "prompt_suggestion"
        inputType: SettingMeta.Switch
    }

    property string startupPrompt: "# LOOM\n\nYou are **Loom**, the user's proactive desktop companion and AI orchestrator, integrated into QuickShell/Celestia. You are the user's digital hands: translate natural intentions into actions, coordinate tools and workers, and carry work through to verified results.\n\n**Act on intent.** Understand conversational requests in context, including implied actions. Prefer doing the work over explaining how to do it. Use available MCP and application tools, make reasonable reversible decisions independently, and present results where they're useful. Ask only when essential information or consequential authorization is missing. For requests seeking only information or discussion, don't make unrelated changes.\n\n**Orchestrate intelligently.** Handle simple actions directly. Delegate substantial or independent work to appropriate available agents, parallelizing when useful. Capture every actionable idea in persistent task state before delegation, even when the user gives many ideas at once. Keep accepting new input while workers operate. Manage task identities, priorities, dependencies, execution, progress, retries, reviews, and completion through the actual orchestration system.\n\n**Expand the system when needed.** If the user requests a feature, integration, tool, or ongoing behavior that is missing, first use existing supported tools if possible. Otherwise create or reuse one durable, specific capability-gap task and delegate it to an actually available authorized coding or ChatGPT web worker as appropriate. Use `loom_capability_request` for a scoped coding gap when available, `loom_web_worker_create` only when its browser bridge is verified live, and `loom_idea_capture` when execution is blocked. Track the same task through implementation, testing, independent review, deployment, and user-visible verification. A created chat or mission is not completion. Avoid duplicate chats, retries that resend uncertain work, false claims of execution, and attempts to bypass unavailable permissions or platform restrictions. Persist honest blockers.\n\n**File ChatGPT work by lifecycle.** Loom keeps ChatGPT chats in five ChatGPT projects: New, Vault, Working, Blocked and Done. There is no ChatGPT project API; Loom moves chats through its Zen browser bridge and accepts a move only when the chat's own route shows the exact target project id. `loom_web_worker_create` files a blank chat into New, moves it to Working, verifies the Working project id, and only then sends the task exactly once; reuse its `request_id` on retries and never resend uncertain work. Use `loom_web_worker_route` to file a worker into Blocked or Vault (with a reason) or back into Working, and `loom_chat_route` for an existing or the current chat. Only `loom_web_worker_review` with an independent reviewer and evidence moves work to Done. If a project is missing or a move cannot be verified, report the blocker instead of proceeding.\n\n**Own the outcome.** Don't stop at starting a worker or producing a first implementation. Follow through, inspect results, test what matters, resolve failures, and verify the intended outcome. Preserve existing work and respect authorization boundaries. Never confuse assigned, running, finished, verified, and deployed. Use supported handoffs to continue work across sessions.\n\n**Finish the requested outcome, not merely an action.** Before using a tool, identify the user's intended end state and what observable evidence would prove it happened. Execute all reasonable intermediate steps without handing them back to the user. A launched app, successful command, submitted worker, or displayed search is progress, not completion. For example, “play Fantastic Fungi” requires finding the correct film, selecting a usable provider, opening it in Showtime, and verifying actual playback—not just opening Kunai or searching. If a step fails, diagnose and attempt safe alternatives, then check again. Keep persistent work open until the end state is verified; if verification is unavailable, explicitly report what was achieved, what remains unverified, and any blocker. Never claim success from a tool exit code alone. Respect user control and authorization boundaries.\n\n**Make progress visible.** Use Loom's interface and MCP to provide immediate, truthful feedback. Keep the desktop unobtrusive and follow Celestia's native design language, colors, and interaction patterns. Use contextual panels, task displays, and reusable UI modules when available. Retrieve relevant project and device context as needed; don't assume unavailable capabilities exist.\n\n**Be natural.** Speak briefly and conversationally, especially in voice. Match the user's language and focus. Avoid unnecessary questions, lengthy explanations, startup acknowledgments, and repetitive status messages. Surface decisions and genuine blockers clearly.\n\nYour purpose is simple: **the user thinks of what they want; you organize and execute what it takes to achieve it.**"
    SettingMeta on startupPrompt {
        label: "Loom instructions"
        description: "Conversation guidance sent at the beginning of new Loom chats. This is a first user instruction, not an API-level system role."
        icon: "edit_note"
        inputType: SettingMeta.TextField
    }

    property string textReplyMode: "text-only"
    SettingMeta on textReplyMode {
        label: "Text replies"
        description: "Always shows text for every Loom answer, Text only shows it after typed requests, Never keeps replies voice-only."
        icon: "chat_bubble"
        inputType: SettingMeta.SplitButton
        options: ["always", "text-only", "never"]
    }

    property int autoHideSeconds: 5
    SettingMeta on autoHideSeconds {
        label: "Auto-hide delay"
        description: "Seconds before an idle Loom disappears."
        icon: "timer_off"
        inputType: SettingMeta.SpinBox
        min: 2
        max: 30
        step: 1
    }

    property int mouthSensitivity: 180
    SettingMeta on mouthSensitivity {
        label: "Mouth sensitivity"
        description: "How strongly Loom's mouth reacts to ChatGPT audio output."
        icon: "graphic_eq"
        inputType: SettingMeta.SpinBox
        min: 50
        max: 400
        step: 10
    }

    property bool hoverTextInput: true
    SettingMeta on hoverTextInput {
        label: "Hover text input"
        description: "After the input hotkey summons Loom, reveal the text composer when you hover him."
        icon: "keyboard"
        inputType: SettingMeta.Switch
    }
}
