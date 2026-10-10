import QtQuick
import QtQuick.Layouts
import QtTest

// Offscreen Qt Quick integration test for the exact notification X layout
// pattern used by Panel.qml. No connection to live Quickshell or Zen.
Item {
    id: window
    width: 400
    height: 220
    visible: true
    property string lastDismissed: ""
    property int dismissCount: 0

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        ColumnLayout {
            id: column
            width: parent.width
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: "Loom native notification live test"
                }
                Rectangle {
                    id: closeButton
                    Layout.preferredWidth: 22
                    Layout.preferredHeight: 22
                    radius: 11
                    color: "lightgray"
                    Text { anchors.centerIn: parent; text: "×"; font.pixelSize: 12 }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            window.lastDismissed = "selected-id"
                            window.dismissCount += 1
                        }
                    }
                }
            }
        }
    }

    TestCase {
        name: "NotificationCloseTarget"
        when: windowShown
        function test_closeButton_hit_area_inside_flickable() {
            compare(closeButton.width, 22)
            compare(closeButton.height, 22)
            mouseClick(closeButton, 11, 11, Qt.LeftButton)
            tryCompare(window, "dismissCount", 1)
            compare(window.lastDismissed, "selected-id")
        }
    }
}
