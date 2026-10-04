import Caelestia.Plugins

SettingsObject {
    property bool enabled: true
    SettingMeta on enabled {
        label: "Enable Tabby"
        description: "Show the Tabby companion when summoned by Protocol7 or the input hotkey."
        icon: "smart_toy"
        inputType: SettingMeta.Switch
    }

    property bool debugEngine: true
    SettingMeta on debugEngine {
        label: "Show Voice engine (Debug)"
        description: "Reveal Tabby's minimal standalone ChatGPT engine window for debugging. Normally this can stay off."
        icon: "bug_report"
        inputType: SettingMeta.Switch
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
