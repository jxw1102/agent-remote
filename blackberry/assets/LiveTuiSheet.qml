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

    onOpened: keySink.requestFocus()

    onClosed: {
        showing = false;
        if (liveTuiSheet.api)
            liveTuiSheet.api.stopLiveTui();
    }

    function sendKey(name) {
        if (liveTuiSheet.api)
            liveTuiSheet.api.sendTuiKey(name);
    }

    // A soft key must not cost the keyboard: a Cascades Button takes focus
    // when it is tapped, and the sink is the only node physical keys reach.
    function softKey(name) {
        liveTuiSheet.sendKey(name);
        keySink.requestFocus();
    }

    function toggleCtrl() {
        if (liveTuiSheet.api)
            liveTuiSheet.api.toggleTuiCtrl();
        keySink.requestFocus();
    }

    // THE PHYSICAL KEYBOARD. Everything Cascades knows goes to C++, which
    // decides what the key was: `event.key` alone is not a character (a
    // modifier arrives through it as a keycode with the top byte gone, which
    // is how shift types an accented a), so only `keycap` tells them apart.
    function onKey(ev) {
        if (! liveTuiSheet.showing || ! liveTuiSheet.api)
            return;
        // The line box owns the keyboard whenever it has focus - that is
        // where an input method composes, and none of it may leak into the
        // terminal.
        if (lineField.focused)
            return;
        liveTuiSheet.api.sendTuiKeyEvent(ev.unicode, ev.keycap, ev.key,
                                         ev.ctrlPressed);
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
        // On the PAGE, not on the focused node: key events propagate from the
        // focused node up to the root, and are redirected to the root when
        // nothing has focus at all. One listener catches both, so nothing
        // double-fires.
        keyListeners: [
            KeyListener {
                onKeyPressed: liveTuiSheet.onKey(event)
            }
        ]

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

            // The hold state, spelled out: a Button tint is not worth the
            // risk on this QML engine, but a Label is - and the user must
            // see that Ctrl is armed before the next key.
            Label {
                visible: liveTuiSheet.api ? liveTuiSheet.api.tuiCtrlLatch : false
                text: qsTr("Ctrl held - the next key is sent as Ctrl+key")
                textStyle.fontSize: FontSize.XXSmall
                textStyle.color: Color.create("#4ad06a")
                leftPadding: 12
                rightPadding: 12
                bottomMargin: 2
            }

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

                // BB10 keyboards have no Ctrl key of their own, so this one
                // HOLDS Ctrl: tap it, then press a key (or type one letter)
                // and it arrives as Ctrl+<key>; the hold ends with that key.
                // The label and the hint line above say whether it is armed
                // - the state has to be readable at a glance, on a small
                // screen.
                Button {
                    // No readonly property here: Cascades' QML engine on
                    // BB10 rejects it ("Readonly not yet supported"), which
                    // stops main.qml from loading this sheet at all.
                    text: (liveTuiSheet.api && liveTuiSheet.api.tuiCtrlLatch)
                          ? "Ctrl \u25CF" : "Ctrl"
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    onClicked: liveTuiSheet.toggleCtrl()
                }
                Button {
                    text: "Esc"
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    onClicked: liveTuiSheet.softKey("Escape")
                }
                Button {
                    text: "\u21E5"   // ⇥ Tab
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    onClicked: liveTuiSheet.softKey("Tab")
                }
                Button {
                    text: "\u2191"   // ↑
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    onClicked: liveTuiSheet.softKey("Up")
                }
                Button {
                    text: "\u2193"   // ↓
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    onClicked: liveTuiSheet.softKey("Down")
                }
                Button {
                    text: "\u2190"   // ←
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    onClicked: liveTuiSheet.softKey("Left")
                }
                Button {
                    text: "\u2192"   // →
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    onClicked: liveTuiSheet.softKey("Right")
                }
                Button {
                    text: "\u21B5"   // ↵ Enter
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    onClicked: liveTuiSheet.softKey("Enter")
                }
            }

            ScrollView {
                // Passport: the capacitive-keyboard swipe scrolls the page's
                // MAIN scrollable. Cascades only auto-picks one when it has no
                // siblings (see ListView::scrollRole), and every list here sits
                // beside chrome - so nothing was ever the main scrollable and the
                // gesture had nothing to drive. Say so explicitly.
                scrollRole: ScrollRole.Main
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

                    // Tap the pane to take the keyboard back from the line box.
                    gestureHandlers: [
                        TapHandler {
                            onTapped: keySink.requestFocus()
                        }
                    ]

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
                    // Leaving the line box puts the physical keyboard back on
                    // the terminal rather than nowhere.
                    onFocusedChanged: {
                        if (! focused && liveTuiSheet.showing)
                            keySink.requestFocus();
                    }
                    input {
                        submitKey: SubmitKey.Send
                        onSubmitted: liveTuiSheet.sendLine()
                    }
                }
                // THE FOCUS SINK, and the node physical keys are delivered
                // to. Deliberately TINY: Cascades paints a focus highlight on
                // whatever holds focus, and only text controls can turn it off
                // - a big focused container paints the whole screen blue.
                Container {
                    id: keySink
                    preferredWidth: 4
                    preferredHeight: 4
                    verticalAlignment: VerticalAlignment.Bottom
                    // Not 0: a fully transparent control can be treated as
                    // not-visible and skipped.
                    opacity: 0.01
                    focusPolicy: FocusPolicy.KeyAndTouch
                }
                Button {
                    text: qsTr("Send")
                    preferredWidth: 100
                    preferredHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    minHeight: liveTuiSheet.api
                              ? Math.round(48 * liveTuiSheet.api.uiScalePct / 100) : 48
                    maxHeight: liveTuiSheet.api
                              ? Math.round(52 * liveTuiSheet.api.uiScalePct / 100) : 52
                    leftMargin: 6
                    onClicked: {
                        liveTuiSheet.sendLine();
                        keySink.requestFocus();
                    }
                }
            }
        }
    }
}
