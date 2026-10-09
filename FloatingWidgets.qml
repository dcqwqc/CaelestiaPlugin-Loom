pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Services.UPower
import Caelestia.Config
import Caelestia.Services
import qs.components
import qs.services
import qs.utils
import dcqwqc.loom.services as T

// Reusable native host for every saved Performance/floating module kind.
Scope {
    id: root
    readonly property string python: "/usr/bin/python3"
    readonly property string tasksPath: Paths.toLocalFile(Qt.resolvedUrl("loom_tasks.py"))

    function geometry(anchor: string, x: real, y: real): var {
        const a = anchor === "free" ? "top-left" : anchor;
        return {
            top: a.startsWith("top") || a === "center", bottom: a.startsWith("bottom"),
            left: a.endsWith("left") || a === "center", right: a.endsWith("right"),
            centered: a === "center",
            horizontal: a === "center" ? Math.round(x) : Math.max(0, Math.round(x)),
            vertical: a === "center" ? Math.round(y) : Math.max(0, Math.round(y))
        };
    }

    function clampOffset(value: real, extent: real, size: real): real {
        return Math.max(0, Math.min(Math.round(value), Math.max(0, Math.round(extent - size))));
    }

    function persist(module: var, command: string, values: var): void {
        const args = [python, tasksPath, command, String(module.id)];
        for (const value of values) args.push(String(value));
        Quickshell.execDetached(args);
    }

    function cursorDelta(start: var, current: var): var {
        return { x: current.x - start.x, y: current.y - start.y };
    }

    function dragOffset(startX: real, startY: real, delta: var, right: bool, bottom: bool,
                        screenWidth: real, screenHeight: real, width: real, height: real): var {
        return { x: clampOffset(startX + (right ? -delta.x : delta.x), screenWidth, width),
                 y: clampOffset(startY + (bottom ? -delta.y : delta.y), screenHeight, height) };
    }

    function resizeFromDelta(width: real, height: real, delta: var,
                             right: bool, bottom: bool, maxWidth: real, maxHeight: real): var {
        return { width: Math.min(maxWidth, Math.max(240, width + (right ? -delta.x : delta.x))),
                 height: Math.min(maxHeight, Math.max(180, height + (bottom ? -delta.y : delta.y))) };
    }

    Instantiator {
        model: T.LoomState.surfaceModules
        delegate: SurfaceWindow {}
    }

    component SurfaceWindow: PanelWindow {
        id: window
        required property var module
        readonly property var placement: module.placement ?? ({})
        readonly property var projected: root.geometry(placement.anchor ?? "top-left", placement.x ?? 0, placement.y ?? 0)
        readonly property real savedWidth: Math.max(240, Number(placement.width ?? 340))
        readonly property real savedHeight: Math.max(180, Number(placement.height ?? 220))
        readonly property real baseX: root.clampOffset(projected.centered
            ? (screen.width - implicitWidth) / 2 + projected.horizontal : projected.horizontal,
            screen.width, implicitWidth)
        readonly property real baseY: root.clampOffset(projected.centered
            ? (screen.height - implicitHeight) / 2 + projected.vertical : projected.vertical,
            screen.height, implicitHeight)
        property real liveX: -1
        property real liveY: -1
        property real liveWidth: -1
        property real liveHeight: -1
        property bool viewerRegistered: false
        property string activeGesture: ""
        property int gestureSerial: 0
        property int cursorQuerySerial: -1
        property var gestureStartCursor: null
        property real gestureStartX: 0
        property real gestureStartY: 0
        property real gestureStartWidth: 0
        property real gestureStartHeight: 0

        visible: module.visible !== false
        screen: {
            const wanted = String(placement.monitor ?? "");
            return Quickshell.screens.find(candidate => candidate.name === wanted) ?? Quickshell.screens[0];
        }
        anchors.top: projected.top
        anchors.bottom: projected.bottom
        anchors.left: projected.left
        anchors.right: projected.right
        margins.top: projected.top ? (liveY >= 0 ? liveY : baseY) : 0
        margins.bottom: projected.bottom ? (liveY >= 0 ? liveY : baseY) : 0
        margins.left: projected.left ? (liveX >= 0 ? liveX : baseX) : 0
        margins.right: projected.right ? (liveX >= 0 ? liveX : baseX) : 0
        implicitWidth: liveWidth > 0 ? liveWidth : savedWidth
        implicitHeight: liveHeight > 0 ? liveHeight : savedHeight
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: `loom-floating-${module.id}`

        function syncViewer(): void {
            viewerRegistered = T.LoomState.setTasksViewer(viewerRegistered, visible && module.kind === "tasks");
        }
        function commitPlacement(x: real, y: real): void {
            const updated = JSON.parse(JSON.stringify(module));
            updated.placement.x = Math.round(x); updated.placement.y = Math.round(y);
            root.persist(module, "place", [updated.placement.anchor, updated.placement.x, updated.placement.y]);
            T.LoomState.replaceSurfaceModule(updated, ["x", "y"]);
        }
        function commitSize(width: real, height: real, x: real, y: real): void {
            const updated = JSON.parse(JSON.stringify(module));
            updated.placement.width = Math.round(width); updated.placement.height = Math.round(height);
            const fields = ["width", "height"];
            if (projected.centered) {
                updated.placement.x = Math.round(x);
                updated.placement.y = Math.round(y);
                fields.push("x", "y");
                root.persist(module, "place", [updated.placement.anchor, updated.placement.x, updated.placement.y]);
            }
            root.persist(module, "resize", [updated.placement.width, updated.placement.height]);
            T.LoomState.replaceSurfaceModule(updated, fields);
        }
        function beginGesture(kind: string): void {
            gestureSerial += 1;
            activeGesture = kind;
            gestureStartCursor = null;
            gestureStartX = baseX; gestureStartY = baseY;
            gestureStartWidth = savedWidth; gestureStartHeight = savedHeight;
            requestCursor();
        }
        function endGesture(kind: string): void {
            if (activeGesture !== kind) return;
            activeGesture = "";
            gestureStartCursor = null;
        }
        function requestCursor(): void {
            if (activeGesture === "" || cursorPosition.running) return;
            cursorQuerySerial = gestureSerial;
            cursorPosition.running = true;
        }
        function applyCursorPosition(position: var, serial: int): void {
            if (activeGesture === "" || serial !== gestureSerial || !Number.isFinite(position.x) || !Number.isFinite(position.y))
                return;
            if (gestureStartCursor === null) {
                gestureStartCursor = position;
                return;
            }
            const delta = root.cursorDelta(gestureStartCursor, position);
            if (activeGesture === "move") {
                const offset = root.dragOffset(gestureStartX, gestureStartY, delta,
                    projected.right, projected.bottom, screen.width, screen.height,
                    implicitWidth, implicitHeight);
                liveX = offset.x; liveY = offset.y;
            } else if (activeGesture === "resize") {
                const size = root.resizeFromDelta(gestureStartWidth, gestureStartHeight, delta,
                    projected.right, projected.bottom,
                    Math.max(240, screen.width - gestureStartX),
                    Math.max(180, screen.height - gestureStartY));
                liveWidth = size.width; liveHeight = size.height;
                if (projected.centered) {
                    // The centered layer origin otherwise shifts by half the size delta.
                    // Hold its top-left fixed so the bottom-right grip follows the cursor.
                    liveX = gestureStartX; liveY = gestureStartY;
                }
            }
        }

        Timer {
            id: cursorPoll
            interval: 20
            repeat: true
            running: window.activeGesture !== ""
            onTriggered: window.requestCursor()
        }

        Process {
            id: cursorPosition
            command: ["hyprctl", "cursorpos", "-j"]
            stdout: StdioCollector {
                onStreamFinished: {
                    try {
                        const position = JSON.parse(text);
                        window.applyCursorPosition(position, window.cursorQuerySerial);
                    } catch (error) {
                        // A missing or malformed compositor response fails closed.
                    }
                }
            }
        }

        Component.onCompleted: syncViewer()
        Component.onDestruction: viewerRegistered = T.LoomState.setTasksViewer(viewerRegistered, false)
        onVisibleChanged: syncViewer()
        onModuleChanged: syncViewer()

        StyledRect {
            id: body
            anchors.fill: parent
            color: Colours.tPalette.m3surfaceContainer
            radius: Tokens.rounding.normal
            border.width: 1
            border.color: Qt.alpha(Colours.palette.m3outline, 0.2)
            clip: true

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Tokens.padding.normal
                spacing: Tokens.spacing.small
                Item {
                    id: titleBar
                    Layout.fillWidth: true
                    Layout.preferredHeight: titleText.implicitHeight
                    StyledText {
                        id: titleText
                        anchors.fill: parent
                        text: window.module.title || window.module.kind
                        color: Colours.palette.m3onSurface
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    Item {
                        id: moveArea
                        anchors.fill: parent
                        anchors.leftMargin: window.projected.bottom && window.projected.right ? grip.width : 0
                        anchors.rightMargin: window.projected.bottom && !window.projected.right ? grip.width : 0
                        DragHandler {
                            id: moveDrag
                            target: null
                            acceptedButtons: Qt.LeftButton
                            onActiveChanged: {
                                if (active) {
                                    window.beginGesture("move");
                                } else if (window.liveX >= 0) {
                                    window.commitPlacement(
                                        window.projected.centered ? window.liveX - Math.round((window.screen.width - window.implicitWidth) / 2) : window.liveX,
                                        window.projected.centered ? window.liveY - Math.round((window.screen.height - window.implicitHeight) / 2) : window.liveY);
                                    window.liveX = -1; window.liveY = -1;
                                }
                                if (!active) window.endGesture("move");
                            }
                        }
                    }
                }
                Loader {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    sourceComponent: window.module.kind === "tasks" ? tasksContent : metricContent
                }
            }

            Component {
                id: tasksContent
                ListView {
                    clip: true
                    spacing: Tokens.spacing.small
                    model: (T.LoomState.tasks.missions || []).concat(T.LoomState.tasks.ideas || [])
                    delegate: StyledRect {
                        id: taskRow
                        required property var modelData
                        width: ListView.view.width
                        height: 42
                        radius: Tokens.rounding.small
                        color: Colours.layer(Colours.palette.m3surfaceContainer, 1)
                        StyledText {
                            anchors.fill: parent
                            anchors.margins: Tokens.padding.small
                            text: String(taskRow.modelData.title || "")
                            color: Colours.palette.m3onSurface
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
            }

            Component {
                id: metricContent
                ColumnLayout {
                    id: metric
                    readonly property string kind: window.module.kind
                    readonly property real value: kind === "cpu" ? Cpu.percentage
                        : kind === "memory" ? Memory.percentage
                        : kind === "storage" ? (Storage.primaryDisk?.perc ?? 0)
                        : kind === "battery" ? UPower.displayDevice.percentage : 0
                    Item { Layout.fillHeight: true }
                    StyledText {
                        Layout.alignment: Qt.AlignHCenter
                        text: metric.kind === "weather" ? (Weather.icon || "cloud") : `${Math.round(metric.value * 100)}%`
                        color: Colours.palette.m3primary
                        font.pixelSize: 42
                    }
                    StyledText {
                        Layout.alignment: Qt.AlignHCenter
                        text: metric.kind === "weather" ? `${Weather.temp || "--"}  ${Weather.description || ""}`
                            : metric.kind === "battery" && !UPower.displayDevice.isLaptopBattery ? "No battery detected"
                            : metric.kind.toUpperCase()
                        color: Colours.palette.m3onSurfaceVariant
                    }
                    Item { Layout.fillHeight: true }
                    ServiceRef { service: Cpu }
                    ServiceRef { service: Memory }
                    ServiceRef { service: Storage }
                }
            }

            Item {
                id: grip
                anchors.left: window.projected.right ? parent.left : undefined
                anchors.right: window.projected.right ? undefined : parent.right
                anchors.top: window.projected.bottom ? parent.top : undefined
                anchors.bottom: window.projected.bottom ? undefined : parent.bottom
                width: 22; height: 22
                HoverHandler { cursorShape: window.projected.right === window.projected.bottom ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor }
                DragHandler {
                    id: resizeDrag
                    target: null
                    onActiveChanged: {
                        if (active) {
                            window.beginGesture("resize");
                        } else if (window.liveWidth > 0) {
                            window.commitSize(window.liveWidth, window.liveHeight,
                                window.projected.centered ? window.liveX - Math.round((window.screen.width - window.liveWidth) / 2) : 0,
                                window.projected.centered ? window.liveY - Math.round((window.screen.height - window.liveHeight) / 2) : 0);
                            window.liveWidth = -1; window.liveHeight = -1;
                            window.liveX = -1; window.liveY = -1;
                        }
                        if (!active) window.endGesture("resize");
                    }
                }
            }
        }
    }
}
