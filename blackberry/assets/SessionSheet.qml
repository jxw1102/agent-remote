import bb.cascades 1.4

// Options applied to the NEXT turn, each gated by the daemon's caps:
//   - execution         Interactive (host TUI) | Headless (CLI) only.
//                       Both always bypass tool permissions.
//   - model             (--model; claude aliases / grok / codex ids)
//   - reasoning effort  (grok --effort; hidden when the harness has none)
// All are app-wide + persisted and apply to every session's next message.
// Plus the two progress-cue switches (beep / LED), also app-wide.
Sheet {
    id: sessionSheet

    property variant api          // pinned by main.qml (api: nav.api)
    property bool ready: false

    peekEnabled: false

    // Fill a DropDown from a string list, selecting `current`.
    function fillDropdown(dd, list, current) {
        dd.removeAll();
        var sel = 0;
        for (var i = 0; i < list.length; i++) {
            var name = "" + list[i];
            var opt = Qt.createQmlObject(
                "import bb.cascades 1.4; Option {}", dd);
            opt.text = (name == "default") ? qsTr("Default") : name;
            opt.value = name;
            dd.add(opt);
            if (name == current)
                sel = i;
        }
        if (dd.count() > 0)
            dd.selectedIndex = sel;
    }

    function show() {
        ready = false;
        if (sessionSheet.api) {
            var pm = "" + sessionSheet.api.permissionMode;
            interToggle.checked = (pm == "interactive");

            // Prefer the OPEN session's harness catalogues (provider_details),
            // not the multi-host root union — that made Claude sessions show
            // Grok effort pickers.
            var models = sessionSheet.api.sessionModels;
            if (! models || models.length == 0)
                models = sessionSheet.api.models;
            if (! models || models.length == 0)
                models = [ "default" ];
            var mo = sessionSheet.api.modelOverride;
            fillDropdown(modelDrop, models, mo == "" ? "default" : mo);

            var efforts = sessionSheet.api.sessionEfforts;
            if (! efforts)
                efforts = [];
            var eo = sessionSheet.api.effortOverride;
            if (efforts.length > 0)
                fillDropdown(effortDrop, efforts, eo == "" ? "default" : eo);

            soundToggle.checked = sessionSheet.api.soundCues;
            ledToggle.checked = sessionSheet.api.ledCues;
            processToggle.checked = sessionSheet.api.processView;
        }
        ready = true;
        open();
    }

    Page {
        titleBar: TitleBar {
            title: qsTr("Session")
            dismissAction: ActionItem {
                title: qsTr("Done")
                onTriggered: sessionSheet.close()
            }
        }

        ScrollView {
            Container {
                leftPadding: 24
                rightPadding: 24
                topPadding: 20
                bottomPadding: 24

                // ---- Execution: Interactive | Headless only ------------
                // Both always auto-run tools (no phone Allow/Deny panel).
                Label {
                    text: qsTr("Execution")
                    visible: sessionSheet.api ? sessionSheet.api.capInteractive : true
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                Container {
                    visible: sessionSheet.api ? sessionSheet.api.capInteractive : true
                    horizontalAlignment: HorizontalAlignment.Fill
                    layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                    Label {
                        text: interToggle.checked
                              ? qsTr("Interactive — host TUI")
                              : qsTr("Headless — one-shot CLI")
                        verticalAlignment: VerticalAlignment.Center
                        layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    }
                    ToggleButton {
                        id: interToggle
                        verticalAlignment: VerticalAlignment.Center
                        onCheckedChanged: {
                            if (sessionSheet.ready && sessionSheet.api)
                                sessionSheet.api.setPermissionMode(
                                    checked ? "interactive" : "headless");
                        }
                    }
                }
                Label {
                    visible: sessionSheet.api ? sessionSheet.api.capInteractive : true
                    text: interToggle.checked
                          ? qsTr("Runs in a tmux TUI on the host. Tools auto-run; session survives daemon restarts.")
                          : qsTr("One-shot CLI turn. Tools auto-run; no permission prompts on the phone.")
                    multiline: true
                    topMargin: 0
                    bottomMargin: 20
                    textStyle.fontSize: FontSize.XXSmall
                    textStyle.color: Color.create("#666666")
                }

                // ---- Model ---------------------------------------------
                Label {
                    text: qsTr("Model")
                    visible: sessionSheet.api ? sessionSheet.api.sessionCapSetModel : false
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                Label {
                    visible: (sessionSheet.api ? sessionSheet.api.sessionCapSetModel : false)
                             && sessionSheet.api.currentSessionId != ""
                             && sessionSheet.api.sessionModel != ""
                    text: sessionSheet.api
                          ? qsTr("This session last ran on %1").arg(sessionSheet.api.sessionModel)
                          : ""
                    multiline: true
                    textStyle.fontSize: FontSize.XXSmall
                    textStyle.color: Color.create("#666666")
                    bottomMargin: 4
                }
                DropDown {
                    id: modelDrop
                    visible: sessionSheet.api ? sessionSheet.api.sessionCapSetModel : false
                    bottomMargin: 20
                    onSelectedValueChanged: {
                        if (sessionSheet.ready && sessionSheet.api && selectedValue)
                            sessionSheet.api.setModelOverride("" + selectedValue);
                    }
                }

                // ---- Reasoning effort (grok / codex only) --------------
                Label {
                    text: qsTr("Reasoning effort")
                    visible: sessionSheet.api ? sessionSheet.api.sessionCapSetEffort : false
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                DropDown {
                    id: effortDrop
                    visible: sessionSheet.api ? sessionSheet.api.sessionCapSetEffort : false
                    onSelectedValueChanged: {
                        if (sessionSheet.ready && sessionSheet.api && selectedValue)
                            sessionSheet.api.setEffortOverride("" + selectedValue);
                    }
                }

                Label {
                    text: qsTr("Applies to the next message in every session.")
                    topMargin: 8
                    multiline: true
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#666666")
                }

                // ---- Process view (the OPEN session only) ---------------
                Label {
                    text: qsTr("Process view")
                    visible: sessionSheet.api
                             ? sessionSheet.api.currentSessionId != "" : false
                    topMargin: 24
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                Container {
                    visible: sessionSheet.api
                             ? sessionSheet.api.currentSessionId != "" : false
                    horizontalAlignment: HorizontalAlignment.Fill
                    layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                    Label {
                        text: qsTr("Show working steps")
                        verticalAlignment: VerticalAlignment.Center
                        layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    }
                    ToggleButton {
                        id: processToggle
                        verticalAlignment: VerticalAlignment.Center
                        onCheckedChanged: {
                            if (sessionSheet.ready && sessionSheet.api
                                    && sessionSheet.api.processView != checked)
                                sessionSheet.api.setProcessView(checked);
                        }
                    }
                }
                Label {
                    visible: sessionSheet.api
                             ? sessionSheet.api.currentSessionId != "" : false
                    text: qsTr("Adds the agent's tool calls, results and thinking under each message. This session only; tap a step to expand it.")
                    multiline: true
                    topMargin: 0
                    textStyle.fontSize: FontSize.XXSmall
                    textStyle.color: Color.create("#666666")
                }

                // ---- Progress cues (Chime) -----------------------------
                Label {
                    text: qsTr("Alerts")
                    topMargin: 24
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                Container {
                    horizontalAlignment: HorizontalAlignment.Fill
                    layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                    Label {
                        text: qsTr("Beep (media volume)")
                        verticalAlignment: VerticalAlignment.Center
                        layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    }
                    ToggleButton {
                        id: soundToggle
                        verticalAlignment: VerticalAlignment.Center
                        onCheckedChanged: {
                            if (sessionSheet.ready && sessionSheet.api)
                                sessionSheet.api.setSoundCues(checked);
                        }
                    }
                }
                Container {
                    horizontalAlignment: HorizontalAlignment.Fill
                    layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                    Label {
                        text: qsTr("Flash the LED")
                        verticalAlignment: VerticalAlignment.Center
                        layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    }
                    ToggleButton {
                        id: ledToggle
                        verticalAlignment: VerticalAlignment.Center
                        onCheckedChanged: {
                            if (sessionSheet.ready && sessionSheet.api)
                                sessionSheet.api.setLedCues(checked);
                        }
                    }
                }
            }
        }
    }
}
