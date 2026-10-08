pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
import Caelestia.Config
import qs.components
import qs.services
import qs.utils
import dcqwqc.loom.services as T

// Native Loom Tasks card. Shows LOOM ledger states only (no progress
// percentages); size comes from the saved tasks module and is persisted on
// resize. Rows are synced in place by key so unrelated delegates survive a
// refresh. Hosts may set `resizable: false` when they own the geometry.
StyledRect {
    id: card

    property bool resizable: true
    property real dragWidth: -1
    property bool viewerRegistered: false
    property bool completed: false
    property real dragHeight: -1
    property real now: Date.now() / 1000

    readonly property string python: "/usr/bin/python3"
    readonly property string tasksPath: Paths.toLocalFile(Qt.resolvedUrl("loom_tasks.py"))
    readonly property var tile: T.LoomState.tasksTile
    readonly property var snapshot: T.LoomState.tasks
    readonly property real savedWidth: tile ? Number(tile.placement.width) : 360
    readonly property real savedHeight: tile ? Number(tile.placement.height) : 300
    readonly property int pad: tok(["padding", "normal"], 12)
    readonly property int gap: tok(["spacing", "small"], 6)

    // Caelestia design tokens when the shell provides them, else the values
    // this plugin already used in Panel.qml.
    function tok(path: var, fallback: real): real {
        try {
            let v = Tokens;
            for (const key of path) v = v[key];
            return typeof v === "number" ? v : fallback;
        } catch (error) {
            return fallback;
        }
    }

    function stateColour(phase: string): color {
        switch (phase) {
        case "blocked": return Colours.palette.m3tertiary;
        case "failed": return Colours.palette.m3error;
        case "review": return Colours.palette.m3secondary;
        case "running": return Colours.palette.m3primary;
        case "verified":
        case "accepted": return Colours.palette.m3primary;
        default: return Colours.palette.m3outline;
        }
    }

    function age(seconds: var): string {
        if (typeof seconds !== "number") return "never refreshed";
        const d = Math.max(0, now - seconds);
        if (d < 90) return "updated just now";
        if (d < 5400) return `updated ${Math.round(d / 60)} min ago`;
        return `updated ${Math.round(d / 3600)} h ago`;
    }

    function syncRows(): void {
        const rows = (snapshot.missions || []).concat(snapshot.ideas || []);
        const keys = rows.map(r => `${r.kind}:${r.id}`);
        for (let i = rows_.count - 1; i >= 0; i--)
            if (keys.indexOf(rows_.get(i).key) < 0) rows_.remove(i);
        for (let i = 0; i < rows.length; i++) {
            const r = rows[i];
            const row = { key: keys[i], kind: String(r.kind), title: String(r.title || ""),
                          phase: String(r.state || "unknown"), label: String(r.label || "") };
            let j = -1;
            for (let k = i; k < rows_.count; k++)
                if (rows_.get(k).key === row.key) { j = k; break; }
            if (j < 0) {
                rows_.insert(i, row);
            } else {
                if (j !== i) rows_.move(j, i, 1);
                rows_.set(i, row);
            }
        }
    }

    implicitWidth: dragWidth > 0 ? dragWidth : Math.max(240, savedWidth)
    implicitHeight: dragHeight > 0 ? dragHeight : Math.max(160, savedHeight)
    radius: tok(["rounding", "normal"], 14)
    color: Colours.tPalette.m3surfaceContainer
    clip: true

    onSnapshotChanged: syncRows()
    function syncViewer(wanted: bool): void {
        viewerRegistered = T.LoomState.setTasksViewer(viewerRegistered, wanted);
    }

    Component.onCompleted: {
        syncRows();
        completed = true;
        syncViewer(visible);
    }
    Component.onDestruction: syncViewer(false)
    onVisibleChanged: if (completed) syncViewer(visible)

    ListModel { id: rows_ }

    Timer {
        interval: 30000
        repeat: true
        running: card.visible
        onTriggered: card.now = Date.now() / 1000
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: card.pad
        spacing: card.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: card.gap

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                StyledText {
                    Layout.fillWidth: true
                    text: card.tile ? String(card.tile.title) : "Loom tasks"
                    color: Colours.palette.m3onSurface
                    font.pixelSize: card.tok(["font", "size", "normal"], 13)
                    font.weight: Font.Medium
                    elide: Text.ElideRight
                }
                StyledText {
                    Layout.fillWidth: true
                    text: "Philipedia LOOM · " + (card.snapshot.stale ? "stale · " : "") + card.age(card.snapshot.fetched_at)
                    color: card.snapshot.stale ? Colours.palette.m3tertiary : Colours.palette.m3onSurfaceVariant
                    font.pixelSize: card.tok(["font", "size", "smaller"], 10)
                    elide: Text.ElideRight
                }
            }

            StyledRect {
                id: refreshButton
                Layout.preferredWidth: 28
                Layout.preferredHeight: 28
                radius: 14
                color: refreshHover.hovered
                    ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                    : "transparent"
                HoverHandler { id: refreshHover }
                TapHandler { onTapped: T.LoomState.requestTasksRefresh() }
                Canvas {
                    id: refreshIcon
                    anchors.centerIn: parent
                    width: 14
                    height: 14
                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.reset();
                        ctx.strokeStyle = Colours.palette.m3onSurfaceVariant.toString();
                        ctx.lineWidth = 1.6;
                        ctx.lineCap = "round";
                        ctx.beginPath();
                        ctx.arc(7, 7, 5.2, 0.3, Math.PI * 1.75);
                        ctx.moveTo(12.4, 2.4); ctx.lineTo(12.2, 5.6); ctx.lineTo(9.0, 5.4);
                        ctx.stroke();
                    }
                    RotationAnimation on rotation {
                        running: T.LoomState.tasksRefreshing
                        loops: Animation.Infinite
                        from: 0
                        to: 360
                        duration: 900
                        onRunningChanged: if (!running) refreshIcon.rotation = 0
                    }
                }
            }
        }

        // State counts straight from the projection; no aggregate "progress".
        Flow {
            Layout.fillWidth: true
            spacing: card.gap
            visible: Object.keys(card.snapshot.counts || {}).length > 0
            Repeater {
                model: Object.keys(card.snapshot.counts || {})
                delegate: StyledRect {
                    id: chip
                    required property string modelData
                    implicitWidth: chipText.implicitWidth + 16
                    implicitHeight: 22
                    radius: 11
                    color: Qt.alpha(card.stateColour(modelData), 0.16)
                    StyledText {
                        id: chipText
                        anchors.centerIn: parent
                        text: `${card.snapshot.counts[chip.modelData]} ${chip.modelData}`
                        color: card.stateColour(chip.modelData)
                        font.pixelSize: card.tok(["font", "size", "smaller"], 10)
                    }
                }
            }
        }

        StyledText {
            Layout.fillWidth: true
            visible: card.snapshot.errors.length > 0
            text: card.snapshot.errors.join(" · ")
            color: Colours.palette.m3error
            font.pixelSize: card.tok(["font", "size", "smaller"], 10)
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 4
            boundsBehavior: Flickable.StopAtBounds
            model: rows_
            section.property: "kind"
            section.delegate: StyledText {
                required property string section
                width: list.width
                topPadding: 4
                bottomPadding: 2
                text: section === "mission" ? "Missions" : "Idea inbox"
                color: Colours.palette.m3outline
                font.pixelSize: card.tok(["font", "size", "smaller"], 10)
            }

            delegate: StyledRect {
                id: row
                required property string title
                required property string phase
                required property string label
                width: list.width
                height: 40
                radius: 10
                color: Colours.layer(Colours.palette.m3surfaceContainer, 1)
                border.width: 1
                border.color: Qt.alpha(Colours.palette.m3outline, 0.13)

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 8
                    Rectangle {
                        Layout.preferredWidth: 8
                        Layout.preferredHeight: 8
                        radius: 4
                        color: card.stateColour(row.phase)
                        Behavior on color { CAnim {} }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        StyledText {
                            Layout.fillWidth: true
                            text: row.title
                            color: Colours.palette.m3onSurface
                            font.pixelSize: card.tok(["font", "size", "small"], 12)
                            elide: Text.ElideRight
                        }
                        StyledText {
                            Layout.fillWidth: true
                            text: row.label
                            color: Colours.palette.m3onSurfaceVariant
                            font.pixelSize: card.tok(["font", "size", "smaller"], 10)
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            StyledText {
                anchors.centerIn: parent
                visible: rows_.count === 0
                text: card.snapshot.fetched_at === null ? "Waiting for first ledger read" : "No missions or ideas"
                color: Colours.palette.m3onSurfaceVariant
                font.pixelSize: card.tok(["font", "size", "small"], 12)
            }
        }
    }

    // Resize grip: live while dragging, persisted to the saved module on release.
    Item {
        id: grip
        visible: card.resizable && card.tile !== null
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: 18
        height: 18
        property real startW: 0
        property real startH: 0

        Canvas {
            anchors.fill: parent
            onPaint: {
                const ctx = getContext("2d");
                ctx.reset();
                ctx.strokeStyle = Qt.alpha(Colours.palette.m3onSurfaceVariant, 0.6).toString();
                ctx.lineWidth = 1.4;
                ctx.lineCap = "round";
                ctx.beginPath();
                ctx.moveTo(14, 6); ctx.lineTo(6, 14);
                ctx.moveTo(14, 10); ctx.lineTo(10, 14);
                ctx.stroke();
            }
        }
        HoverHandler { cursorShape: Qt.SizeFDiagCursor }
        DragHandler {
            target: null
            onActiveChanged: {
                if (active) {
                    grip.startW = card.width;
                    grip.startH = card.height;
                    return;
                }
                if (card.dragWidth < 0) return;
                const w = Math.round(Math.min(4096, card.dragWidth));
                const h = Math.round(Math.min(4096, card.dragHeight));
                Quickshell.execDetached([card.python, card.tasksPath, "resize", String(card.tile.id), String(w), String(h)]);
                const tile = JSON.parse(JSON.stringify(card.tile));
                tile.placement.width = w;
                tile.placement.height = h;
                T.LoomState.tasksTile = tile;
                card.dragWidth = -1;
                card.dragHeight = -1;
            }
            onTranslationChanged: {
                if (!active) return;
                card.dragWidth = Math.max(240, grip.startW + translation.x);
                card.dragHeight = Math.max(160, grip.startH + translation.y);
            }
        }
    }
}
