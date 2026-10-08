pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Wayland
import Caelestia.Config
import Caelestia.Services
import qs.components
import qs.services
import qs.utils
import dcqwqc.loom.services as T

// Persistent native host for Loom modules.  It is loaded by URL from Main.qml
// because plugin entry points have versioned import URLs and cannot reliably
// resolve an adjacent custom QML type by name.
Scope {
    id: root

    readonly property var tile: T.LoomState.tasksTile
    readonly property var placement: tile ? tile.placement : null
    readonly property string python: "/usr/bin/python3"
    readonly property string tasksPath: Paths.toLocalFile(Qt.resolvedUrl("loom_tasks.py"))

    // Pure placement projection, also exercised by the node test. Offsets are
    // measured inward from the selected compositor edge.
    function geometry(anchor: string, x: real, y: real): var {
        const a = anchor === "free" ? "top-left" : anchor;
        return {
            top: a.startsWith("top"), bottom: a.startsWith("bottom"),
            left: a.endsWith("left"), right: a.endsWith("right"),
            centered: a === "center", horizontal: Math.max(0, Math.round(x)),
            vertical: Math.max(0, Math.round(y))
        };
    }

    function persist(kind: string, values: var): void {
        if (!tile) return;
        const args = [python, tasksPath, kind, String(tile.id)];
        for (const value of values) args.push(String(value));
        Quickshell.execDetached(args);
    }

    PanelWindow {
        id: surface

        readonly property var projected: root.geometry(root.placement?.anchor ?? "top-left",
                                                        root.placement?.x ?? 0,
                                                        root.placement?.y ?? 0)
        property real liveX: -1
        property real liveY: -1
        property real liveWidth: -1
        property real liveHeight: -1
        property bool viewerRegistered: false

        visible: root.tile !== null && root.tile.visible !== false
            && ["performance", "floating"].includes(String(root.placement?.surface ?? ""))
        screen: {
            const wanted = String(root.placement?.monitor ?? "");
            return Quickshell.screens.find(s => s.name === wanted) ?? Quickshell.screens[0];
        }
        anchors.top: projected.top
        anchors.bottom: projected.bottom
        anchors.left: projected.left
        anchors.right: projected.right
        margins.top: projected.top ? (liveY >= 0 ? liveY : projected.vertical) : 0
        margins.bottom: projected.bottom ? (liveY >= 0 ? liveY : projected.vertical) : 0
        margins.left: projected.left ? (liveX >= 0 ? liveX : projected.horizontal) : 0
        margins.right: projected.right ? (liveX >= 0 ? liveX : projected.horizontal) : 0
        implicitWidth: liveWidth > 0 ? liveWidth : Math.max(240, Number(root.placement?.width ?? 360))
        implicitHeight: liveHeight > 0 ? liveHeight : Math.max(180, Number(root.placement?.height ?? 300))
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "loom-floating-widget"

        function syncViewer(): void {
            viewerRegistered = T.LoomState.setTasksViewer(viewerRegistered, visible);
        }
        Component.onCompleted: syncViewer()
        Component.onDestruction: {
            viewerRegistered = T.LoomState.setTasksViewer(viewerRegistered, false);
        }
        onVisibleChanged: syncViewer()

        StyledRect {
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

                RowLayout {
                    Layout.fillWidth: true
                    StyledText {
                        Layout.fillWidth: true
                        text: root.tile?.title ?? "Loom tasks"
                        color: Colours.palette.m3onSurface
                        font.weight: Font.DemiBold
                    }
                    // These are the shell's live Performance services, not a
                    // separately sampled or cached approximation.
                    StyledText {
                        text: `CPU ${Math.round(Cpu.percentage * 100)}%  ·  RAM ${Math.round(Memory.percentage * 100)}%`
                        color: Colours.palette.m3onSurfaceVariant
                        font.pixelSize: 10
                        ServiceRef { service: Cpu }
                        ServiceRef { service: Memory }
                    }
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: Tokens.spacing.small
                    model: (T.LoomState.tasks.missions || []).concat(T.LoomState.tasks.ideas || [])
                    delegate: StyledRect {
                        id: row
                        required property var modelData
                        width: ListView.view.width
                        height: 42
                        radius: Tokens.rounding.small
                        color: Colours.layer(Colours.palette.m3surfaceContainer, 1)
                        StyledText {
                            anchors.fill: parent
                            anchors.margins: Tokens.padding.small
                            text: String(row.modelData.title || "")
                            color: Colours.palette.m3onSurface
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
            }

            // Drag the body to alter the saved inward edge offsets.
            DragHandler {
                target: null
                acceptedButtons: Qt.LeftButton
                onTranslationChanged: if (active) {
                    surface.liveX = Math.max(0, surface.projected.horizontal
                        + (surface.projected.right ? -translation.x : translation.x));
                    surface.liveY = Math.max(0, surface.projected.vertical
                        + (surface.projected.bottom ? -translation.y : translation.y));
                }
                onActiveChanged: if (!active && surface.liveX >= 0) {
                    root.persist("place", [root.placement.anchor, Math.round(surface.liveX), Math.round(surface.liveY)]);
                    surface.liveX = -1; surface.liveY = -1;
                }
            }

            Item {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                width: 22
                height: 22
                HoverHandler { cursorShape: Qt.SizeFDiagCursor }
                DragHandler {
                    target: null
                    onTranslationChanged: if (active) {
                        surface.liveWidth = Math.max(240, Number(root.placement.width) + translation.x);
                        surface.liveHeight = Math.max(180, Number(root.placement.height) + translation.y);
                    }
                    onActiveChanged: if (!active && surface.liveWidth > 0) {
                        root.persist("resize", [Math.round(surface.liveWidth), Math.round(surface.liveHeight)]);
                        surface.liveWidth = -1; surface.liveHeight = -1;
                    }
                }
            }
        }
    }
}
