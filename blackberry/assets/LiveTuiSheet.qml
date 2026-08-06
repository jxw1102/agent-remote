import bb.cascades 1.4

// Live TUI — host tmux pane for the open Interactive session.
// Polls GET /api/sessions/<id>/tui; soft keys / line box POST …/tui/keys.
// Opened from the drag-down menu in Interactive mode (Queue is headless-only).
Sheet {
    id: liveTuiSheet

    property variant api
    property bool showing: false
    property string lineText: ""

    peekEnabled: false

    property int tRev: liveTuiSheet.api ? liveTuiSheet.api.tuiRev : 0
    onTRevChanged: {
        // Property pin only — Label text binds to api.tuiText directly.
    }

    function show() {
        if (! liveTuiSheet.api)
            return;
        liveTuiSheet.lineText = "";
        liveTuiSheet.api.startLiveTui();
        showing = true;
        open();
    }

    onClosed: {
        showing = false;
        if (liveTuiSheet.api)
            liveTuiSheet.api.stopLiveTui();
    }

    function sendKey(name) {
        if (liveTuiSheet.api)
            liveTuiSheet.api.sendTuiKey(name);
    }

    function sendLine() {
        if (! liveTuiSheet.api)
            return;
        var t = liveTuiSheet.lineText;
        if (! t || t.length == 0)
            return;
        liveTuiSheet.api.sendTuiLine(t);
        liveTuiSheet.lineText = "";
        lineField.text = "";
    }

    Page {
        titleBar: TitleBar {
            // ASCII separators only — BB10 title bars mojibake UTF-8 middle dots.
            title: {
                var a = liveTuiSheet.api;
                if (! a)
                    return qsTr("Live TUI");
                var st = a.tuiStatus || "";
                if (st.length > 0)
                    return qsTr("Live TUI - %1").arg(st);
                return qsTr("Live TUI");
            }
            dismissAction: ActionItem {
                title: qsTr("Close")
                onTriggered: liveTuiSheet.close()
            }
        }

        Container {
            background: Color.create("#0a0c10")
            horizontalAlignment: HorizontalAlignment.Fill
            verticalAlignment: VerticalAlignment.Fill
            layout: StackLayout {}

            // Soft keys — one compact row (same Button chrome).
            // Esc as ASCII: ⎋ is missing from BB system fonts and shows as tofu.
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 4
                rightPadding: 4
                topPadding: 4
                bottomPadding: 4
                layout: StackLayout {
                    orientation: LayoutOrientation.LeftToRight
                }

                Button {
                    text: "Esc"
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    onClicked: liveTuiSheet.sendKey("Escape")
                }
                Button {
                    text: "\u21E5"   // ⇥ Tab
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    onClicked: liveTuiSheet.sendKey("Tab")
                }
                Button {
                    text: "\u2191"   // ↑
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    onClicked: liveTuiSheet.sendKey("Up")
                }
                Button {
                    text: "\u2193"   // ↓
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    onClicked: liveTuiSheet.sendKey("Down")
                }
                Button {
                    text: "\u2190"   // ←
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    onClicked: liveTuiSheet.sendKey("Left")
                }
                Button {
                    text: "\u2192"   // →
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    onClicked: liveTuiSheet.sendKey("Right")
                }
                Button {
                    text: "^C"
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    onClicked: liveTuiSheet.sendKey("Ctrl+C")
                }
                Button {
                    text: "\u21B5"   // ↵ Enter
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    onClicked: liveTuiSheet.sendKey("Enter")
                }
            }

            ScrollView {
                horizontalAlignment: HorizontalAlignment.Fill
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                scrollViewProperties {
                    scrollMode: ScrollMode.Both
                    pinchToZoomEnabled: false
                    overScrollEffectMode: OverScrollEffectMode.None
                }

                Container {
                    background: Color.create("#0a0c10")
                    horizontalAlignment: HorizontalAlignment.Fill
                    leftPadding: 8
                    rightPadding: 8
                    topPadding: 4
                    bottomPadding: 4

                    Label {
                        text: liveTuiSheet.api
                              ? (liveTuiSheet.api.tuiText || "")
                              : ""
                        multiline: true
                        // Smaller than XSmall so more of the host pane fits.
                        textStyle.fontSize: FontSize.XXSmall
                        textStyle.color: Color.create("#d0d4dc")
                        textStyle.fontFamily: "Courier New"
                        textStyle.fontWeight: FontWeight.Normal
                    }
                }
            }

            // Line box → text + Enter into the host TUI.
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 8
                rightPadding: 8
                topPadding: 6
                bottomPadding: 10
                layout: StackLayout {
                    orientation: LayoutOrientation.LeftToRight
                }

                TextField {
                    id: lineField
                    hintText: qsTr("Line into TUI")
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    inputMode: TextFieldInputMode.Text
                    textStyle.fontFamily: "Courier New"
                    textStyle.fontSize: FontSize.XSmall
                    onTextChanging: {
                        liveTuiSheet.lineText = text;
                    }
                    input {
                        submitKey: SubmitKey.Send
                        onSubmitted: liveTuiSheet.sendLine()
                    }
                }
                Button {
                    text: qsTr("Send")
                    preferredWidth: 100
                    preferredHeight: 48
                    minHeight: 48
                    maxHeight: 52
                    leftMargin: 6
                    onClicked: liveTuiSheet.sendLine()
                }
            }
        }
    }
}
