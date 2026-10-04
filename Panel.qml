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

    implicitWidth: T.TabbyState.whiteboardVisible ? 360 : (composerVisible ? 360 : 104)
    implicitHeight: T.TabbyState.whiteboardVisible
        ? (composerVisible ? 278 : 224)
        : (composerVisible ? 108 : 62)

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
            stateHalo.requestPaint();
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
            width: 96
            height: 56

            readonly property string mood: T.TabbyState.state
            readonly property real level: Math.max(0, Math.min(1, T.TabbyState.audioLevel))

            // State halo: breathing while listening, audio-reactive while
            // speaking, and a fast pop when Tabby first wakes.
            Canvas {
                id: stateHalo
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
                        ctx.lineWidth = 3.4;
                        const state = T.TabbyState.state;
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
                            const level = Math.max(0, Math.min(1, T.TabbyState.audioLevel));
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
                        target: T.TabbyState
                        function onStateChanged(): void {
                            root.phase = 0;
                            face.requestPaint();
                            stateHalo.requestPaint();
                            thinkingCanvas.requestPaint();
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
                    visible: T.TabbyState.state === "thinking" || T.TabbyState.state === "tool"
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

            Rectangle {
                id: closeButton
                visible: root.hovered
                width: 22
                height: 22
                anchors.right: parent.right
                anchors.top: parent.top
                radius: 11
                scale: root.hovered ? 1.0 : 0.8
                color: Colours.tPalette.m3surfaceContainerHighest
                opacity: closeTap.pressed ? 0.72 : 0.98

                Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

                MaterialIcon {
                    anchors.centerIn: parent
                    text: "close"
                    font.pixelSize: 15
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
