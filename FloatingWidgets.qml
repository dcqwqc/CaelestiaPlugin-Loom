pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
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

    function windowOrigin(marginX: real, marginY: real, width: real, height: real,
                          screenWidth: real, screenHeight: real, right: bool, bottom: bool): var {
        return { x: right ? screenWidth - marginX - width : marginX,
                 y: bottom ? screenHeight - marginY - height : marginY };
    }

    // A layer-shell margin update moves the surface under the pointer. Recover
    // physical screen motion from pointer motion in window coordinates plus
    // the surface-origin displacement already applied since the press.
    function pointerDelta(pressLocal: var, currentLocal: var, pressOrigin: var, currentOrigin: var): var {
        return { x: currentLocal.x - pressLocal.x + currentOrigin.x - pressOrigin.x,
                 y: currentLocal.y - pressLocal.y + currentOrigin.y - pressOrigin.y };
    }

    function dragOffset(startX: real, startY: real, delta: var, right: bool, bottom: bool,
                        screenWidth: real, screenHeight: real, width: real, height: real): var {
        return { x: clampOffset(startX + (right ? -delta.x : delta.x), screenWidth, width),
                 y: clampOffset(startY + (bottom ? -delta.y : delta.y), screenHeight, height) };
    }

    function resizeFromDelta(width: real, height: real, delta: var,
                             right: bool, bottom: bool, screenWidth: real, screenHeight: real): var {
        return { width: Math.min(screenWidth, Math.max(240, width + (right ? -delta.x : delta.x))),
                 height: Math.min(screenHeight, Math.max(180, height + (bottom ? -delta.y : delta.y))) };
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
            T.LoomState.replaceSurfaceModule(updated, true);
        }
        function commitSize(width: real, height: real): void {
            const updated = JSON.parse(JSON.stringify(module));
            updated.placement.width = Math.round(width); updated.placement.height = Math.round(height);
            root.persist(module, "resize", [updated.placement.width, updated.placement.height]);
            T.LoomState.replaceSurfaceModule(updated, true);
        }
        function currentOrigin(): var {
            return root.windowOrigin(liveX >= 0 ? liveX : baseX, liveY >= 0 ? liveY : baseY,
                                     implicitWidth, implicitHeight, screen.width, screen.height,
                                     projected.right, projected.bottom);
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
                    DragHandler {
                        id: moveDrag
                        target: null
                        acceptedButtons: Qt.LeftButton
                        property point pressLocal
                        property point pressOrigin
                        property real pressX
                        property real pressY
                        onActiveChanged: {
                            if (active) {
                                pressLocal = titleBar.mapToItem(body, centroid.position);
                                pressOrigin = window.currentOrigin();
                                pressX = window.baseX; pressY = window.baseY;
                            } else if (window.liveX >= 0) {
                                window.commitPlacement(
                                    window.projected.centered ? window.liveX - Math.round((window.screen.width - window.implicitWidth) / 2) : window.liveX,
                                    window.projected.centered ? window.liveY - Math.round((window.screen.height - window.implicitHeight) / 2) : window.liveY);
                                window.liveX = -1; window.liveY = -1;
                            }
                        }
                        onCentroidChanged: if (active) {
                            const local = titleBar.mapToItem(body, centroid.position);
                            const delta = root.pointerDelta(pressLocal, local, pressOrigin, window.currentOrigin());
                            const offset = root.dragOffset(pressX, pressY, delta,
                                window.projected.right, window.projected.bottom,
                                window.screen.width, window.screen.height,
                                window.implicitWidth, window.implicitHeight);
                            window.liveX = offset.x; window.liveY = offset.y;
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
                    property point pressLocal
                    property point pressOrigin
                    property real pressWidth
                    property real pressHeight
                    onActiveChanged: {
                        if (active) {
                            pressLocal = grip.mapToItem(body, centroid.position);
                            pressOrigin = window.currentOrigin();
                            pressWidth = window.savedWidth; pressHeight = window.savedHeight;
                        } else if (window.liveWidth > 0) {
                            window.commitSize(window.liveWidth, window.liveHeight);
                            window.liveWidth = -1; window.liveHeight = -1;
                        }
                    }
                    onCentroidChanged: if (active) {
                        const local = grip.mapToItem(body, centroid.position);
                        const delta = root.pointerDelta(pressLocal, local, pressOrigin, window.currentOrigin());
                        const size = root.resizeFromDelta(pressWidth, pressHeight, delta,
                            window.projected.right, window.projected.bottom,
                            window.screen.width, window.screen.height);
                        window.liveWidth = size.width; window.liveHeight = size.height;
                    }
                }
            }
        }
    }
}
