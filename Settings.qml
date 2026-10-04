import Caelestia.Plugins

SettingsObject {
    property bool enabled: true
    SettingMeta on enabled {
        label: "Enable Tabby"
        description: "Show the Tabby companion when summoned by Protocol7 or the input hotkey."
        icon: "smart_toy"
        inputType: SettingMeta.Switch
    }

    property bool debugEngine: false
    SettingMeta on debugEngine {
        label: "Show Voice engine (Debug)"
        description: "Reveal Tabby's minimal standalone ChatGPT engine window for debugging. Normally this can stay off."
        icon: "bug_report"
        inputType: SettingMeta.Switch
    }


    property string sessionMode: "smart"
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

    property bool startupPromptEnabled: true
    SettingMeta on startupPromptEnabled {
        label: "Startup instructions"
        description: "Send Tabby's guidance as the first message only when a genuinely new chat is created."
        icon: "prompt_suggestion"
        inputType: SettingMeta.Switch
    }

    property string startupPrompt: "You are Tabby, my desktop companion. Keep voice replies concise and natural. Use available tools when I ask you to act on my computer. Treat these as guidance for this conversation and do not explain them unless I ask."
    SettingMeta on startupPrompt {
        label: "Tabby instructions"
        description: "Conversation guidance sent at the beginning of new Tabby chats. This is a first user instruction, not an API-level system role."
        icon: "edit_note"
        inputType: SettingMeta.TextField
    }

    property string textReplyMode: "text-only"
    SettingMeta on textReplyMode {
        label: "Text replies"
        description: "Always shows text for every Tabby answer, Text only shows it after typed requests, Never keeps replies voice-only."
        icon: "chat_bubble"
        inputType: SettingMeta.SplitButton
        options: ["always", "text-only", "never"]
    }

    property int autoHideSeconds: 5
    SettingMeta on autoHideSeconds {
        label: "Auto-hide delay"
        description: "Seconds before an idle Tabby disappears."
        icon: "timer_off"
        inputType: SettingMeta.SpinBox
        min: 2
        max: 30
        step: 1
    }

    property int mouthSensitivity: 180
    SettingMeta on mouthSensitivity {
        label: "Mouth sensitivity"
        description: "How strongly Tabby's mouth reacts to ChatGPT audio output."
        icon: "graphic_eq"
        inputType: SettingMeta.SpinBox
        min: 50
        max: 400
        step: 10
    }

    property bool hoverTextInput: true
    SettingMeta on hoverTextInput {
        label: "Hover text input"
        description: "After the input hotkey summons Tabby, reveal the text composer when you hover him."
        icon: "keyboard"
        inputType: SettingMeta.Switch
    }
}
