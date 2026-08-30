import bb.cascades 1.4
import bb.system 1.2

Sheet {
    id: settingsSheet

    // Pinned by main.qml (api: nav.api) - no bare `_api` in this document.
    property variant api
    signal saved()

    peekEnabled: false

    property bool ready: false
    // Which profile the form edits; -1 = creating a new one.
    property int editIndex: 0

    // Keep the profile list in sync while the sheet is open.
    property variant profSnapshot: settingsSheet.api ? settingsSheet.api.profiles : []
    onProfSnapshotChanged: {
        if (! ready)
            return;
        // A tap only changes WHICH profile is active, and activateProfile()
        // emits profilesChanged() too — so rebuilding here scrolled the strip
        // back to the first chip on every tap. Repaint in place unless the
        // set of profiles itself changed (add / delete / rename).
        if (sameProfileNames())
            refreshProfileChips();
        else
            rebuildProfiles();
    }

    // True when the model already lists exactly these profiles, in order.
    function sameProfileNames() {
        var a = settingsSheet.api;
        var ps = a ? a.profiles : [];
        if (ps.length != profilesModel.size())
            return false;
        for (var i = 0; i < ps.length; ++i) {
            var item = profilesModel.data([ i ]);
            var raw = "" + (ps[i].name || "");
            var nm = raw != "" ? raw : qsTr("(unnamed)");
            if (! item || ("" + item.name) != nm)
                return false;
        }
        return true;
    }

    // Height of the horizontal profile strip (single row of name chips).
    // Classic 84. The chips hold text, and system FontSizes are larger on
    // a Passport, so a fixed strip clipped them.
    property int profileStripH: settingsSheet.api
                                ? Math.round(84 * settingsSheet.api.uiScalePct / 100)
                                : 84
    property int chipH: settingsSheet.api
                        ? Math.round(60 * settingsSheet.api.uiScalePct / 100) : 60
    property int chipW: settingsSheet.api
                        ? Math.round(220 * settingsSheet.api.uiScalePct / 100) : 220

    function rebuildProfiles() {
        // Bake every display field (name, chip colors, active dot) into the
        // row. Item visuals bound to bridge functions (ListItem.view.isActive)
        // threw and left the chip's name blank; ListItemData fields render
        // reliably (see bb10-cascades-rules).
        profilesModel.clear();
        var a = settingsSheet.api;
        var ps = a ? a.profiles : [];
        var act = a ? a.activeProfileIndex : 0;
        var acc = a ? a.accentColor : "#00A8DF";
        var abg = a ? a.themeBannerBg : "#1e2a1e";
        for (var i = 0; i < ps.length; ++i) {
            var raw = "" + (ps[i].name || "");
            // enabled is absent on profiles saved before the switch existed.
            var on = (ps[i].enabled === undefined) ? true : ps[i].enabled;
            profilesModel.append({
                // Baked per row: inside a ListItemComponent only ListItemData
                // always resolves.
                h: settingsSheet.chipH,
                w: settingsSheet.chipW,
                name: raw != "" ? raw : qsTr("(unnamed)"),
                bg: (i == act) ? abg : "#161616",
                fg: ! on ? "#6a6a6a" : ((i == act) ? acc : "#ffffff"),
                dot: ! on ? "\u25CB  " : ((i == act) ? "\u25CF  " : "")
            });
        }
        profilesList.scrollToPosition(ScrollPosition.Beginning,
                                      ScrollAnimation.None);
    }

    // Repaint the chips for a new active profile WITHOUT touching the model's
    // shape. rebuildProfiles() clears/re-appends and then scrolls to the
    // first chip, so calling it after a tap threw the strip back to profile
    // one — the row you just tapped could scroll out of view.
    function refreshProfileChips() {
        var a = settingsSheet.api;
        if (! a)
            return;
        var act = a.activeProfileIndex;
        var acc = a.accentColor;
        var abg = a.themeBannerBg;
        for (var i = 0; i < profilesModel.size(); ++i) {
            var item = profilesModel.data([ i ]);
            if (! item)
                continue;
            var ps2 = a.profiles;
            var on = (i >= ps2.length || ps2[i].enabled === undefined)
                     ? true : ps2[i].enabled;
            var bg = (i == act) ? abg : "#161616";
            var fg = ! on ? "#6a6a6a" : ((i == act) ? acc : "#ffffff");
            var dot = ! on ? "○  " : ((i == act) ? "●  " : "");
            if (("" + item.bg) == ("" + bg) && ("" + item.fg) == ("" + fg)
                    && ("" + item.dot) == ("" + dot))
                continue;
            item.bg = bg;
            item.fg = fg;
            item.dot = dot;
            profilesModel.replace(i, item);
        }
    }

    function fillForm(index) {
        editIndex = index;
        if (index < 0 || ! settingsSheet.api
                || index >= settingsSheet.api.profiles.length) {
            nameField.text = "";
            urlField.text = "";
            tokenField.text = "";
            return;
        }
        var p = settingsSheet.api.profiles[index];
        nameField.text = "" + (p.name || "");
        urlField.text = "" + (p.baseUrl || "");
        tokenField.text = "" + (p.token || "");
        // Absent flag = enabled (profiles saved before the switch existed).
        enabledToggle.checked = (p.enabled === undefined) ? true : p.enabled;
    }

    // Prefill the form from the active profile, then open the sheet.
    function show() {
        ready = false;
        rebuildProfiles();
        fillForm(settingsSheet.api ? settingsSheet.api.activeProfileIndex : 0);
        ready = true;
        open();
    }

    Page {
        titleBar: TitleBar {
            title: qsTr("Settings")
            dismissAction: ActionItem {
                title: qsTr("Cancel")
                onTriggered: settingsSheet.close()
            }
            acceptAction: ActionItem {
                title: qsTr("Done")
                onTriggered: {
                    if (settingsSheet.api) {
                        settingsSheet.api.saveProfile(settingsSheet.editIndex,
                                                      nameField.text,
                                                      urlField.text,
                                                      tokenField.text);
                        settingsSheet.editIndex = settingsSheet.api.activeProfileIndex;
                    }
                    settingsSheet.saved();
                    settingsSheet.close();
                }
            }
        }

        ScrollView {
            // Passport: the capacitive-keyboard swipe scrolls the page's
            // MAIN scrollable. Cascades only auto-picks one when it has no
            // siblings (see ListView::scrollRole), and every list here sits
            // beside chrome - so nothing was ever the main scrollable and the
            // gesture had nothing to drive. Say so explicitly.
            scrollRole: ScrollRole.Main
            Container {
                leftPadding: 24
                rightPadding: 24
                topPadding: 24

                // ---- Profiles: one per daemon host; tap to switch. ----
                Label {
                    text: qsTr("Profiles (tap to switch)")
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                ListView {
                    id: profilesList
                    dataModel: profilesModel
                    horizontalAlignment: HorizontalAlignment.Fill
                    preferredHeight: settingsSheet.profileStripH
                    // Horizontal strip of name-only chips (see quirk in
                    // memory: Fill is ignored on item roots, so each chip
                    // sizes to its own text - exactly what we want here).
                    layout: StackListLayout {
                        orientation: LayoutOrientation.LeftToRight
                    }
                    // Don't hijack the sheet's vertical scroll.
                    scrollRole: ScrollRole.None

                    listItemComponents: [
                        ListItemComponent {
                            type: ""
                            // Name-only chip. Item roots don't honor Fill or
                            // minWidth (see ProjectsPage / memory), so the box
                            // painted but the DockLayout-centered name never got
                            // measured - a blank chip. An explicit preferredWidth
                            // makes the row lay out (proven in commit 866102a).
                            // All visuals bind to ListItemData (baked in
                            // rebuildProfiles), never ListItem.view functions.
                            Container {
                                rightMargin: 12
                                preferredHeight: ListItemData.h ? ListItemData.h : 60
                                maxHeight: ListItemData.h ? ListItemData.h : 60
                                preferredWidth: ListItemData.w ? ListItemData.w : 220
                                background: Color.create(ListItemData.bg)
                                leftPadding: 22
                                rightPadding: 22
                                layout: DockLayout {}
                                Label {
                                    verticalAlignment: VerticalAlignment.Center
                                    horizontalAlignment: HorizontalAlignment.Center
                                    text: ListItemData.dot + ListItemData.name
                                    multiline: false
                                    textStyle.fontSize: FontSize.Small
                                    textStyle.color: Color.create(ListItemData.fg)
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                            }
                        }
                    ]

                    onTriggered: {
                        var row = 0;
                        if (indexPath && indexPath.length > 0)
                            row = indexPath[0];
                        if (! settingsSheet.api)
                            return;
                        settingsSheet.api.activateProfile(row);
                        settingsSheet.fillForm(row);
                        // In place: keeps the strip where you left it.
                        settingsSheet.refreshProfileChips();
                        settingsSheet.saved();
                    }
                }

                Container {
                    layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                    topMargin: 8
                    horizontalAlignment: HorizontalAlignment.Fill
                    Button {
                        text: qsTr("Add profile")
                        layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                        onClicked: {
                            settingsSheet.fillForm(-1);
                            nameField.requestFocus();
                        }
                    }
                    Button {
                        text: qsTr("Delete")
                        leftMargin: 8
                        layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                        enabled: settingsSheet.editIndex >= 0
                        onClicked: {
                            if (! settingsSheet.api
                                    || settingsSheet.editIndex < 0)
                                return;
                            var p = settingsSheet.api.profiles[
                                        settingsSheet.editIndex];
                            var n = p ? ("" + (p.name || "")) : "";
                            deleteConfirm.body = n != ""
                                ? qsTr("Delete profile \"%1\"? This cannot be undone.")
                                      .arg(n)
                                : qsTr("Delete this profile? This cannot be undone.");
                            deleteConfirm.show();
                        }
                    }
                }

                // ---- The selected (or new) profile's connection. ----
                Label {
                    text: settingsSheet.editIndex < 0
                          ? qsTr("New profile")
                          : qsTr("Edit profile")
                    topMargin: 24
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                TextField {
                    id: nameField
                    hintText: qsTr("Name (e.g. Mac, VPS)")
                }
                TextField {
                    id: urlField
                    topMargin: 8
                    hintText: qsTr("http://host:port (agentremoted)")
                    inputMode: TextFieldInputMode.Url
                }
                TextField {
                    id: tokenField
                    topMargin: 8
                    hintText: qsTr("token - python3 -m agentremoted --print-token")
                }

                // Switch a daemon off without deleting it: Agent Remote polls
                // every profile (sessions, drop, usage, status sockets), so a
                // host that is asleep or unreachable otherwise costs a
                // timeout on every sweep. Disabled profiles are never
                // contacted; the chip shows a hollow ring.
                Container {
                    visible: settingsSheet.editIndex >= 0
                    topMargin: 16
                    horizontalAlignment: HorizontalAlignment.Fill
                    layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                    Label {
                        text: qsTr("Enabled")
                        verticalAlignment: VerticalAlignment.Center
                        layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    }
                    ToggleButton {
                        id: enabledToggle
                        verticalAlignment: VerticalAlignment.Center
                        onCheckedChanged: {
                            var a = settingsSheet.api;
                            if (! settingsSheet.ready || ! a
                                    || settingsSheet.editIndex < 0)
                                return;
                            a.setProfileEnabled(settingsSheet.editIndex, checked);
                            // The C++ refuses to switch off the last enabled
                            // profile — snap back if it did.
                            checked = a.profileEnabled(settingsSheet.editIndex);
                        }
                    }
                }

                // Permission mode / model / effort now live in the
                // swipe-down "Session" menu (they change per turn, not per
                // connection). Settings is just the daemon connection.

                Button {
                    text: qsTr("Test connection")
                    topMargin: 24
                    horizontalAlignment: HorizontalAlignment.Fill
                    onClicked: {
                        var a = settingsSheet.api;
                        if (! a)
                            return;
                        // Save the form first so the test hits these values
                        a.saveProfile(settingsSheet.editIndex, nameField.text,
                                      urlField.text, tokenField.text);
                        settingsSheet.editIndex = a.activeProfileIndex;
                        a.ping();
                    }
                }
                Label {
                    id: pingLabel
                    // Pure property bindings - no pingResult.connect().
                    text: ! settingsSheet.api ? ""
                          : settingsSheet.api.pingState == 1 ? qsTr("Testing...")
                          : settingsSheet.api.pingState == 2 ? qsTr("Connected: ") + settingsSheet.api.pingInfo
                          : settingsSheet.api.pingState == 3 ? qsTr("Failed: ") + settingsSheet.api.pingInfo
                          : ""
                    visible: settingsSheet.api ? settingsSheet.api.pingState != 0 : false
                    multiline: true
                    textStyle.fontSize: FontSize.Small
                    textStyle.color: ! settingsSheet.api ? Color.create("#888888")
                                     : settingsSheet.api.pingState == 2 ? Color.create("#44cc66")
                                     : settingsSheet.api.pingState == 3 ? Color.create("#cc4444")
                                     : Color.create("#888888")
                    horizontalAlignment: HorizontalAlignment.Center
                }

                Label {
                    text: qsTr("The daemon must be reachable from the phone. On a LAN keep it on a trusted network - plain HTTP is not encrypted; over the internet front it with Cloudflare.")
                    topMargin: 24
                    multiline: true
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#666666")
                }

                // Crash / error log (GrokRemote feature): UI errors, paint
                // fallbacks, and the tail of any crash dump from a
                // previous run.
                Label {
                    text: qsTr("Crash / error log")
                    topMargin: 32
                    visible: settingsSheet.api ? settingsSheet.api.errorLog != "" : false
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#888888")
                }
                Container {
                    visible: settingsSheet.api ? settingsSheet.api.errorLog != "" : false
                    horizontalAlignment: HorizontalAlignment.Fill
                    background: Color.create("#1a1a1a")
                    leftPadding: 8
                    rightPadding: 8
                    topPadding: 6
                    bottomPadding: 6
                    topMargin: 4
                    Label {
                        text: settingsSheet.api ? settingsSheet.api.errorLog : ""
                        multiline: true
                        textStyle.fontSize: FontSize.XXSmall
                        textStyle.color: Color.create("#9a9a9a")
                    }
                }
                Button {
                    text: qsTr("Clear log")
                    topMargin: 8
                    visible: settingsSheet.api ? settingsSheet.api.errorLog != "" : false
                    horizontalAlignment: HorizontalAlignment.Fill
                    onClicked: {
                        if (settingsSheet.api)
                            settingsSheet.api.clearErrorLog();
                    }
                }

                Label {
                    text: settingsSheet.api
                          ? settingsSheet.api.brandName + " " + settingsSheet.api.brandVersion
                          : ""
                    topMargin: 16
                    bottomMargin: 24
                    horizontalAlignment: HorizontalAlignment.Center
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#555555")
                }
            }
        }
    }

    attachedObjects: [
        ArrayDataModel {
            id: profilesModel
        },
        SystemDialog {
            id: deleteConfirm
            title: qsTr("Delete profile")
            confirmButton.label: qsTr("Delete")
            cancelButton.label: qsTr("Cancel")
            onFinished: {
                if (value != SystemUiResult.ConfirmButtonSelection)
                    return;
                if (settingsSheet.api && settingsSheet.editIndex >= 0) {
                    settingsSheet.api.deleteProfile(settingsSheet.editIndex);
                    settingsSheet.fillForm(
                        settingsSheet.api.activeProfileIndex);
                    settingsSheet.saved();
                }
            }
        }
    ]
}
