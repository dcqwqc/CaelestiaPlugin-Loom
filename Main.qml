pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import qs.components.misc
import qs.utils
import dcqwqc.loom.services as T

Scope {
    id: root
    property var settings: null

    readonly property string python: "/usr/bin/python3"
    readonly property string backendPath: Paths.toLocalFile(Qt.resolvedUrl("backend.py"))
    readonly property string ctlPath: Paths.toLocalFile(Qt.resolvedUrl("loomctl.py"))
    readonly property string tasksPath: Paths.toLocalFile(Qt.resolvedUrl("loom_tasks.py"))
    readonly property string configBridge: Paths.toLocalFile(Qt.resolvedUrl("config_bridge.py"))
    readonly property string configPath: `${Quickshell.env("HOME")}/.config/tabby/config.json`
    property bool writeQueued: false
    property bool backendWanted: true

    Loader {
        active: true
        source: Qt.resolvedUrl("FloatingWidgets.qml")
    }

    readonly property string patchJson: settings ? JSON.stringify({
        enabled: settings.enabled,
        assistant_name: settings.assistantName,
        wake_phrase: settings.wakePhrase,
        close_phrase: settings.closePhrase,
        wake_aliases: settings.wakeAliases,
        close_aliases: settings.closeAliases,
        debug_engine: settings.debugEngine,
        auto_hide_seconds: settings.autoHideSeconds,
        mouth_sensitivity: settings.mouthSensitivity / 100.0,
        hover_text_input: settings.hoverTextInput,
        session_mode: settings.sessionMode,
        smart_new_chat_minutes: settings.smartNewChatMinutes,
        startup_prompt_enabled: settings.startupPromptEnabled,
        startup_prompt: settings.startupPrompt,
        text_reply_mode: settings.textReplyMode,
        hotkey_mode: settings.hotkeyMode,
        double_tap_ms: settings.doubleTapMs,
        fn_double_tap_ms: settings.doubleTapMs
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
            onRead: data => T.LoomState.applyMessage(data)
        }
        stderr: SplitParser {
            splitMarker: "\n"
            onRead: data => {
                if (data.trim() !== "") console.warn("Loom backend:", data.trim())
            }
        }
        onExited: {
            T.LoomState.reset();
            if (root.backendWanted && !restartTimer.running)
                restartTimer.restart();
        }
    }

    // Tasks tile lifecycle: cached snapshot + saved module at start, then a
    // bounded SSH refresh (fixed host, see tabby/missions.py) only while a card
    // is visible. Never overlapping: a running refresh is not restarted.
    function refreshTasks(): void {
        if (tasksRefresh.running) return;
        T.LoomState.tasksRefreshing = true;
        tasksRefresh.running = true;
    }

    Process {
        id: tasksSnapshot
        command: [root.python, root.tasksPath, "snapshot"]
        stdout: SplitParser { onRead: data => T.LoomState.applyTasks(data) }
    }

    Process {
        id: tasksTileProc
        command: [root.python, root.tasksPath, "tile"]
        stdout: SplitParser {
            onRead: data => {
                T.LoomState.applyTile(data);
                surfaceModules.running = true;
            }
        }
    }

    Process {
        id: surfaceModules
        command: [root.python, root.tasksPath, "modules"]
        stdout: SplitParser { onRead: data => T.LoomState.applySurfaceModules(data) }
    }

    Timer {
        interval: 2000
        repeat: true
        running: true
        onTriggered: if (!surfaceModules.running) surfaceModules.running = true
    }

    Process {
        id: tasksRefresh
        command: [root.python, root.tasksPath, "refresh"]
        stdout: SplitParser { onRead: data => T.LoomState.applyTasks(data) }
        stderr: SplitParser {
            onRead: data => {
                if (data.trim() !== "") console.warn("Loom tasks:", data.trim())
            }
        }
        onExited: T.LoomState.tasksRefreshing = false
    }

    Timer {
        interval: 60000
        repeat: true
        triggeredOnStart: true
        running: T.LoomState.tasksViewers > 0
        onTriggered: root.refreshTasks()
    }

    Connections {
        target: T.LoomState
        function onTasksRefreshRequestsChanged(): void { root.refreshTasks(); }
    }

    CustomShortcut {
        name: "loomInput"
        description: "Toggle Loom Voice + text"
        onPressed: root.control("toggle-fallback", null)
    }

    IpcHandler {
        target: "loom"
        function wake(): string { root.control("wake", null); return "queued"; }
        function close(): string { root.control("close", null); return "queued"; }
        function toggleInput(): string { root.control("toggle", null); return "queued"; }
        function newChat(): string { root.control("new-session", null); return "queued"; }
        function pasteClipboard(): string { root.control("paste-clipboard", null); return "queued"; }
        function sendText(text: string): string { root.control("send-text", text); return "queued"; }
        function toggleTasks(): string {
            T.LoomState.tasksPanelVisible = !T.LoomState.tasksPanelVisible;
            return T.LoomState.tasksPanelVisible ? "enabled on counter hover" : "disabled";
        }
        function refreshTasks(): string { root.refreshTasks(); return "queued"; }
        function tasks(): string {
            const t = T.LoomState.tasks;
            const counts = Object.keys(t.counts).map(k => `${k}=${t.counts[k]}`).join(" ");
            return [
                `missions=${t.missions.length} ideas=${t.ideas.length}`,
                `states=${counts}`,
                `stale=${t.stale} fetchedAt=${t.fetched_at}`,
                `errors=${t.errors.join("; ")}`
            ].join("\n");
        }
        function debug(): string {
            return [
                `connected=${T.LoomState.backendConnected}`,
                `summoned=${T.LoomState.summoned}`,
                `state=${T.LoomState.state}`,
                `inputArmed=${T.LoomState.inputArmed}`,
                `audioLevel=${T.LoomState.audioLevel}`,
                `attachment=${T.LoomState.attachmentPending}`,
                `fnHotkeyAvailable=${T.LoomState.fnHotkeyAvailable}`,
                `altHotkeyAvailable=${T.LoomState.altHotkeyAvailable}`
            ].join("\n");
        }
    }

    // Compatibility for clients configured during the brief Lume naming period.
    CustomShortcut {
        name: "lumeInput"
        description: "Legacy shortcut for Loom"
        onPressed: root.control("toggle-fallback", null)
    }
    IpcHandler {
        target: "lume"
        function wake(): string { root.control("wake", null); return "queued"; }
        function close(): string { root.control("close", null); return "queued"; }
        function toggleInput(): string { root.control("toggle", null); return "queued"; }
    }

    // Compatibility for existing Caelestia shell scripts and hotkey profiles.
    CustomShortcut {
        name: "tabbyInput"
        description: "Legacy input shortcut for Loom"
        onPressed: root.control("toggle-fallback", null)
    }
    IpcHandler {
        target: "tabby"
        function wake(): string { root.control("wake", null); return "queued"; }
        function close(): string { root.control("close", null); return "queued"; }
        function toggleInput(): string { root.control("toggle", null); return "queued"; }
        function newChat(): string { root.control("new-session", null); return "queued"; }
        function pasteClipboard(): string { root.control("paste-clipboard", null); return "queued"; }
        function sendText(text: string): string { root.control("send-text", text); return "queued"; }
    }

    onSettingsChanged: applySettings()
    Connections {
        target: settings
        enabled: settings !== null
        function onEnabledChanged(): void { root.applySettings(); }
        function onAssistantNameChanged(): void { root.applySettings(); }
        function onWakePhraseChanged(): void { root.applySettings(); }
        function onClosePhraseChanged(): void { root.applySettings(); }
        function onWakeAliasesChanged(): void { root.applySettings(); }
        function onCloseAliasesChanged(): void { root.applySettings(); }
        function onDebugEngineChanged(): void { root.applySettings(); }
        function onAutoHideSecondsChanged(): void { root.applySettings(); }
        function onMouthSensitivityChanged(): void { root.applySettings(); }
        function onHoverTextInputChanged(): void { root.applySettings(); }
        function onSessionModeChanged(): void { root.applySettings(); }
        function onSmartNewChatMinutesChanged(): void { root.applySettings(); }
        function onStartupPromptEnabledChanged(): void { root.applySettings(); }
        function onStartupPromptChanged(): void { root.applySettings(); }
        function onTextReplyModeChanged(): void { root.applySettings(); }
        function onHotkeyModeChanged(): void { root.applySettings(); }
        function onDoubleTapMsChanged(): void { root.applySettings(); }
    }

    Component.onCompleted: {
        applySettings();
        tasksSnapshot.running = true;
        tasksTileProc.running = true;
    }
    Component.onDestruction: {
        backendWanted = false;
        restartTimer.stop();
        backend.running = false;
        tasksRefresh.running = false;
    }
}
