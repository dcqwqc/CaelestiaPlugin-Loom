pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Hyprland
import Quickshell.Wayland
import Caelestia.Config
import qs.components
import qs.components.controls
import qs.services
import qs.utils
import dcqwqc.loom
import dcqwqc.loom.services as T

Item {
    id: root

    // Native Tasks component is inline: Quickshell uses versioned plugin URLs,
    // which make adjacent QML files fail Qt type filename verification.
    component TasksView: StyledRect {
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
                    TapHandler { onTapped: T.LoomState.requestTasksRefresh(true) }
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


    property real phase: 0
    property bool panelHostHovered: false
    property bool notificationsPinned: false
    property bool hovered: hover.hovered || panelHostHovered
    // The small counter opens this native drawer on mouse hover or touch tap.
    // Mouse exit dismisses it; a touch latch persists until a tap elsewhere.
    property bool chipExpanded: false
    // Touch latch is independent of mouse hover. It remains open until a
    // second touch on the chip or an outside touch clears the focus grab.
    property bool touchPinned: false
    readonly property bool panelTouchPinned: chipVisible && touchPinned
    readonly property bool panelTouchTargetEnabled: chipVisible
    property bool composerVisible: T.LoomState.inputArmed && (hovered || composerField.activeFocus || composerHover.hovered)

    readonly property string python: "/usr/bin/python3"
    readonly property string ctlPath: Paths.toLocalFile(Qt.resolvedUrl("loomctl.py"))
    readonly property var notificationItems: Array.isArray(T.LoomState.notifications) ? T.LoomState.notifications : []
    readonly property int notificationCount: notificationItems.length
    readonly property bool notificationsExpanded: notificationCount > 0 && (hovered || notificationsPinned)
    readonly property int notificationHeight: notificationsExpanded ? Math.min(270, 26 + Math.min(3, notificationCount) * 95) : 0
    readonly property int workingCount: Array.isArray(T.LoomState.working) ? T.LoomState.working.length : 0
    readonly property bool hasWorking: workingCount > 0
    // Idle counter chip: running tasks only, shown while full Loom/Voice is not.
    readonly property int runningCount: Array.isArray(T.LoomState.working) ? T.LoomState.working.filter(t => t && t.status === "working").length : 0
    readonly property bool fullLoom: T.LoomState.summoned || T.LoomState.voiceActive || T.LoomState.whiteboardVisible || T.LoomState.inputArmed
    readonly property bool chipVisible: !fullLoom && runningCount > 0
    readonly property bool idleWorkingExpanded: chipVisible && chipExpanded && hasWorking
    // Task rows belong to the counter hover only, not the summoned voice window.
    readonly property bool workingListVisible: idleWorkingExpanded
    readonly property bool voiceVisible: T.LoomState.voiceActive
    readonly property bool startupLoading: T.LoomState.summoned && !T.LoomState.voiceActive && T.LoomState.state === "wake"
    readonly property bool faceSlotVisible: T.LoomState.summoned || T.LoomState.voiceActive
    // Preserve existing hover-only task drawer. Notification chip is independent.
    readonly property bool tasksCardVisible: T.LoomState.tasksPanelVisible && idleWorkingExpanded
    readonly property bool panelVisible: T.LoomState.enabled && (faceSlotVisible || workingListVisible || chipVisible || T.LoomState.whiteboardVisible || tasksCardVisible || notificationCount > 0)
    readonly property bool panelInputEnabled: true
    readonly property bool panelOverFullscreen: true
    readonly property bool panelLiftShadow: panelVisible
    readonly property real panelDeformAmount: 0.025
    readonly property int panelMotionDuration: 180

    // The counter stays at the top edge; expanded content begins below the
    // bar's hit strip, within Caelestia's native shared drawer surface.
    readonly property int counterStripHeight: chipVisible ? 48 : 0
    readonly property int workingHeight: workingListVisible ? Math.min(idleWorkingExpanded ? 380 : 220, 12 + workingCount * 54) : 0
    readonly property int boardHeight: T.LoomState.whiteboardVisible ? Math.max(48, Math.min(440, boardColumn.implicitHeight + 24)) : 0
    implicitWidth: Math.max(tasksCardVisible ? tasksCard.implicitWidth + 20 : 0,
        notificationsExpanded ? 360
        : notificationCount > 0 ? 105
        : idleWorkingExpanded ? 360
        : chipVisible ? Math.max(60, counterChip.implicitWidth + 16)
        : (T.LoomState.whiteboardVisible || T.LoomState.inputArmed || workingListVisible) ? 360 : 104)
    implicitHeight: (faceSlotVisible ? 56 : 0)
        + (composerVisible ? 48 : 0)
        + (T.LoomState.whiteboardVisible ? boardHeight + 6 : 0)
        + (workingListVisible ? workingHeight + 6 : 0)
        + (chipVisible ? counterStripHeight : 0)
        + (tasksCardVisible ? tasksCard.implicitHeight + 6 : 0)
        + (notificationCount > 0 ? 30 : 0)
        + (notificationsExpanded ? notificationHeight + 6 : 0)
        + ((T.LoomState.summoned || workingListVisible || T.LoomState.whiteboardVisible || tasksCardVisible) ? 6 : 0)

    Behavior on implicitWidth { NumberAnimation { duration: 170; easing.type: Easing.OutCubic } }
    Behavior on implicitHeight { NumberAnimation { duration: 170; easing.type: Easing.OutCubic } }

    function command(name: string, value: string): void {
        const args = [root.python, root.ctlPath, name];
        if (value !== undefined && value !== null && value.length > 0)
            args.push(value);
        Quickshell.execDetached(args);
    }

    function choose(itemId: string, option: string): void {
        Quickshell.execDetached([root.python, root.ctlPath, "choose", itemId, option]);
    }

    function notificationResponse(itemId: string, option: string): void {
        Quickshell.execDetached([root.python, root.ctlPath, "notification-answer", itemId, option]);
    }
    function notificationDismiss(itemId: string): void {
        Quickshell.execDetached([root.python, root.ctlPath, "notification-dismiss", itemId]);
    }

    // ---- Declarative Loom UI (tabby/ui_tree.py) -------------------------
    // Views are validated by the backend; this renderer only maps typed
    // props onto Caelestia theme tokens and reports interactions back.
    readonly property bool uiHasInput: uiHasType(T.LoomState.uiViews, "input")

    function uiHasType(views: var, kind: string): bool {
        const walk = n => !!n && (n.type === kind || (n.children || []).some(walk));
        return (views || []).some(v => walk(v.root));
    }

    function uiGap(gap: string): int {
        return ({ none: 0, small: 4, normal: 8, large: 12 })[gap] ?? 8;
    }

    function uiEventArgs(viewId: string, nodeId: string, eventName: string, value: var): var {
        const args = [root.python, root.ctlPath, "ui-event", viewId, nodeId, eventName];
        if (value !== undefined)
            args.push(JSON.stringify(value));
        return args;
    }

    function uiEvent(viewId: string, nodeId: string, eventName: string, value: var): void {
        Quickshell.execDetached(root.uiEventArgs(viewId, nodeId, eventName, value));
    }

    // Slider position (0..1) -> value, snapped like the backend does.
    function uiSliderValue(props: var, fraction: real): real {
        const lo = Number(props.min ?? 0), hi = Number(props.max ?? 1), step = Number(props.step ?? 0);
        let v = lo + Math.max(0, Math.min(1, fraction)) * (hi - lo);
        if (step > 0)
            v = Math.min(hi, lo + Math.round((v - lo) / step) * step);
        return v;
    }

    function uiToneColour(tone: string): color {
        return !tone || tone === "neutral" ? Colours.palette.m3onSurface : root.toneColour(tone);
    }

    Component {
        id: uiNodeComponent

        // Loaded by a Loader that carries `node` and `viewId`.
        ColumnLayout {
            id: uiNode
            readonly property var node: parent && parent.node ? parent.node : ({})
            readonly property string viewId: parent && parent.viewId ? parent.viewId : ""
            readonly property var props: node.props || ({})
            readonly property string kind: String(node.type || "")
            readonly property var kids: Array.isArray(node.children) ? node.children : []
            readonly property bool enabledNode: props.disabled !== true
            visible: props.hidden !== true
            spacing: 4

            function report(eventName: string, value: var): void {
                if (uiNode.enabledNode)
                    root.uiEvent(uiNode.viewId, String(uiNode.node.id || ""), eventName, value);
            }

            // card frame + title
            StyledRect {
                visible: uiNode.kind === "card"
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? cardColumn.implicitHeight + 16 : 0
                radius: 10
                color: Colours.layer(Colours.palette.m3surfaceContainer, 2)
                border.width: 1
                border.color: Qt.alpha(uiNode.props.tone && uiNode.props.tone !== "neutral"
                    ? root.uiToneColour(uiNode.props.tone) : Colours.palette.m3outline, 0.25)

                ColumnLayout {
                    id: cardColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 8
                    spacing: root.uiGap("normal")
                    StyledText {
                        visible: text.length > 0
                        Layout.fillWidth: true
                        text: String(uiNode.props.title || "")
                        textFormat: Text.PlainText
                        color: root.uiToneColour(uiNode.props.tone)
                        font.pixelSize: 13
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                    }
                    Repeater {
                        model: uiNode.kind === "card" ? uiNode.kids : []
                        delegate: Loader {
                            required property var modelData
                            readonly property var node: modelData
                            readonly property string viewId: uiNode.viewId
                            Layout.fillWidth: true
                            sourceComponent: uiNodeComponent
                        }
                    }
                }
            }

            // column / row containers
            ColumnLayout {
                visible: uiNode.kind === "column"
                Layout.fillWidth: true
                spacing: root.uiGap(String(uiNode.props.gap || "normal"))
                Repeater {
                    model: uiNode.kind === "column" ? uiNode.kids : []
                    delegate: Loader {
                        required property var modelData
                        readonly property var node: modelData
                        readonly property string viewId: uiNode.viewId
                        Layout.fillWidth: true
                        sourceComponent: uiNodeComponent
                    }
                }
            }
            RowLayout {
                visible: uiNode.kind === "row"
                Layout.fillWidth: true
                spacing: root.uiGap(String(uiNode.props.gap || "normal"))
                Item { visible: uiNode.props.align === "end" || uiNode.props.align === "center"; Layout.fillWidth: true }
                Repeater {
                    model: uiNode.kind === "row" ? uiNode.kids : []
                    delegate: Loader {
                        required property var modelData
                        readonly property var node: modelData
                        readonly property string viewId: uiNode.viewId
                        // text grows, controls keep their natural width
                        Layout.fillWidth: ["text", "progress", "slider", "input", "column", "card", "list"].indexOf(String(modelData.type)) >= 0
                        sourceComponent: uiNodeComponent
                    }
                }
                Item { visible: uiNode.props.align === "center"; Layout.fillWidth: true }
            }

            // text
            StyledText {
                visible: uiNode.kind === "text" && text.length > 0
                Layout.fillWidth: true
                text: String(uiNode.props.text || "")
                textFormat: Text.PlainText
                color: uiNode.props.tone && uiNode.props.tone !== "neutral" ? root.uiToneColour(uiNode.props.tone)
                    : uiNode.props.style === "caption" ? Colours.palette.m3onSurfaceVariant : Colours.palette.m3onSurface
                font.pixelSize: uiNode.props.style === "title" ? 15 : uiNode.props.style === "caption" ? 11 : 12
                font.weight: uiNode.props.style === "title" || uiNode.props.style === "label" ? Font.Medium : Font.Normal
                wrapMode: Text.Wrap
                maximumLineCount: 10
                elide: Text.ElideRight
            }

            // badge
            Rectangle {
                visible: uiNode.kind === "badge"
                Layout.preferredWidth: badgeText.implicitWidth + 14
                Layout.preferredHeight: 20
                radius: 10
                color: Qt.alpha(root.uiToneColour(uiNode.props.tone || "primary"), 0.16)
                StyledText {
                    id: badgeText
                    anchors.centerIn: parent
                    text: String(uiNode.props.text || "")
                    textFormat: Text.PlainText
                    color: root.uiToneColour(uiNode.props.tone || "primary")
                    font.pixelSize: 10
                    font.weight: Font.Medium
                }
            }

            // progress
            StyledText {
                visible: uiNode.kind === "progress" && String(uiNode.props.label || "").length > 0
                Layout.fillWidth: true
                text: String(uiNode.props.label || "") + "  " + Math.round(Number(uiNode.props.value || 0) * 100) + "%"
                textFormat: Text.PlainText
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Rectangle {
                visible: uiNode.kind === "progress"
                Layout.fillWidth: true
                Layout.preferredHeight: 6
                radius: 3
                color: Colours.palette.m3surfaceContainerHighest
                Rectangle {
                    width: parent.width * Math.max(0, Math.min(1, Number(uiNode.props.value || 0)))
                    height: parent.height
                    radius: parent.radius
                    color: root.uiToneColour(uiNode.props.tone || "primary")
                    Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                }
            }

            // divider
            RowLayout {
                visible: uiNode.kind === "divider"
                Layout.fillWidth: true
                spacing: 8
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Qt.alpha(Colours.palette.m3outline, 0.3) }
                StyledText {
                    visible: text.length > 0
                    text: String(uiNode.props.label || "")
                    textFormat: Text.PlainText
                    color: Colours.palette.m3outline
                    font.pixelSize: 10
                }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Qt.alpha(Colours.palette.m3outline, 0.3) }
            }

            // list
            Repeater {
                model: uiNode.kind === "list" && Array.isArray(uiNode.props.entries) ? uiNode.props.entries : []
                delegate: StyledText {
                    required property int index
                    required property var modelData
                    Layout.fillWidth: true
                    text: (uiNode.props.ordered ? (index + 1) + ".  " : "•  ") + String(modelData ?? "")
                    textFormat: Text.PlainText
                    color: Colours.palette.m3onSurfaceVariant
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                }
            }

            // button: variant/tone map onto M3 roles
            Rectangle {
                id: uiButton
                readonly property string variant: String(uiNode.props.variant || "tonal")
                readonly property color accent: root.uiToneColour(uiNode.props.tone || "primary")
                visible: uiNode.kind === "button"
                Layout.preferredWidth: buttonText.implicitWidth + 24
                Layout.preferredHeight: 30
                radius: 15
                opacity: uiNode.enabledNode ? 1 : 0.45
                color: variant === "filled" ? accent
                    : variant === "tonal" ? Qt.alpha(accent, buttonHover.hovered ? 0.26 : 0.16)
                    : buttonHover.hovered ? Qt.alpha(accent, 0.1) : "transparent"
                border.width: variant === "outlined" ? 1 : 0
                border.color: Qt.alpha(accent, 0.6)
                StyledText {
                    id: buttonText
                    anchors.centerIn: parent
                    text: String(uiNode.props.label || "")
                    textFormat: Text.PlainText
                    color: uiButton.variant === "filled" ? Colours.palette.m3onPrimary : uiButton.accent
                    font.pixelSize: 12
                    font.weight: Font.Medium
                }
                HoverHandler { id: buttonHover; cursorShape: uiNode.enabledNode ? Qt.PointingHandCursor : Qt.ArrowCursor }
                TapHandler { enabled: uiNode.kind === "button" && uiNode.enabledNode; onTapped: uiNode.report("press", undefined) }
            }

            // toggle
            RowLayout {
                visible: uiNode.kind === "toggle"
                Layout.fillWidth: true
                spacing: 8
                opacity: uiNode.enabledNode ? 1 : 0.45
                StyledText {
                    Layout.fillWidth: true
                    text: String(uiNode.props.label || "")
                    textFormat: Text.PlainText
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                Rectangle {
                    readonly property bool on: uiNode.props.value === true
                    Layout.preferredWidth: 36
                    Layout.preferredHeight: 20
                    radius: 10
                    color: on ? Colours.palette.m3primary : Colours.palette.m3surfaceContainerHighest
                    border.width: on ? 0 : 1
                    border.color: Colours.palette.m3outline
                    Rectangle {
                        width: 14
                        height: 14
                        radius: 7
                        anchors.verticalCenter: parent.verticalCenter
                        x: parent.on ? parent.width - width - 3 : 3
                        color: parent.on ? Colours.palette.m3onPrimary : Colours.palette.m3outline
                        Behavior on x { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
                    }
                    HoverHandler { cursorShape: uiNode.enabledNode ? Qt.PointingHandCursor : Qt.ArrowCursor }
                    TapHandler { enabled: uiNode.kind === "toggle" && uiNode.enabledNode; onTapped: uiNode.report("change", !parent.on) }
                }
            }

            // slider: previews locally while dragging, reports on release
            StyledText {
                visible: uiNode.kind === "slider"
                Layout.fillWidth: true
                text: String(uiNode.props.label || "") + "  " + Number(sliderTrack.dragging ? sliderTrack.preview : uiNode.props.value || 0).toFixed(Number(uiNode.props.step || 0) >= 1 ? 0 : 2)
                textFormat: Text.PlainText
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Item {
                id: sliderTrack
                property bool dragging: false
                property real preview: 0
                readonly property real lo: Number(uiNode.props.min ?? 0)
                readonly property real hi: Number(uiNode.props.max ?? 1)
                readonly property real shown: dragging ? preview : Number(uiNode.props.value ?? lo)
                readonly property real fraction: hi > lo ? Math.max(0, Math.min(1, (shown - lo) / (hi - lo))) : 0
                visible: uiNode.kind === "slider"
                Layout.fillWidth: true
                Layout.preferredHeight: 18
                opacity: uiNode.enabledNode ? 1 : 0.45
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width
                    height: 4
                    radius: 2
                    color: Colours.palette.m3surfaceContainerHighest
                    Rectangle { width: parent.width * sliderTrack.fraction; height: parent.height; radius: 2; color: Colours.palette.m3primary }
                }
                Rectangle {
                    width: 14
                    height: 14
                    radius: 7
                    anchors.verticalCenter: parent.verticalCenter
                    x: (parent.width - width) * sliderTrack.fraction
                    color: Colours.palette.m3primary
                }
                MouseArea {
                    anchors.fill: parent
                    enabled: uiNode.kind === "slider" && uiNode.enabledNode
                    cursorShape: Qt.PointingHandCursor
                    function track(mx: real): void { sliderTrack.preview = root.uiSliderValue(uiNode.props, mx / Math.max(1, width)); }
                    onPressed: mouse => { sliderTrack.dragging = true; track(mouse.x); }
                    onPositionChanged: mouse => { if (pressed) track(mouse.x); }
                    onReleased: { uiNode.report("change", sliderTrack.preview); sliderTrack.dragging = false; }
                    onCanceled: sliderTrack.dragging = false
                }
            }

            // input: change on editing finished, submit on Enter
            StyledText {
                visible: uiNode.kind === "input" && text.length > 0
                Layout.fillWidth: true
                text: String(uiNode.props.label || "")
                textFormat: Text.PlainText
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            StyledTextField {
                id: uiInput
                property string synced: String(uiNode.props.value || "")
                visible: uiNode.kind === "input"
                enabled: uiNode.kind === "input" && uiNode.enabledNode
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 32 : 0
                placeholderText: String(uiNode.props.placeholder || "")
                maximumLength: Number(uiNode.props.max_length || 200)
                selectByMouse: true
                activeFocusOnPress: true
                text: synced
                onSyncedChanged: if (!activeFocus) text = synced
                onAccepted: uiNode.report("submit", text)
                onEditingFinished: if (text !== synced) uiNode.report("change", text)
            }

            // select: single-choice chips
            StyledText {
                visible: uiNode.kind === "select" && text.length > 0
                Layout.fillWidth: true
                text: String(uiNode.props.label || "")
                textFormat: Text.PlainText
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Flow {
                visible: uiNode.kind === "select"
                Layout.fillWidth: true
                spacing: 6
                opacity: uiNode.enabledNode ? 1 : 0.45
                Repeater {
                    model: uiNode.kind === "select" && Array.isArray(uiNode.props.options) ? uiNode.props.options : []
                    delegate: Rectangle {
                        id: selectChip
                        required property var modelData
                        readonly property bool chosen: String(uiNode.props.value || "") === String(modelData)
                        width: selectText.implicitWidth + 22
                        height: 28
                        radius: 14
                        color: chosen ? Colours.palette.m3primary
                            : selectHover.hovered ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                            : Colours.palette.m3surfaceContainerHigh
                        border.width: 1
                        border.color: Qt.alpha(Colours.palette.m3outline, 0.25)
                        StyledText {
                            id: selectText
                            anchors.centerIn: parent
                            text: String(selectChip.modelData)
                            textFormat: Text.PlainText
                            color: selectChip.chosen ? Colours.palette.m3onPrimary : Colours.palette.m3onSurface
                            font.pixelSize: 12
                        }
                        HoverHandler { id: selectHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            enabled: uiNode.enabledNode && !selectChip.chosen
                            onTapped: uiNode.report("change", String(selectChip.modelData))
                        }
                    }
                }
            }
        }
    }

    function toneColour(tone: string): color {
        switch (tone) {
        case "primary": return Colours.palette.m3primary;
        case "secondary": return Colours.palette.m3secondary;
        case "tertiary":
        case "warning": return Colours.palette.m3tertiary;
        case "success": return Colours.palette.m3primary;
        case "error": return Colours.palette.m3error;
        default: return Colours.palette.m3onSurfaceVariant;
        }
    }

    function submit(): void {
        const value = composerField.text;
        composerField.text = "";
        root.command("send-text", value);
    }

    Timer {
        interval: 42
        repeat: true
        running: root.panelVisible && !root.chipVisible
        onTriggered: {
            root.phase += T.LoomState.state === "thinking" || T.LoomState.state === "tool" ? 0.22 : 0.12;
            face.requestPaint();
            stateHalo.requestPaint();
            thinkingCanvas.requestPaint();
            startupLoader.requestPaint();
        }
    }

    // Called by the 60x48 native input strip, which surrounds the small
    // visual counter so a finger does not have to hit its 22px badge.
    function toggleTouchCounter(): void {
        if (!chipVisible) return;
        touchPinned = !touchPinned;
        chipExpanded = touchPinned;
        chipCollapseTimer.stop();
    }

    function dismissTouchPanel(): void {
        if (!touchPinned) return;
        touchPinned = false;
        chipExpanded = false;
        chipCollapseTimer.stop();
    }

    onFullLoomChanged: if (fullLoom) dismissTouchPanel()

    // Keep the hover target alive while crossing the small gap between the
    // counter and its expanded list. Once the pointer exits the entire panel,
    // collapse after a brief grace period to prevent edge flicker.
    function checkChipDismissal(): void {
        if (!chipExpanded) return;
        if (touchPinned || hover.hovered || panelHostHovered) {
            chipCollapseTimer.stop();
            return;
        }
        chipCollapseTimer.restart();
    }

    onPanelHostHoveredChanged: checkChipDismissal()
    onChipVisibleChanged: {
        if (!chipVisible) {
            chipExpanded = false;
            touchPinned = false;
            chipCollapseTimer.stop();
        }
    }

    Timer {
        id: chipCollapseTimer
        interval: 140
        repeat: false
        onTriggered: {
            if (!root.touchPinned && !hover.hovered && !root.panelHostHovered)
                root.chipExpanded = false;
        }
    }

    // Native shell pointer hover. In the compact state the whole panel is
    // the 60x48 hit target, so there is no dependence on a tiny child pill.
    HoverHandler {
        id: hover
        onHoveredChanged: {
            if (hovered && root.chipVisible && !root.touchPinned) {
                chipCollapseTimer.stop();
                root.chipExpanded = true;
            } else {
                root.checkChipDismissal();
            }
        }
    }

    StyledRect {
        id: counterChip
        visible: root.chipVisible
        anchors.top: parent.top
        anchors.topMargin: 3
        anchors.horizontalCenter: parent.horizontalCenter
        implicitWidth: chipRow.implicitWidth + 18
        implicitHeight: 22
        radius: implicitHeight / 2
        color: Colours.tPalette.m3surfaceContainer

        RowLayout {
            id: chipRow
            anchors.centerIn: parent
            spacing: 6

            Rectangle {
                Layout.preferredWidth: 7
                Layout.preferredHeight: 7
                radius: 3.5
                color: Colours.palette.m3secondary
                SequentialAnimation on opacity {
                    running: root.chipVisible
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.3; duration: 600 }
                    NumberAnimation { to: 1; duration: 600 }
                }
            }
            StyledText {
                text: String(root.runningCount)
                color: Colours.palette.m3onSurface
                font.pixelSize: 12
                font.weight: Font.DemiBold
            }
        }
    }

    // The real input surface is 60x48 at rest (not an overlay window).
    // Keep the chip's *visual* pill at 22px, while the entire touch strip
    // handles both pointer hover and touchscreen tap-to-latch.
    Item {
        id: counterTouchArea
        visible: root.chipVisible
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        width: 60
        height: 48
        z: 3

        TapHandler {
            acceptedDevices: PointerDevice.TouchScreen
            onTapped: root.toggleTouchCounter()
        }
    }

    ColumnLayout {
        anchors.top: parent.top
        // In idle mode the chip is a sibling overlay. Keep the expanded task
        // panel below it instead of painting the list over the number.
        anchors.topMargin: root.chipVisible ? root.counterStripHeight : 0
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 6

        StyledRect {
            id: notificationChip
            visible: root.notificationCount > 0
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: root.notificationsExpanded ? 340 : 94
            Layout.preferredHeight: 26
            radius: 13
            color: Colours.tPalette.m3surfaceContainer
            RowLayout {
                anchors.centerIn: parent
                spacing: 5
                Rectangle {
                    Layout.preferredWidth: 7
                    Layout.preferredHeight: 7
                    radius: 3.5
                    color: Colours.palette.m3tertiary
                }
                StyledText {
                    text: String(root.notificationCount) + " needs attention"
                    font.pixelSize: 11
                    color: Colours.palette.m3onSurface
                    elide: Text.ElideRight
                }
            }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: root.notificationsPinned = !root.notificationsPinned }
        }

        StyledRect {
            id: notificationPanel
            visible: root.notificationsExpanded
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 340
            Layout.preferredHeight: root.notificationHeight
            radius: 14
            color: Colours.tPalette.m3surfaceContainer
            clip: true
            Flickable {
                anchors.fill: parent
                anchors.margins: 12
                contentWidth: width
                contentHeight: notificationColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                ColumnLayout {
                    id: notificationColumn
                    width: parent.width
                    spacing: 10
                    Repeater {
                        model: root.notificationItems.slice(0, 3)
                        delegate: ColumnLayout {
                            id: notificationEntry
                            required property var modelData
                            readonly property var entry: modelData || ({})
                            Layout.fillWidth: true
                            spacing: 4
                            RowLayout {
                                Layout.fillWidth: true
                                StyledText {
                                    Layout.fillWidth: true
                                    text: String(notificationEntry.entry.title || "")
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                Rectangle {
                                    width: 22; height: 22; radius: 11
                                    color: Colours.palette.m3surfaceContainerHighest
                                    StyledText { anchors.centerIn: parent; text: "×"; font.pixelSize: 12 }
                                    TapHandler {
                                        onTapped: root.notificationDismiss(String(notificationEntry.entry.id || ""))
                                    }
                                }
                            }
                            StyledText {
                                Layout.fillWidth: true
                                text: String(notificationEntry.entry.body || "")
                                font.pixelSize: 11
                                wrapMode: Text.Wrap
                                maximumLineCount: 3
                                elide: Text.ElideRight
                            }
                            Flow {
                                Layout.fillWidth: true
                                visible: notificationEntry.entry.status === "pending"
                                         && Array.isArray(notificationEntry.entry.options)
                                         && notificationEntry.entry.options.length > 0
                                spacing: 5
                                Repeater {
                                    model: Array.isArray(notificationEntry.entry.options)
                                           ? notificationEntry.entry.options : []
                                    delegate: Rectangle {
                                        id: notificationOption
                                        required property string modelData
                                        width: optionLabel.implicitWidth + 20
                                        height: 28
                                        radius: 14
                                        color: optionHover.hovered
                                            ? Colours.palette.m3surfaceContainerHighest
                                            : Colours.palette.m3surfaceContainerHigh
                                        StyledText {
                                            id: optionLabel
                                            anchors.centerIn: parent
                                            text: notificationOption.modelData
                                            font.pixelSize: 11
                                        }
                                        HoverHandler { id: optionHover; cursorShape: Qt.PointingHandCursor }
                                        TapHandler {
                                            onTapped: root.notificationResponse(String(notificationEntry.entry.id || ""), notificationOption.modelData)
                                        }
                                    }
                                }
                            }
                        }
                    }
                    StyledText {
                        visible: root.notificationCount > 3
                        text: "+" + (root.notificationCount - 3) + " more in Loom"
                        font.pixelSize: 11
                    }
                }
            }
        }

        Item {
            id: faceArea
            visible: root.faceSlotVisible
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 96
            Layout.preferredHeight: visible ? 56 : 0
            width: 96
            height: visible ? 56 : 0

            readonly property string mood: T.LoomState.state
            readonly property real level: Math.max(0, Math.min(1, T.LoomState.audioLevel))

            // Loading still owns the short Voice-start transition. Once that
            // transition is over, the green face stays present for both Voice
            // and ordinary text/message sessions.
            Canvas {
                id: startupLoader
                anchors.centerIn: parent
                width: 30
                height: 30
                visible: root.startupLoading
                onPaint: {
                    const ctx = getContext("2d");
                    ctx.reset();
                    ctx.strokeStyle = Colours.palette.m3primary.toString();
                    ctx.lineWidth = 3.2;
                    ctx.lineCap = "round";
                    const start = root.phase * 2.3;
                    ctx.beginPath();
                    ctx.arc(width / 2, height / 2, 10.5, start, start + Math.PI * 1.38);
                    ctx.stroke();
                }
            }

            // State halo belongs to the live Voice face only.
            Canvas {
                id: stateHalo
                visible: T.LoomState.voiceActive
                anchors.centerIn: parent
                width: 92
                height: 54
                opacity: faceArea.mood === "idle" ? 0 : 1

                onPaint: {
                    const ctx = getContext("2d");
                    ctx.reset();
                    const state = faceArea.mood;
                    let pulse = 0;
                    let alpha = 0;
                    if (state === "listening") {
                        pulse = (Math.sin(root.phase * 2.1) + 1) / 2;
                        alpha = 0.22 + pulse * 0.28;
                    } else if (state === "speaking") {
                        pulse = faceArea.level;
                        alpha = 0.30 + pulse * 0.55;
                    } else if (state === "wake") {
                        pulse = (Math.sin(root.phase * 4.0) + 1) / 2;
                        alpha = 0.28 + pulse * 0.35;
                    } else if (state === "thinking" || state === "tool") {
                        pulse = 0.35;
                        alpha = 0.14;
                    } else {
                        return;
                    }
                    ctx.strokeStyle = Qt.rgba(
                        Colours.palette.m3primary.r,
                        Colours.palette.m3primary.g,
                        Colours.palette.m3primary.b,
                        alpha
                    ).toString();
                    ctx.lineWidth = 2.0 + pulse * 1.8;
                    const padX = 6 - pulse * 2.5;
                    const padY = 4 - pulse * 1.5;
                    ctx.beginPath();
                    ctx.ellipse(padX, padY, width - padX * 2, height - padY * 2);
                    ctx.stroke();
                }
            }

            Item {
                id: animatedFace
                visible: root.faceSlotVisible && !root.startupLoading
                anchors.centerIn: parent
                width: 82
                height: 48

                transform: [
                    Translate {
                        y: faceArea.mood === "idle"
                            ? Math.sin(root.phase * 1.35) * 1.7
                            : faceArea.mood === "thinking" || faceArea.mood === "tool"
                                ? Math.sin(root.phase * 2.2) * 2.1
                                : faceArea.mood === "speaking"
                                    ? -faceArea.level * 2.8
                                    : Math.sin(root.phase * 1.7) * 0.8
                    },
                    Scale {
                        origin.x: animatedFace.width / 2
                        origin.y: animatedFace.height / 2
                        xScale: faceArea.mood === "speaking"
                            ? 1.0 + faceArea.level * 0.11
                            : faceArea.mood === "listening"
                                ? 1.0 + ((Math.sin(root.phase * 2.0) + 1) / 2) * 0.035
                                : faceArea.mood === "wake"
                                    ? 1.03 + ((Math.sin(root.phase * 3.5) + 1) / 2) * 0.055
                                    : 1.0
                        yScale: xScale
                    },
                    Rotation {
                        origin.x: animatedFace.width / 2
                        origin.y: animatedFace.height / 2
                        angle: faceArea.mood === "thinking" || faceArea.mood === "tool"
                            ? Math.sin(root.phase * 1.5) * 4.0
                            : 0
                    }
                ]

                Canvas {
                    id: face
                    anchors.centerIn: parent
                    width: 72
                    height: 44
                    readonly property color ink: Colours.palette.m3primary

                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.reset();
                        ctx.lineCap = "round";
                        ctx.lineJoin = "round";
                        ctx.strokeStyle = ink.toString();
                        ctx.fillStyle = ink.toString();
                        ctx.lineWidth = 3.4;
                        const state = T.LoomState.state;
                        const blink = Math.floor(root.phase * 1.55) % 53 === 0;
                        let look = 0;
                        if (state === "thinking" || state === "tool")
                            look = Math.sin(root.phase * 1.85) * 3.6;
                        else if (state === "listening")
                            look = Math.sin(root.phase * 0.7) * 1.1;

                        if (blink) {
                            ctx.beginPath();
                            ctx.moveTo(18, 20); ctx.lineTo(27, 20);
                            ctx.moveTo(45, 20); ctx.lineTo(54, 20);
                            ctx.stroke();
                        } else {
                            const eyeH = state === "wake" || state === "listening" ? 11 : 9;
                            ctx.fillRect(19 + look, 14, 7, eyeH);
                            ctx.fillRect(46 + look, 14, 7, eyeH);
                        }

                        ctx.beginPath();
                        if (state === "speaking") {
                            const level = Math.max(0, Math.min(1, T.LoomState.audioLevel));
                            const mouthH = 3.0 + Math.pow(level, 0.68) * 13.0;
                            ctx.ellipse(25, 31 - mouthH / 2, 22, mouthH);
                        } else if (state === "thinking" || state === "tool") {
                            const wobble = Math.sin(root.phase * 2.7) * 1.7;
                            ctx.moveTo(28, 33 + wobble);
                            ctx.quadraticCurveTo(36, 29 - wobble, 44, 33 + wobble);
                        } else if (state === "success") {
                            ctx.arc(36, 28, 9, 0.2, Math.PI - 0.2);
                        } else if (state === "error") {
                            ctx.arc(36, 39, 8, Math.PI + 0.25, Math.PI * 2 - 0.25);
                        } else if (state === "wake" || state === "listening") {
                            const radius = 2.6 + ((Math.sin(root.phase * 2.2) + 1) / 2) * 1.4;
                            ctx.arc(36, 33, radius, 0, Math.PI * 2);
                        } else {
                            ctx.moveTo(30, 32);
                            ctx.quadraticCurveTo(36, 36, 42, 32);
                        }
                        ctx.stroke();
                    }

                    Connections {
                        target: T.LoomState
                        function onStateChanged(): void {
                            root.phase = 0;
                            face.requestPaint();
                            stateHalo.requestPaint();
                            thinkingCanvas.requestPaint();
                            startupLoader.requestPaint();
                        }
                        function onVoiceActiveChanged(): void {
                            root.phase = 0;
                            face.requestPaint();
                            stateHalo.requestPaint();
                            startupLoader.requestPaint();
                        }
                        function onAudioLevelChanged(): void {
                            face.requestPaint();
                            stateHalo.requestPaint();
                        }
                    }
                }

                Canvas {
                    id: thinkingCanvas
                    anchors.fill: parent
                    visible: T.LoomState.state === "thinking" || T.LoomState.state === "tool"
                    opacity: 0.95
                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.reset();
                        ctx.fillStyle = Colours.palette.m3primary.toString();
                        for (let i = 0; i < 4; i++) {
                            const a = root.phase * 1.75 + i * Math.PI * 2 / 4;
                            const x = width / 2 + Math.cos(a) * 38;
                            const y = height / 2 + Math.sin(a) * 21;
                            const r = 2.0 + ((Math.sin(a + root.phase * 1.2) + 1) / 2) * 1.6;
                            ctx.beginPath();
                            ctx.arc(x, y, r, 0, Math.PI * 2);
                            ctx.fill();
                        }
                    }
                }
            }

            StyledRect {
                id: workPinButton
                visible: root.hovered && T.LoomState.summoned
                width: 24
                height: 24
                anchors.left: parent.left
                anchors.top: parent.top
                radius: 12
                color: workPinHover.hovered
                    ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                    : Colours.tPalette.m3surfaceContainerHighest
                opacity: workPinTap.pressed ? 0.72 : 0.98
                Behavior on color { CAnim {} }
                HoverHandler { id: workPinHover }
                Canvas {
                    anchors.centerIn: parent
                    width: 12
                    height: 12
                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.reset();
                        ctx.strokeStyle = Colours.palette.m3onSurface.toString();
                        ctx.lineWidth = 1.6;
                        ctx.lineCap = "round";
                        ctx.lineJoin = "round";
                        ctx.beginPath();
                        ctx.rect(1.5, 4.0, 9.0, 6.4);
                        ctx.moveTo(4.0, 4.0); ctx.lineTo(4.0, 2.1);
                        ctx.lineTo(8.0, 2.1); ctx.lineTo(8.0, 4.0);
                        ctx.stroke();
                        ctx.beginPath();
                        ctx.moveTo(1.8, 6.5); ctx.quadraticCurveTo(6, 8.1, 10.2, 6.5);
                        ctx.stroke();
                    }
                }
                TapHandler { id: workPinTap; onTapped: root.command("work-pin-current", "") }
            }

            StyledRect {
                id: closeButton
                visible: root.hovered
                width: 24
                height: 24
                anchors.right: parent.right
                anchors.top: parent.top
                radius: 12
                scale: root.hovered ? 1.0 : 0.86
                color: closeHover.hovered
                    ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                    : Colours.tPalette.m3surfaceContainerHighest
                opacity: closeTap.pressed ? 0.72 : 0.98

                Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                Behavior on color { CAnim {} }

                HoverHandler { id: closeHover }
                Canvas {
                    anchors.centerIn: parent
                    width: 10
                    height: 10
                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.reset();
                        ctx.strokeStyle = Colours.palette.m3onSurface.toString();
                        ctx.lineWidth = 1.8;
                        ctx.lineCap = "round";
                        ctx.beginPath();
                        ctx.moveTo(1.5, 1.5); ctx.lineTo(width - 1.5, height - 1.5);
                        ctx.moveTo(width - 1.5, 1.5); ctx.lineTo(1.5, height - 1.5);
                        ctx.stroke();
                    }
                }
                TapHandler {
                    id: closeTap
                    onTapped: root.command("close", "")
                }
            }
        }

        FocusScope {
            id: composer
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 332
            Layout.preferredHeight: 42
            visible: root.composerVisible

            HoverHandler { id: composerHover }
            TapHandler {
                acceptedButtons: Qt.LeftButton
                onTapped: composerField.forceActiveFocus()
            }

            StyledRect {
                anchors.fill: parent
                radius: 21
                color: composerField.activeFocus
                    ? Colours.layer(Colours.palette.m3surfaceContainer, 3)
                    : Colours.layer(Colours.palette.m3surfaceContainer, 2)
                border.width: 1
                border.color: composerField.activeFocus
                    ? Qt.alpha(Colours.palette.m3primary, 0.72)
                    : Qt.alpha(Colours.palette.m3outline, 0.22)

                Behavior on color { CAnim {} }
                Behavior on border.color { CAnim {} }
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 6
                spacing: 5

                StyledRect {
                    visible: T.LoomState.attachmentPending
                    Layout.preferredWidth: 8
                    Layout.preferredHeight: 8
                    radius: 4
                    color: Colours.palette.m3primary
                }

                StyledRect {
                    id: newChatButton
                    Layout.preferredWidth: 28
                    Layout.preferredHeight: 28
                    radius: 14
                    color: newChatHover.hovered || newChatTap.pressed
                        ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                        : "transparent"
                    Behavior on color { CAnim {} }
                    HoverHandler { id: newChatHover }
                    Canvas {
                        anchors.centerIn: parent
                        width: 12
                        height: 12
                        onPaint: {
                            const ctx = getContext("2d");
                            ctx.reset();
                            ctx.strokeStyle = Colours.palette.m3onSurfaceVariant.toString();
                            ctx.lineWidth = 1.7;
                            ctx.lineCap = "round";
                            ctx.beginPath();
                            ctx.moveTo(width / 2, 1.5); ctx.lineTo(width / 2, height - 1.5);
                            ctx.moveTo(1.5, height / 2); ctx.lineTo(width - 1.5, height / 2);
                            ctx.stroke();
                        }
                    }
                    TapHandler { id: newChatTap; onTapped: root.command("new-session", "") }
                }

                StyledTextField {
                    id: composerField
                    implicitWidth: 180
                    Layout.fillWidth: true
                    Layout.preferredHeight: 34
                    placeholderText: T.LoomState.attachmentPending ? "Add a message…" : "Message Loom…"
                    horizontalAlignment: TextInput.AlignLeft
                    selectByMouse: true
                    activeFocusOnPress: true

                    Keys.onReturnPressed: event => {
                        if (!(event.modifiers & Qt.ShiftModifier)) {
                            event.accepted = true;
                            root.submit();
                        }
                    }
                    Keys.onEnterPressed: event => {
                        event.accepted = true;
                        root.submit();
                    }
                    Keys.onPressed: event => {
                        if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_V) {
                            root.command("paste-clipboard", "");
                            event.accepted = false;
                        } else if (event.key === Qt.Key_Escape) {
                            event.accepted = true;
                            root.command("hide-input", "");
                        }
                    }
                }

                StyledRect {
                    id: attachButton
                    Layout.preferredWidth: 30
                    Layout.preferredHeight: 30
                    radius: 15
                    color: attachHover.hovered || attachTap.pressed
                        ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                        : "transparent"
                    Behavior on color { CAnim {} }
                    HoverHandler { id: attachHover }
                    Canvas {
                        anchors.centerIn: parent
                        width: 14
                        height: 14
                        onPaint: {
                            const ctx = getContext("2d");
                            ctx.reset();
                            ctx.strokeStyle = Colours.palette.m3onSurfaceVariant.toString();
                            ctx.lineWidth = 1.7;
                            ctx.lineCap = "round";
                            ctx.beginPath();
                            // Compact paperclip shape, drawn directly so there is
                            // no Material Symbols ligature fallback.
                            ctx.moveTo(9.8, 4.0);
                            ctx.lineTo(6.0, 9.2);
                            ctx.quadraticCurveTo(4.3, 11.4, 2.6, 9.8);
                            ctx.quadraticCurveTo(1.0, 8.3, 2.5, 6.3);
                            ctx.lineTo(7.2, 1.8);
                            ctx.quadraticCurveTo(9.0, 0.2, 10.6, 1.7);
                            ctx.quadraticCurveTo(12.1, 3.1, 10.7, 4.8);
                            ctx.lineTo(6.0, 9.5);
                            ctx.stroke();
                        }
                    }
                    TapHandler { id: attachTap; onTapped: root.command("paste-clipboard", "") }
                }

                StyledRect {
                    id: sendButton
                    Layout.preferredWidth: 30
                    Layout.preferredHeight: 30
                    radius: 15
                    color: sendHover.hovered
                        ? Colours.palette.m3primaryContainer
                        : Colours.palette.m3primary
                    opacity: sendTap.pressed ? 0.76 : 1
                    Behavior on color { CAnim {} }
                    HoverHandler { id: sendHover }
                    Canvas {
                        anchors.centerIn: parent
                        width: 13
                        height: 13
                        onPaint: {
                            const ctx = getContext("2d");
                            ctx.reset();
                            ctx.strokeStyle = (sendHover.hovered
                                ? Colours.palette.m3onPrimaryContainer
                                : Colours.palette.m3onPrimary).toString();
                            ctx.lineWidth = 1.9;
                            ctx.lineCap = "round";
                            ctx.lineJoin = "round";
                            ctx.beginPath();
                            ctx.moveTo(width / 2, height - 1.5);
                            ctx.lineTo(width / 2, 2.0);
                            ctx.moveTo(2.7, 5.5);
                            ctx.lineTo(width / 2, 2.0);
                            ctx.lineTo(width - 2.7, 5.5);
                            ctx.stroke();
                        }
                        Connections {
                            target: sendHover
                            function onHoveredChanged(): void { parent.requestPaint(); }
                        }
                    }
                    TapHandler { id: sendTap; onTapped: root.submit() }
                }
            }
        }

        StyledRect {
            id: board
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 340
            Layout.preferredHeight: root.boardHeight
            visible: T.LoomState.whiteboardVisible
            radius: 14
            color: Colours.tPalette.m3surfaceContainer
            clip: true

            readonly property var entries: Array.isArray(T.LoomState.items) ? T.LoomState.items : []
            readonly property var shapes: entries.filter(e => e && e.type === "shape")
            onShapesChanged: shapeCanvas.requestPaint()
            // Keep the newest item (usually the one an agent just added) in view.
            onEntriesChanged: Qt.callLater(() => boardFlick.contentY = Math.max(0, boardFlick.contentHeight - boardFlick.height))

            Flickable {
                id: boardFlick
                anchors.fill: parent
                anchors.margins: 12
                contentWidth: width
                contentHeight: boardColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                clip: true

                ColumnLayout {
                    id: boardColumn
                    width: boardFlick.width
                    spacing: 8

                    // All shape items share one drawing area; x/y/w/h are
                    // fractions of it (values > 1 are treated as pixels).
                    Canvas {
                        id: shapeCanvas
                        visible: board.shapes.length > 0
                        Layout.fillWidth: true
                        Layout.preferredHeight: visible ? 120 : 0
                        onWidthChanged: requestPaint()
                        onPaint: {
                            const ctx = getContext("2d");
                            ctx.reset();
                            const sx = v => Number(v || 0) > 1 ? Number(v) : Number(v || 0) * width;
                            const sy = v => Number(v || 0) > 1 ? Number(v) : Number(v || 0) * height;
                            for (const s of board.shapes) {
                                const c = root.toneColour(s.tone || "primary");
                                ctx.strokeStyle = c.toString();
                                ctx.fillStyle = Qt.alpha(c, 0.22).toString();
                                ctx.lineWidth = 2;
                                ctx.lineCap = "round";
                                const x = sx(s.x), y = sy(s.y), w = sx(s.w), h = sy(s.h);
                                ctx.beginPath();
                                if (s.kind === "rect") {
                                    ctx.rect(x, y, w, h);
                                } else if (s.kind === "circle") {
                                    ctx.ellipse(x, y, w, h);
                                } else {
                                    ctx.moveTo(x, y);
                                    ctx.lineTo(x + w, y + h);
                                }
                                if (s.filled && (s.kind === "rect" || s.kind === "circle")) ctx.fill();
                                ctx.stroke();
                                if (s.kind === "arrow") {
                                    const a = Math.atan2(h, w), l = 9;
                                    ctx.beginPath();
                                    ctx.moveTo(x + w, y + h);
                                    ctx.lineTo(x + w - l * Math.cos(a - 0.45), y + h - l * Math.sin(a - 0.45));
                                    ctx.moveTo(x + w, y + h);
                                    ctx.lineTo(x + w - l * Math.cos(a + 0.45), y + h - l * Math.sin(a + 0.45));
                                    ctx.stroke();
                                }
                                if (s.label) {
                                    ctx.fillStyle = Colours.palette.m3onSurface.toString();
                                    ctx.font = "11px sans-serif";
                                    ctx.fillText(String(s.label), x + 4, s.kind === "line" || s.kind === "arrow" ? y - 4 : y + 14);
                                }
                            }
                        }
                    }

                    Repeater {
                        model: board.entries
                        delegate: ColumnLayout {
                            id: entryRoot
                            required property var modelData
                            readonly property var entry: modelData || ({})
                            readonly property string kind: String(entry.type || "")
                            readonly property var options: kind === "choice" && entry.options ? entry.options : []
                            readonly property var listEntries: kind === "list" && entry.entries ? entry.entries : []
                            readonly property bool known: ["text", "progress", "status", "choice", "shape", "card", "list", "divider"].indexOf(kind) >= 0
                            visible: kind !== "shape"
                            Layout.fillWidth: true
                            spacing: 4

                            // text, card and any future/unknown widget type
                            StyledText {
                                visible: (entryRoot.kind === "text" || entryRoot.kind === "card" || !entryRoot.known) && text.length > 0
                                Layout.fillWidth: true
                                text: String(entryRoot.entry.title || "")
                                color: entryRoot.entry.tone ? root.toneColour(entryRoot.entry.tone) : Colours.palette.m3onSurface
                                font.pixelSize: 13
                                font.weight: Font.Medium
                                elide: Text.ElideRight
                            }
                            StyledText {
                                visible: (entryRoot.kind === "text" || entryRoot.kind === "card" || !entryRoot.known) && text.length > 0
                                Layout.fillWidth: true
                                text: String(entryRoot.kind === "card" ? (entryRoot.entry.body || "") : (entryRoot.entry.text || ""))
                                color: entryRoot.kind === "text" ? Colours.palette.m3onSurface : Colours.palette.m3onSurfaceVariant
                                font.pixelSize: entryRoot.kind === "text" ? 13 : 12
                                wrapMode: Text.Wrap
                                maximumLineCount: 8
                                elide: Text.ElideRight
                            }
                            StyledText {
                                visible: entryRoot.kind === "card" && text.length > 0
                                text: String(entryRoot.entry.badge || "")
                                color: root.toneColour(entryRoot.entry.tone || "primary")
                                font.pixelSize: 10
                                font.weight: Font.Medium
                            }

                            // progress (and card progress)
                            StyledText {
                                visible: entryRoot.kind === "progress"
                                Layout.fillWidth: true
                                text: String(entryRoot.entry.label || "Working…") + "  " + Math.round(Number(entryRoot.entry.value || 0) * 100) + "%"
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                            Rectangle {
                                readonly property real value: entryRoot.kind === "progress" ? Number(entryRoot.entry.value || 0)
                                    : Number(entryRoot.entry.progress === undefined ? -1 : entryRoot.entry.progress)
                                visible: (entryRoot.kind === "progress" || entryRoot.kind === "card") && value >= 0
                                Layout.fillWidth: true
                                Layout.preferredHeight: 6
                                radius: 3
                                color: Colours.palette.m3surfaceContainerHighest
                                Rectangle {
                                    width: parent.width * Math.max(0, Math.min(1, parent.value))
                                    height: parent.height
                                    radius: parent.radius
                                    color: root.toneColour(entryRoot.entry.tone || "primary")
                                    Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                                }
                            }

                            // status line
                            RowLayout {
                                visible: entryRoot.kind === "status"
                                Layout.fillWidth: true
                                spacing: 8
                                Rectangle {
                                    readonly property string st: String(entryRoot.entry.state || "info")
                                    Layout.preferredWidth: 8
                                    Layout.preferredHeight: 8
                                    radius: 4
                                    color: st === "error" ? Colours.palette.m3error
                                        : st === "success" ? Colours.palette.m3primary
                                        : st === "warning" ? Colours.palette.m3tertiary
                                        : st === "working" ? Colours.palette.m3secondary
                                        : Colours.palette.m3outline
                                    SequentialAnimation on opacity {
                                        running: parent.st === "working"
                                        loops: Animation.Infinite
                                        NumberAnimation { to: 0.3; duration: 600 }
                                        NumberAnimation { to: 1; duration: 600 }
                                    }
                                }
                                StyledText {
                                    Layout.fillWidth: true
                                    text: String(entryRoot.entry.label || "")
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                }
                            }

                            // choice: clickable buttons, answer recorded by the backend
                            StyledText {
                                visible: entryRoot.kind === "choice" && text.length > 0
                                Layout.fillWidth: true
                                text: String(entryRoot.entry.label || "")
                                font.pixelSize: 13
                                wrapMode: Text.Wrap
                            }
                            Flow {
                                visible: entryRoot.kind === "choice"
                                Layout.fillWidth: true
                                spacing: 6
                                Repeater {
                                    model: entryRoot.options.length
                                    delegate: Rectangle {
                                        id: optionChip
                                        required property int index
                                        readonly property string modelData: String(entryRoot.options[index] ?? "")
                                        readonly property bool chosen: String(entryRoot.entry.selected || "") === String(modelData)
                                        readonly property bool answered: String(entryRoot.entry.selected || "").length > 0
                                        width: optionText.implicitWidth + 22
                                        height: 28
                                        radius: 14
                                        opacity: answered && !chosen ? 0.45 : 1
                                        color: chosen ? Colours.palette.m3primary
                                            : optionHover.hovered ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                                            : Colours.palette.m3surfaceContainerHigh
                                        border.width: 1
                                        border.color: Qt.alpha(Colours.palette.m3outline, 0.25)
                                        StyledText {
                                            id: optionText
                                            anchors.centerIn: parent
                                            text: String(optionChip.modelData)
                                            color: optionChip.chosen ? Colours.palette.m3onPrimary : Colours.palette.m3onSurface
                                            font.pixelSize: 12
                                        }
                                        HoverHandler { id: optionHover; cursorShape: Qt.PointingHandCursor }
                                        TapHandler {
                                            enabled: !optionChip.answered
                                            onTapped: root.choose(String(entryRoot.entry.id || ""), String(optionChip.modelData))
                                        }
                                    }
                                }
                            }

                            // list
                            StyledText {
                                visible: entryRoot.kind === "list" && text.length > 0
                                Layout.fillWidth: true
                                text: String(entryRoot.entry.title || "")
                                font.pixelSize: 13
                                font.weight: Font.Medium
                                elide: Text.ElideRight
                            }
                            Repeater {
                                model: entryRoot.listEntries.length
                                delegate: StyledText {
                                    required property int index
                                    Layout.fillWidth: true
                                    text: (entryRoot.entry.ordered ? (index + 1) + ".  " : "•  ") + String(entryRoot.listEntries[index] ?? "")
                                    color: Colours.palette.m3onSurfaceVariant
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                    maximumLineCount: 3
                                    elide: Text.ElideRight
                                }
                            }

                            // divider
                            RowLayout {
                                visible: entryRoot.kind === "divider"
                                Layout.fillWidth: true
                                spacing: 8
                                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Qt.alpha(Colours.palette.m3outline, 0.3) }
                                StyledText {
                                    visible: text.length > 0
                                    text: String(entryRoot.entry.label || "")
                                    color: Colours.palette.m3outline
                                    font.pixelSize: 10
                                }
                                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Qt.alpha(Colours.palette.m3outline, 0.3) }
                            }
                        }
                    }

                    // Declarative views coexist with the classic items above.
                    Repeater {
                        model: Array.isArray(T.LoomState.uiViews) ? T.LoomState.uiViews : []
                        delegate: ColumnLayout {
                            id: uiView
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 6
                            StyledText {
                                visible: text.length > 0
                                Layout.fillWidth: true
                                text: String(uiView.modelData.title || "")
                                textFormat: Text.PlainText
                                color: Colours.palette.m3onSurfaceVariant
                                font.pixelSize: 11
                                font.weight: Font.Medium
                                elide: Text.ElideRight
                            }
                            Loader {
                                readonly property var node: uiView.modelData.root
                                readonly property string viewId: String(uiView.modelData.id || "")
                                Layout.fillWidth: true
                                sourceComponent: uiNodeComponent
                            }
                        }
                    }
                }
            }
        }

        StyledRect {
            id: workingPanel
            visible: root.workingListVisible
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 340
            Layout.preferredHeight: root.workingHeight
            radius: 14
            color: Colours.tPalette.m3surfaceContainer
            clip: true

            ListView {
                id: workingList
                anchors.fill: parent
                anchors.margins: 6
                clip: true
                spacing: 4
                boundsBehavior: Flickable.StopAtBounds
                model: T.LoomState.working

                delegate: StyledRect {
                    id: workCard
                    required property var modelData
                    required property int index
                    width: workingList.width
                    height: 50
                    radius: 11
                    color: workCardHover.hovered
                        ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                        : Colours.layer(Colours.palette.m3surfaceContainer, 1)
                    border.width: 1
                    border.color: Qt.alpha(Colours.palette.m3outline, 0.13)
                    readonly property var task: modelData || ({})
                    readonly property bool done: String(task.status || "") === "done"
                    Behavior on color { CAnim {} }

                    HoverHandler { id: workCardHover }
                    TapHandler {
                        acceptedButtons: Qt.LeftButton
                        enabled: String(workCard.task.url || "").length > 0
                        onTapped: root.command("work-open", String(workCard.task.id || ""))
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 10
                        anchors.rightMargin: 7
                        spacing: 8

                        Item {
                            Layout.preferredWidth: 18
                            Layout.preferredHeight: 18
                            Canvas {
                                id: workStatusCanvas
                                anchors.fill: parent
                                rotation: workCard.done ? 0 : 0
                                onPaint: {
                                    const ctx = getContext("2d");
                                    ctx.reset();
                                    ctx.strokeStyle = (workCard.done
                                        ? Colours.palette.m3primary
                                        : Colours.palette.m3onSurfaceVariant).toString();
                                    ctx.lineWidth = 1.8;
                                    ctx.lineCap = "round";
                                    if (workCard.done) {
                                        ctx.beginPath();
                                        ctx.arc(9, 9, 7, 0, Math.PI * 2);
                                        ctx.stroke();
                                        ctx.beginPath();
                                        ctx.moveTo(5.4, 9.2); ctx.lineTo(8.0, 11.7); ctx.lineTo(12.8, 6.4);
                                        ctx.stroke();
                                    } else {
                                        ctx.beginPath();
                                        ctx.arc(9, 9, 6.5, 0.25, Math.PI * 1.55);
                                        ctx.stroke();
                                    }
                                }
                                RotationAnimation on rotation {
                                    running: String(workCard.task.status || "working") === "working"
                                    loops: Animation.Infinite
                                    from: 0
                                    to: 360
                                    duration: 900
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            StyledText {
                                Layout.fillWidth: true
                                text: String(workCard.task.title || "Working task")
                                color: Colours.palette.m3onSurface
                                font.pixelSize: 12
                                font.weight: Font.Medium
                                elide: Text.ElideRight
                                maximumLineCount: 1
                            }
                            StyledText {
                                Layout.fillWidth: true
                                readonly property string status: String(workCard.task.status || "working")
                                readonly property string detail: workCard.done ? "Done"
                                    : status === "waiting" ? "Waiting"
                                    : status === "blocked" ? "Blocked"
                                    : (Number(workCard.task.progress || 0) > 0
                                        ? Math.round(Number(workCard.task.progress) * 100) + "% · Working"
                                        : "Working…")
                                text: workCard.task.kind === "manual" && workCard.task.summary
                                    ? detail + " · " + String(workCard.task.summary).split("\n")[0]
                                    : detail
                                color: Colours.palette.m3onSurfaceVariant
                                font.pixelSize: 10
                                elide: Text.ElideRight
                            }
                        }

                        StyledRect {
                            id: workVoiceButton
                            visible: String(workCard.task.url || "").length > 0
                            Layout.preferredWidth: 28
                            Layout.preferredHeight: 28
                            radius: 14
                            color: workVoiceHover.hovered
                                ? Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
                                : "transparent"
                            HoverHandler { id: workVoiceHover }
                            TapHandler {
                                onTapped: root.command("work-voice", String(workCard.task.id || ""))
                            }
                            Canvas {
                                anchors.centerIn: parent
                                width: 11
                                height: 14
                                onPaint: {
                                    const ctx = getContext("2d");
                                    ctx.reset();
                                    ctx.strokeStyle = Colours.palette.m3onSurfaceVariant.toString();
                                    ctx.lineWidth = 1.6;
                                    ctx.lineCap = "round";
                                    ctx.beginPath();
                                    ctx.roundedRect(3.1, 1.1, 4.8, 7.6, 2.4, 2.4);
                                    ctx.moveTo(1.6, 6.0); ctx.quadraticCurveTo(5.5, 11.8, 9.4, 6.0);
                                    ctx.moveTo(5.5, 10.0); ctx.lineTo(5.5, 12.5);
                                    ctx.moveTo(3.2, 12.5); ctx.lineTo(7.8, 12.5);
                                    ctx.stroke();
                                }
                            }
                        }

                        StyledRect {
                            id: workDeleteButton
                            Layout.preferredWidth: 26
                            Layout.preferredHeight: 26
                            radius: 13
                            color: workDeleteHover.hovered
                                ? Qt.alpha(Colours.palette.m3errorContainer, 0.72)
                                : "transparent"
                            HoverHandler { id: workDeleteHover }
                            TapHandler {
                                onTapped: root.command("work-delete-user", String(workCard.task.id || ""))
                            }
                            Canvas {
                                anchors.centerIn: parent
                                width: 9
                                height: 9
                                onPaint: {
                                    const ctx = getContext("2d");
                                    ctx.reset();
                                    ctx.strokeStyle = (workDeleteHover.hovered
                                        ? Colours.palette.m3onErrorContainer
                                        : Colours.palette.m3onSurfaceVariant).toString();
                                    ctx.lineWidth = 1.6;
                                    ctx.lineCap = "round";
                                    ctx.beginPath();
                                    ctx.moveTo(1.4, 1.4); ctx.lineTo(7.6, 7.6);
                                    ctx.moveTo(7.6, 1.4); ctx.lineTo(1.4, 7.6);
                                    ctx.stroke();
                                }
                            }
                        }
                    }
                }
            }
        }

        TasksView {
            id: tasksCard
            visible: root.tasksCardVisible
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: implicitWidth
            Layout.preferredHeight: implicitHeight
        }
    }

    HyprlandFocusGrab {
        id: composerFocusGrab
        active: root.panelVisible && composerField.activeFocus
        windows: QsWindow.window ? [QsWindow.window] : []
        onCleared: composerField.focus = false
    }

    Binding {
        when: root.panelVisible && (root.composerVisible || (T.LoomState.whiteboardVisible && root.uiHasInput))
        target: QsWindow.window
        property: "WlrLayershell.keyboardFocus"
        value: WlrKeyboardFocus.OnDemand
    }

    onComposerVisibleChanged: {
        if (!composerVisible)
            composerField.focus = false;
    }
}
