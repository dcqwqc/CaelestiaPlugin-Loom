pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import qs.components.misc
import qs.utils
import dcqwqc.tabby.services as T

Scope {
    id: root
    property var settings: null

    readonly property string python: "/usr/bin/python3"
    readonly property string backendPath: Paths.toLocalFile(Qt.resolvedUrl("backend.py"))
    readonly property string ctlPath: Paths.toLocalFile(Qt.resolvedUrl("tabbyctl.py"))
    readonly property string configBridge: Paths.toLocalFile(Qt.resolvedUrl("config_bridge.py"))
    readonly property string configPath: `${Quickshell.env("HOME")}/.config/tabby/config.json`
    property bool writeQueued: false
    property bool backendWanted: true

    readonly property string patchJson: settings ? JSON.stringify({
        enabled: settings.enabled,
        debug_engine: settings.debugEngine,
        auto_hide_seconds: settings.autoHideSeconds,
        mouth_sensitivity: settings.mouthSensitivity / 100.0,
        hover_text_input: settings.hoverTextInput,
        session_mode: settings.sessionMode,
        smart_new_chat_minutes: settings.smartNewChatMinutes,
        startup_prompt_enabled: settings.startupPromptEnabled,
        startup_prompt: settings.startupPrompt
    }) : "{}"

    function applySettings(): void {
        if (!settings) return;
        if (writeConfig.running) {
            writeQueued = true;
            return;
        }
        writeConfig.running = true;
    }

    function control(command: string, extra: var): void {
        const args = [root.python, root.ctlPath, command];
        if (extra !== undefined && extra !== null && String(extra).length > 0)
            args.push(String(extra));
        Quickshell.execDetached(args);
    }

    Process {
        id: writeConfig
        command: [root.python, root.configBridge, root.configPath, root.patchJson]
        onExited: code => {
            if (code !== 0) return;
            if (root.writeQueued) {
                root.writeQueued = false;
                Qt.callLater(() => writeConfig.running = true);
                return;
            }
            backend.running = false;
            restartTimer.restart();
        }
    }

    Timer {
        id: restartTimer
        interval: 250
        repeat: false
        onTriggered: backend.running = true
    }

    Process {
        id: backend
        command: [root.python, root.backendPath]
        stdout: SplitParser {
            splitMarker: "\n"
            onRead: data => T.TabbyState.applyMessage(data)
        }
        stderr: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                if (data.trim() !== "") console.warn("Tabby backend:", data.trim())
            }
        }
        onExited: T.TabbyState.reset()
    }

    CustomShortcut {
        name: "tabbyInput"
        description: "Toggle Tabby Voice + text"
        onPressed: root.control("toggle", null)
    }

    IpcHandler {
        target: "tabby"
        function wake(): string { root.control("wake", null); return "queued"; }
        function close(): string { root.control("close", null); return "queued"; }
        function toggleInput(): string { root.control("toggle", null); return "queued"; }
        function newChat(): string { root.control("new-session", null); return "queued"; }
        function pasteClipboard(): string { root.control("paste-clipboard", null); return "queued"; }
        function sendText(text: string): string { root.control("send-text", text); return "queued"; }
        function debug(): string {
            return [
                `connected=${T.TabbyState.backendConnected}`,
                `summoned=${T.TabbyState.summoned}`,
                `state=${T.TabbyState.state}`,
                `inputArmed=${T.TabbyState.inputArmed}`,
                `audioLevel=${T.TabbyState.audioLevel}`,
                `attachment=${T.TabbyState.attachmentPending}`
            ].join("\n");
        }
    }

    onSettingsChanged: applySettings()
    Connections {
        target: settings
        enabled: settings !== null
        function onEnabledChanged(): void { root.applySettings(); }
        function onDebugEngineChanged(): void { root.applySettings(); }
        function onAutoHideSecondsChanged(): void { root.applySettings(); }
        function onMouthSensitivityChanged(): void { root.applySettings(); }
        function onHoverTextInputChanged(): void { root.applySettings(); }
        function onSessionModeChanged(): void { root.applySettings(); }
        function onSmartNewChatMinutesChanged(): void { root.applySettings(); }
        function onStartupPromptEnabledChanged(): void { root.applySettings(); }
        function onStartupPromptChanged(): void { root.applySettings(); }
    }

    Component.onCompleted: applySettings()
    Component.onDestruction: backend.running = false
}
