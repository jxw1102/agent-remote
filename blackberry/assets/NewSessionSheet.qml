import bb.cascades 1.4

Sheet {
    id: newSessionSheet

    // Pinned by main.qml (api: nav.api) - no bare `_api` in this document.
    property variant api
    property string cwd: ""
    property string projectName: ""
    // Multi-harness: which provider to start on (claude|grok|codex).
    property string harness: ""

    signal startRequested(string cwd, string prompt, string provider)

    peekEnabled: false

    // Set cwd/projectName first, then call show(). The picked project's
    // path prefills the editable working-directory field.
    function show() {
        promptArea.text = "";
        cwdField.text = newSessionSheet.cwd;
        // Default harness: first from multi list, else profile provider.
        var hs = harnesses();
        newSessionSheet.harness = hs.length ? hs[0] : "";
        rebuildHarnessChips();
        open();
    }

    function harnesses() {
        var a = newSessionSheet.api;
        if (! a)
            return [];
        try {
            return a.profileHarnesses() || [];
        } catch (e) {
            return [];
        }
    }

    function needsCwd() {
        var a = newSessionSheet.api;
        if (! a)
            return true;
        if (newSessionSheet.harness != "") {
            try {
                return a.harnessRequiresCwd(newSessionSheet.harness);
            } catch (e) {}
        }
        return a.capRequiresCwd;
    }

    function rebuildHarnessChips() {
        harnessModel.clear();
        var hs = harnesses();
        var a = newSessionSheet.api;
        for (var i = 0; i < hs.length; ++i) {
            var h = hs[i];
            var label = h;
            if (h == "claude") label = "Claude";
            else if (h == "grok") label = "Grok";
            else if (h == "codex") label = "Codex";
            harnessModel.append({
                id: h,
                name: label,
                accent: a ? a.providerAccent(h) : "#00A8DF",
                selected: h == newSessionSheet.harness
            });
        }
        harnessContainer.visible = hs.length > 1;
    }

    Page {
        titleBar: TitleBar {
            title: qsTr("New session")
            dismissAction: ActionItem {
                title: qsTr("Cancel")
                onTriggered: newSessionSheet.close()
            }
            acceptAction: ActionItem {
                title: qsTr("Start")
                // Always enabled: the daemon runs jobs concurrently. If
                // another job is being tracked, ApiClient detaches from it
                // (it keeps running server-side) before starting this one.
                enabled: true
                onTriggered: {
                    var prompt = promptArea.text.trim();
                    var dir = cwdField.text.trim();
                    if (dir == "")
                        dir = newSessionSheet.cwd;
                    if (prompt == "")
                        return;
                    if (dir == "" && newSessionSheet.needsCwd())
                        return;
                    newSessionSheet.close();
                    newSessionSheet.startRequested(
                        dir, prompt, newSessionSheet.harness);
                }
            }
        }

        Container {
            leftPadding: 24
            rightPadding: 24
            topPadding: 24

            Label {
                text: newSessionSheet.projectName
                textStyle.fontSize: FontSize.Large
            }

            // Unified: restates which daemon is active.
            Label {
                visible: newSessionSheet.api ? newSessionSheet.api.unified : false
                text: {
                    var a = newSessionSheet.api;
                    if (! a)
                        return "";
                    var ps = a.profiles;
                    var i = a.activeProfileIndex;
                    if (! ps || i < 0 || i >= ps.length)
                        return "";
                    return qsTr("Runs on %1").arg(ps[i].name || "");
                }
                topMargin: 4
                textStyle.fontSize: FontSize.XSmall
                textStyle.color: Color.create("#9a9a9a")
            }

            // Multi-harness picker (Claude / Grok / Codex).
            Container {
                id: harnessContainer
                visible: false
                topMargin: 16

                Label {
                    text: qsTr("Which harness?")
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }

                ListView {
                    id: harnessList
                    topMargin: 8
                    preferredHeight: 56
                    dataModel: ArrayDataModel { id: harnessModel }
                    layout: StackListLayout {
                        orientation: LayoutOrientation.LeftToRight
                    }
                    listItemComponents: [
                        ListItemComponent {
                            type: ""
                            Container {
                                id: chipRoot
                                leftPadding: 4
                                rightPadding: 4
                                Container {
                                    leftPadding: 14
                                    rightPadding: 14
                                    topPadding: 10
                                    bottomPadding: 10
                                    background: ListItemData.selected
                                                ? Color.create(ListItemData.accent)
                                                : Color.create("#2a2a2a")
                                    Label {
                                        text: ListItemData.name
                                        textStyle.fontSize: FontSize.Small
                                        textStyle.color: ListItemData.selected
                                                         ? Color.Black
                                                         : Color.create(ListItemData.accent)
                                    }
                                }
                            }
                        }
                    ]
                    onTriggered: {
                        var item = dataModel.data(indexPath);
                        if (! item)
                            return;
                        newSessionSheet.harness = item.id;
                        newSessionSheet.rebuildHarnessChips();
                    }
                }
            }

            Label {
                text: qsTr("Working directory")
                topMargin: 16
                textStyle.fontSize: FontSize.XSmall
                textStyle.color: Color.create("#888888")
            }
            TextField {
                id: cwdField
                hintText: newSessionSheet.needsCwd()
                          ? qsTr("/path/on/the/server")
                          : qsTr("optional - server workspace if empty")
                inputMode: TextFieldInputMode.Url
            }

            TextArea {
                id: promptArea
                topMargin: 24
                hintText: {
                    var a = newSessionSheet.api;
                    var name = "the agent";
                    if (newSessionSheet.harness == "claude") name = "Claude";
                    else if (newSessionSheet.harness == "grok") name = "Grok";
                    else if (newSessionSheet.harness == "codex") name = "Codex";
                    else if (a) name = a.agentName;
                    return qsTr("What should %1 do?").arg(name);
                }
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
            }
        }
    }
}
