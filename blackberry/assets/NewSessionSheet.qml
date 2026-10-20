import bb.cascades 1.4

Sheet {
    id: newSessionSheet

    // Pinned by main.qml (api: nav.api) - no bare `_api` in this document.
    property variant api
    property string cwd: ""
    property string projectName: ""
    // Multi-harness: which provider to start on (claude|grok|codex).
    property string harness: ""
    // Guards DropDown onSelectedValueChanged during programmatic setup
    // (same ready-flag pattern SessionSheet uses).
    property bool ready: false

    signal startRequested(string cwd, string prompt, string provider)

    peekEnabled: false

    // Set cwd/projectName first, then call show(). The picked project's
    // path prefills the editable working-directory field.
    function show() {
        ready = false;
        promptArea.text = "";
        cwdField.text = newSessionSheet.cwd;
        // Default harness: first from multi list, else profile provider.
        var hs = harnesses();
        newSessionSheet.harness = hs.length ? hs[0] : "";
        rebuildHarnessChips();
        rebuildModelDropdown();
        ready = true;
        open();
    }

    // Fill a DropDown from a string list, selecting `current`. Mirrors
    // SessionSheet.fillDropdown so the two pickers stay consistent.
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

    function rebuildModelDropdown() {
        var a = newSessionSheet.api;
        if (! a)
            return;
        // New session has no open session yet, so use the harness-parameterized
        // catalogue (provider_details[h].models) for the harness the user
        // picked — NOT the multi-host root union (api.models), which on a
        // multi-harness daemon is often empty / just "default". Mirrors
        // sessionModels() for an open session. Falls back to "default"
        // when the daemon hasn't advertised any for this harness.
        var h = newSessionSheet.harness;
        var models = a.harnessModels(h);
        if (! models || models.length == 0)
            models = [ "default" ];
        var mo = a.modelOverride;
        fillDropdown(modelDrop, models, mo == "" ? "default" : mo);
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
            else if (h == "deepseek" || h == "dsh") label = "DeepSeek";
            else if (h == "cursor" || h == "cursor-agent") label = "Cursor";
            else if (h == "copilot" || h == "github-copilot") label = "Copilot";
            else if (h == "codebuddy" || h == "cbc") label = "CodeBuddy";
            else if (label.length > 0)
                label = label.charAt(0).toUpperCase() + label.substring(1);
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

        ScrollView {
            // Passport: the capacitive-keyboard swipe scrolls the page's
            // MAIN scrollable. Cascades only auto-picks one when it has no
            // siblings, so say so explicitly (see SessionSheet / SettingsSheet).
            scrollRole: ScrollRole.Main
            Container {
                leftPadding: 24
                rightPadding: 24
                topPadding: 24
                bottomPadding: 24

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
                    // Not a fixed 56: on a Passport the system font is bigger,
                    // the chip grew past this box and the ListView clipped the
                    // labels (Claude/Grok/Codex sliced through the middle).
                    preferredHeight: newSessionSheet.api
                                     ? Math.round(56 * newSessionSheet.api.uiScalePct / 100)
                                     : 56
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
                        // Harness changed → its model catalogue and cap may
                        // differ (e.g. grok lists models, claude may not).
                        newSessionSheet.rebuildModelDropdown();
                    }
                }
            }

            // Model picker (per-harness cap + catalogue from /api/ping).
            // Parity with SessionSheet's picker and Android's NewSessionScreen.
            Container {
                id: modelContainer
                visible: newSessionSheet.api
                         ? newSessionSheet.api.harnessCapSetModel(newSessionSheet.harness)
                         : false
                topMargin: 16

                Label {
                    text: qsTr("Model")
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                DropDown {
                    id: modelDrop
                    topMargin: 8
                    onSelectedValueChanged: {
                        if (newSessionSheet.ready && newSessionSheet.api && selectedValue)
                            newSessionSheet.api.setModelOverride("" + selectedValue);
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
                    else if (newSessionSheet.harness == "deepseek"
                             || newSessionSheet.harness == "dsh") name = "DeepSeek";
                    else if (newSessionSheet.harness == "cursor"
                             || newSessionSheet.harness == "cursor-agent") name = "Cursor";
                    else if (newSessionSheet.harness == "copilot"
                             || newSessionSheet.harness == "github-copilot") name = "Copilot";
                    else if (newSessionSheet.harness == "codebuddy"
                             || newSessionSheet.harness == "cbc") name = "CodeBuddy";
                    else if (a) name = a.agentName;
                    return qsTr("What should %1 do?").arg(name);
                }
                // Inside a ScrollView the parent's height is unbounded, so
                // spaceQuota: 1 would collapse the compose area to its
                // preferred size. Pin a min/max instead so the field is
                // comfortable and the sheet scrolls when content overflows.
                minHeight: 160
                maxHeight: 320
                maximumLength: 8000
            }
            }
        }
    }
}
