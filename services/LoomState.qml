pragma Singleton
import QtQuick

QtObject {
    property bool backendConnected: false
    property bool enabled: true
    property bool summoned: false
    property bool voiceActive: false
    property string state: "idle"
    property bool inputArmed: false
    property real audioLevel: 0
    property bool attachmentPending: false
    property bool whiteboardVisible: false
    property var items: []
    property var working: []
    property bool fnHotkeyAvailable: false
    property bool altHotkeyAvailable: false
    property int sequence: 0
    property int messagesReceived: 0

    // Tasks tile. Fed by Main.qml through loom_tasks.py, not by the backend, so
    // backend restarts (reset()) leave the last ledger snapshot in place.
    property var tasks: ({ missions: [], ideas: [], counts: {}, stale: true, errors: [], fetched_at: null })
    property var tasksTile: null
    property bool tasksRefreshing: false
    property bool tasksPanelVisible: false
    property int tasksViewers: 0
    property int tasksRefreshRequests: 0

    function applyTasks(line: string): void {
        try {
            const snapshot = JSON.parse(line);
            if (!snapshot || snapshot.version !== 1) return;
            tasks = {
                missions: Array.isArray(snapshot.missions) ? snapshot.missions : [],
                ideas: Array.isArray(snapshot.ideas) ? snapshot.ideas : [],
                counts: snapshot.counts ?? {},
                stale: snapshot.stale !== false,
                errors: Array.isArray(snapshot.errors) ? snapshot.errors : [],
                fetched_at: typeof snapshot.fetched_at === "number" ? snapshot.fetched_at : null
            };
        } catch (error) {
            console.warn("Loom tasks parse failed:", error);
        }
    }

    function applyTile(line: string): void {
        try {
            const module = JSON.parse(line);
            if (module && module.kind === "tasks" && module.placement)
                tasksTile = module;
        } catch (error) {
            console.warn("Loom tasks tile parse failed:", error);
        }
    }

    function requestTasksRefresh(): void {
        tasksRefreshRequests += 1;
    }

    function applyMessage(line: string): void {
        const prefix = "TABBY_STATE ";
        if (!line || !line.startsWith(prefix)) return;
        try {
            const message = JSON.parse(line.slice(prefix.length));
            backendConnected = true;
            enabled = message.enabled !== false;
            summoned = message.summoned === true;
            voiceActive = message.voiceActive === true;
            state = String(message.state ?? "idle");
            inputArmed = message.inputArmed === true;
            audioLevel = Math.max(0, Math.min(1, Number(message.audioLevel ?? 0)));
            attachmentPending = message.attachmentPending === true;
            whiteboardVisible = message.whiteboardVisible === true;
            items = Array.isArray(message.items) ? message.items : [];
            working = Array.isArray(message.working) ? message.working : [];
            fnHotkeyAvailable = Boolean(message.fnHotkeyAvailable ?? fnHotkeyAvailable);
            altHotkeyAvailable = Boolean(message.altHotkeyAvailable ?? altHotkeyAvailable);
            sequence = Number(message.sequence ?? sequence);
            messagesReceived += 1;
        } catch (error) {
            console.warn("Loom state parse failed:", error);
        }
    }

    function reset(): void {
        backendConnected = false;
        summoned = false;
        voiceActive = false;
        state = "idle";
        inputArmed = false;
        audioLevel = 0;
        attachmentPending = false;
        whiteboardVisible = false;
        items = [];
        working = [];
        fnHotkeyAvailable = false;
        altHotkeyAvailable = false;
    }
}
