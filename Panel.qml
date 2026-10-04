pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Caelestia.Config
import qs.components
import qs.components.controls
import qs.services
import qs.utils
import dcqwqc.tabby.services as T

Item {
    id: root

    property real phase: 0
    property bool hovered: hover.hovered
    property bool composerVisible: T.TabbyState.inputArmed && (hovered || composerField.activeFocus || composerHover.hovered)

    readonly property string python: "/usr/bin/python3"
    readonly property string ctlPath: Paths.toLocalFile(Qt.resolvedUrl("tabbyctl.py"))
    readonly property bool panelVisible: T.TabbyState.enabled && T.TabbyState.summoned
    readonly property bool panelInputEnabled: true
    readonly property bool panelOverFullscreen: true
    readonly property bool panelLiftShadow: T.TabbyState.whiteboardVisible || composerVisible
    readonly property real panelDeformAmount: 0.025
    readonly property int panelMotionDuration: 180

    implicitWidth: T.TabbyState.whiteboardVisible ? 360 : (composerVisible ? 360 : 82)
    implicitHeight: T.TabbyState.whiteboardVisible
        ? (composerVisible ? 278 : 224)
        : (composerVisible ? 98 : 48)

    Behavior on implicitWidth { NumberAnimation { duration: 170; easing.type: Easing.OutCubic } }
    Behavior on implicitHeight { NumberAnimation { duration: 170; easing.type: Easing.OutCubic } }

    function command(name: string, value: string): void {
        const args = [root.python, root.ctlPath, name];
        if (value !== undefined && value !== null && value.length > 0)
            args.push(value);
        Quickshell.execDetached(args);
    }

    function submit(): void {
        const value = composerField.text;
        composerField.text = "";
        root.command("send-text", value);
    }

    Timer {
        interval: 42
        repeat: true
        running: root.panelVisible
        onTriggered: {
            root.phase += T.TabbyState.state === "thinking" || T.TabbyState.state === "tool" ? 0.22 : 0.12;
            face.requestPaint();
            thinkingCanvas.requestPaint();
        }
    }

    HoverHandler { id: hover }

    ColumnLayout {
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 6

        Item {
            id: faceArea
            Layout.alignment: Qt.AlignHCenter
            width: 76
            height: 40

            Canvas {
                id: face
                anchors.centerIn: parent
                width: 64
                height: 38
                readonly property color ink: T.TabbyState.state === "error"
                    ? Colours.palette.m3error
                    : Colours.palette.m3primary

                onPaint: {
                    const ctx = getContext("2d");
                    ctx.reset();
                    ctx.lineCap = "round";
                    ctx.lineJoin = "round";
                    ctx.strokeStyle = ink.toString();
                    ctx.fillStyle = ink.toString();
                    ctx.lineWidth = 3.2;
                    const state = T.TabbyState.state;
                    const blink = Math.floor(root.phase * 1.6) % 47 === 0;
                    let look = 0;
                    if (state === "thinking" || state === "tool")
                        look = Math.sin(root.phase * 1.7) * 2.8;

                    if (blink) {
                        ctx.beginPath();
                        ctx.moveTo(17, 18); ctx.lineTo(24, 18);
                        ctx.moveTo(40, 18); ctx.lineTo(47, 18);
                        ctx.stroke();
                    } else {
                        const eyeH = state === "wake" || state === "listening" ? 10 : 8;
                        ctx.fillRect(17 + look, 14, 6, eyeH);
                        ctx.fillRect(41 + look, 14, 6, eyeH);
                    }

                    ctx.beginPath();
                    if (state === "speaking") {
                        // Real PCM amplitude from the current output monitor.
                        // No synthetic mouth oscillator while speaking.
                        const level = Math.max(0, Math.min(1, T.TabbyState.audioLevel));
                        const mouthH = 2.0 + Math.pow(level, 0.72) * 10.5;
                        ctx.ellipse(26, 29 - mouthH / 2, 12, mouthH);
                    } else if (state === "thinking" || state === "tool") {
                        const wobble = Math.sin(root.phase * 2.4) * 1.3;
                        ctx.moveTo(26, 29 + wobble);
                        ctx.quadraticCurveTo(32, 26 - wobble, 38, 29 + wobble);
                    } else if (state === "success") {
                        ctx.arc(32, 24, 8, 0.2, Math.PI - 0.2);
                    } else if (state === "error") {
                        ctx.arc(32, 35, 7, Math.PI + 0.25, Math.PI * 2 - 0.25);
                    } else if (state === "wake" || state === "listening") {
                        ctx.arc(32, 28, 3, 0, Math.PI * 2);
                    } else {
                        ctx.moveTo(27, 28);
                        ctx.quadraticCurveTo(32, 31, 37, 28);
                    }
                    ctx.stroke();
                }

                Connections {
                    target: T.TabbyState
                    function onStateChanged(): void { face.requestPaint(); }
                    function onAudioLevelChanged(): void { face.requestPaint(); }
                }
            }

            Canvas {
                id: thinkingCanvas
                anchors.fill: parent
                visible: T.TabbyState.state === "thinking" || T.TabbyState.state === "tool"
                opacity: 0.8
                onPaint: {
                    const ctx = getContext("2d");
                    ctx.reset();
                    ctx.fillStyle = Colours.palette.m3primary.toString();
                    for (let i = 0; i < 3; i++) {
                        const a = root.phase * 1.6 + i * Math.PI * 2 / 3;
                        const x = width / 2 + Math.cos(a) * 33;
                        const y = height / 2 + Math.sin(a) * 17;
                        const r = 1.4 + ((Math.sin(a + root.phase) + 1) / 2) * 1.2;
                        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
                    }
                }
            }

            Rectangle {
                id: closeButton
                visible: root.hovered
                width: 20
                height: 20
                anchors.right: parent.right
                anchors.top: parent.top
                radius: 10
                color: Colours.tPalette.m3surfaceContainerHighest
                opacity: closeTap.pressed ? 0.75 : 0.96

                MaterialIcon {
                    anchors.centerIn: parent
                    text: "close"
                    font.pixelSize: 14
                    color: Colours.palette.m3onSurface
                }

                TapHandler {
                    id: closeTap
                    onTapped: root.command("close", "")
                }
            }
        }

        StyledRect {
            id: composer
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 340
            Layout.preferredHeight: 42
            visible: root.composerVisible
            radius: 18
            color: Colours.tPalette.m3surfaceContainer

            HoverHandler { id: composerHover }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 6
                spacing: 4

                MaterialIcon {
                    visible: T.TabbyState.attachmentPending
                    text: "image"
                    color: Colours.palette.m3primary
                    font.pixelSize: 17
                }

                TextField {
                    id: composerField
                    Layout.fillWidth: true
                    Layout.preferredHeight: 36
                    placeholderText: T.TabbyState.attachmentPending ? "Add a message…" : "Message Tabby…"
                    horizontalAlignment: TextInput.AlignLeft
                    selectByMouse: true
                    color: Colours.palette.m3onSurface
                    placeholderTextColor: Colours.palette.m3outline
                    selectionColor: Colours.palette.m3primaryContainer
                    selectedTextColor: Colours.palette.m3onPrimaryContainer
                    background: null
                    font.pixelSize: 13

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
                        }
                        if (event.key === Qt.Key_Escape) {
                            event.accepted = true;
                            root.command("hide-input", "");
                        }
                    }
                }

                Rectangle {
                    width: 28
                    height: 28
                    radius: 14
                    color: attachTap.pressed ? Colours.palette.m3surfaceContainerHighest : "transparent"
                    MaterialIcon {
                        anchors.centerIn: parent
                        text: "attach_file"
                        font.pixelSize: 18
                        color: Colours.palette.m3onSurfaceVariant
                    }
                    TapHandler { id: attachTap; onTapped: root.command("paste-clipboard", "") }
                }

                Rectangle {
                    width: 30
                    height: 30
                    radius: 15
                    color: Colours.palette.m3primary
                    opacity: sendTap.pressed ? 0.75 : 1
                    MaterialIcon {
                        anchors.centerIn: parent
                        text: "arrow_upward"
                        font.pixelSize: 18
                        color: Colours.palette.m3onPrimary
                    }
                    TapHandler { id: sendTap; onTapped: root.submit() }
                }
            }
        }

        StyledRect {
            id: board
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 340
            Layout.preferredHeight: 164
            visible: T.TabbyState.whiteboardVisible
            radius: 14
            color: Colours.tPalette.m3surfaceContainer
            clip: true

            Repeater {
                model: T.TabbyState.items
                delegate: Item {
                    id: entryRoot
                    required property var modelData
                    required property int index
                    anchors.fill: parent
                    readonly property var entry: modelData || ({})

                    StyledText {
                        visible: entryRoot.entry.type === "text"
                        x: 14
                        y: 10 + entryRoot.index * 34
                        width: board.width - 28
                        text: entryRoot.entry.title
                            ? String(entryRoot.entry.title) + "\n" + String(entryRoot.entry.text || "")
                            : String(entryRoot.entry.text || "")
                        color: Colours.palette.m3onSurface
                        font.pixelSize: 13
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }

                    Item {
                        visible: entryRoot.entry.type === "progress"
                        x: 14
                        y: 12 + entryRoot.index * 34
                        width: board.width - 28
                        height: 28
                        StyledText {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            text: entryRoot.entry.label || "Working…"
                            font.pixelSize: 12
                        }
                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 6
                            radius: 3
                            color: Colours.palette.m3surfaceContainerHighest
                            Rectangle {
                                width: parent.width * Math.max(0, Math.min(1, Number(entryRoot.entry.value || 0)))
                                height: parent.height
                                radius: parent.radius
                                color: Colours.palette.m3primary
                            }
                        }
                    }

                    StyledText {
                        visible: entryRoot.entry.type === "choice"
                        x: 14
                        y: 10 + entryRoot.index * 34
                        width: board.width - 28
                        text: "> " + String(entryRoot.entry.label || "")
                            + ((entryRoot.entry.options && entryRoot.entry.options.length)
                                ? "   " + entryRoot.entry.options.join("  /  ") : "")
                        font.pixelSize: 13
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }

    onComposerVisibleChanged: {
        if (composerVisible)
            Qt.callLater(() => composerField.forceActiveFocus());
    }
}
