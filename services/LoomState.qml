pragma Singleton
import QtQuick
import QtQml.Models

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
    property var uiViews: []
    property var working: []
    property var notifications: []
    property bool fnHotkeyAvailable: false
    property bool altHotkeyAvailable: false
    property int sequence: 0
    property int messagesReceived: 0

    // Tasks tile. Fed by Main.qml through loom_tasks.py, not by the backend, so
    // backend restarts (reset()) leave the last ledger snapshot in place.
    property var tasks: ({ missions: [], ideas: [], counts: {}, stale: true, errors: [], fetched_at: null })
    property var tasksTile: null
    // A keyed ListModel lets Instantiator retain delegate/window identity while
    // the registry is polled. Replacing a plain JS array here tears down every
    // PanelWindow, including one with an active drag or resize gesture.
    property ListModel surfaceModules: ListModel { dynamicRoles: true }
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

    function applySurfaceModules(line: string): void {
        try {
            const snapshot = JSON.parse(line);
            if (!snapshot || snapshot.version !== 1 || !Array.isArray(snapshot.modules)) return;
            reconcileSurfaceModules(snapshot.modules);
        } catch (error) {
            console.warn("Loom surface modules parse failed:", error);
        }
    }

    function reconcileSurfaceModules(modules: var): void {
        for (let wanted = 0; wanted < modules.length; wanted++) {
            const incoming = modules[wanted];
            let found = -1;
            for (let current = wanted; current < surfaceModules.count; current++) {
                if (surfaceModules.get(current).module.id === incoming.id) {
                    found = current;
                    break;
                }
            }
            if (found < 0) {
                surfaceModules.append({ module: incoming });
                found = surfaceModules.count - 1;
            }
            if (found !== wanted) surfaceModules.move(found, wanted, 1);
            const existing = surfaceModules.get(wanted).module;
            if (JSON.stringify(existing) !== JSON.stringify(incoming))
                surfaceModules.setProperty(wanted, "module", incoming);
        }
        if (surfaceModules.count > modules.length)
            surfaceModules.remove(modules.length, surfaceModules.count - modules.length);
    }

    function replaceSurfaceModule(module: var): void {
        for (let i = 0; i < surfaceModules.count; i++) {
            if (surfaceModules.get(i).module.id !== module.id) continue;
            if (JSON.stringify(surfaceModules.get(i).module) !== JSON.stringify(module))
                surfaceModules.setProperty(i, "module", module);
            break;
        }
        if (tasksTile?.id === module.id) tasksTile = module;
    }

    // Idempotent per-card registration: returns the card's new registered
    // flag, so repeated or construction-time visibility events cannot skew the
    // count, and it never drops below zero.
    function setTasksViewer(registered: bool, wanted: bool): bool {
        if (registered === wanted) return registered;
        const hadViewers = tasksViewers > 0;
        tasksViewers = Math.max(0, tasksViewers + (wanted ? 1 : -1));
        if (!hadViewers && tasksViewers > 0) requestTasksRefresh();
        return wanted;
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
            uiViews = Array.isArray(message.uiViews) ? message.uiViews : [];
            working = Array.isArray(message.working) ? message.working : [];
            notifications = Array.isArray(message.notifications) ? message.notifications : [];
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
        uiViews = [];
        working = [];
        notifications = [];
        fnHotkeyAvailable = false;
        altHotkeyAvailable = false;
    }
}
