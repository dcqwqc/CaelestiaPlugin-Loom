pragma Singleton
import QtQuick

QtObject {
    property bool backendConnected: false
    property bool enabled: true
    property bool summoned: false
    property string state: "idle"
    property bool inputArmed: false
    property real audioLevel: 0
    property bool attachmentPending: false
    property bool whiteboardVisible: false
    property var items: []
    property int sequence: 0
    property int messagesReceived: 0

    function applyMessage(line: string): void {
        const prefix = "TABBY_STATE ";
        if (!line || !line.startsWith(prefix)) return;
        try {
            const message = JSON.parse(line.slice(prefix.length));
            backendConnected = true;
            enabled = message.enabled !== false;
            summoned = message.summoned === true;
            state = String(message.state ?? "idle");
            inputArmed = message.inputArmed === true;
            audioLevel = Math.max(0, Math.min(1, Number(message.audioLevel ?? 0)));
            attachmentPending = message.attachmentPending === true;
            whiteboardVisible = message.whiteboardVisible === true;
            items = Array.isArray(message.items) ? message.items : [];
            sequence = Number(message.sequence ?? sequence);
            messagesReceived += 1;
        } catch (error) {
            console.warn("Tabby state parse failed:", error);
        }
    }

    function reset(): void {
        backendConnected = false;
        summoned = false;
        state = "idle";
        inputArmed = false;
        audioLevel = 0;
        attachmentPending = false;
        whiteboardVisible = false;
        items = [];
    }
}
