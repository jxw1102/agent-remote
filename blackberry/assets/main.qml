import bb.cascades 1.4
import bb.system 1.2

NavigationPane {
    id: nav

    // Cache the context property once: ListView handlers (and pushed pages)
    // lose the root `_api` lookup after push/pop on BB10 Cascades - a silent
    // ReferenceError. Everything below reaches the client via nav.api.
    // (Pattern proven on-device by GrokRemote.)
    property variant api

    backButtonsVisible: false

    // Swipe down from the top bezel: app-wide drop-down menu.
    Menu.definition: MenuDefinition {
        actions: [
            ActionItem {
                // One menu slot: Live TUI in Interactive mode, Queue in Headless.
                // (ActionItem has no `visible` in Cascades — only title/image/enabled.)
                title: (nav.api && nav.api.liveTuiMenu)
                       ? qsTr("Live TUI")
                       : ((nav.api && nav.api.queuedCount > 0)
                          ? qsTr("Queue (%1)").arg(nav.api.queuedCount)
                          : qsTr("Queue"))
                imageSource: (nav.api && nav.api.liveTuiMenu)
                             ? "asset:///images/ic_tui.png"
                             : "asset:///images/ic_queue.png"
                // Interactive: need an open session. Headless: daemon queue always.
                enabled: nav.api
                         ? (nav.api.liveTuiMenu
                            ? nav.api.liveTuiEnabled
                            : nav.api.queueSupported)
                         : true
                onTriggered: {
                    if (nav.api && nav.api.liveTuiMenu)
                        liveTuiSheet.show();
                    else
                        queueSheet.show();
                }
            },
            ActionItem {
                // Next-turn options: permission mode, model, reasoning
                // effort - whichever the daemon supports. Menu cells are
                // narrow, so keep it to one word; the model/permission detail
                // lives inside the sheet (titled "Session").
                title: qsTr("Session")
                enabled: nav.api ? (nav.api.capSetModel || nav.api.capPermissions
                                    || nav.api.capSetEffort) : false
                imageSource: "asset:///images/ic_model.png"
                onTriggered: sessionSheet.show()
            },
            ActionItem {
                // In-app sheet from daemon /api/usage whenever the daemon
                // serves it (capShowUsage): claude reads the subscription
                // endpoint, grok's daemon reads its TUI's /usage command.
                // BRAND_USAGE_URL (grok: grok.com) is the fallback for a
                // daemon too old to answer /api/usage.
                // (ActionItem has no `visible` in Cascades - only `enabled`.)
                title: qsTr("Usage")
                // Unified: the sheet aggregates every profile, so gate only
                // on being configured - capShowUsage reflects just the
                // ACTIVE daemon and would grey the menu out wrongly.
                enabled: nav.api
                         ? (nav.api.unified
                            ? nav.api.configured
                            : (nav.api.capShowUsage || nav.api.usageOpensBrowser))
                         : false
                imageSource: "asset:///images/ic_usage.png"
                onTriggered: {
                    if (nav.api && nav.api.capShowUsage)
                        usageSheet.show();
                    else if (nav.api && nav.api.usageOpensBrowser)
                        nav.api.openUsageInBrowser();
                }
            },
            ActionItem {
                // Focus / All. One slot that toggles, labelled with where it
                // will take you — menu cells are too narrow for two entries,
                // and the count tells you whether it is worth going.
                title: (nav.api && nav.api.focusMode)
                       ? qsTr("All sessions")
                       : ((nav.api && nav.api.focusCount > 0)
                          ? qsTr("Focus (%1)").arg(nav.api.focusCount)
                          : qsTr("Focus"))
                imageSource: "asset:///images/ic_queue.png"
                enabled: nav.api ? nav.api.capFocus : false
                onTriggered: {
                    if (nav.api)
                        nav.api.focusMode = !nav.api.focusMode;
                }
            },
            ActionItem {
                // Host->phone drop (menu cells are narrow - one word like
                // Queue/Session/Usage). Files land in Downloads/Inbox.
                title: qsTr("Inbox")
                imageSource: "asset:///images/ic_inbox.png"
                enabled: nav.api ? nav.api.configured : false
                onTriggered: dropSheet.show()
            }
        ]
    }

    onPopTransitionEnded: {
        page.destroy();
    }

    Page {
        id: sessionsPage
        actionBarVisibility: ChromeVisibility.Hidden

        property bool ready: false

        // Property pins: C++ bumps a rev, the page rebuilds its model.
        property int sessRev: nav.api ? nav.api.sessionsRev : 0
        onSessRevChanged: {
            if (! ready || ! nav.api)
                return;
            sessionsModel.clear();
            sessionsModel.append(nav.api.sessions);
        }

        // ProjectsPage requests "new session here" through C++ so the
        // sheet (owned by this root page) can open after the pop.
        property int newSessRev: nav.api ? nav.api.newSessionRequestRev : 0
        onNewSessRevChanged: {
            if (! ready || ! nav.api)
                return;
            if (nav.top != sessionsPage)
                nav.pop();
            newSessionSheet.cwd = nav.api.newSessionCwd;
            newSessionSheet.projectName = nav.api.newSessionProjectName;
            newSessionSheet.show();
        }

        function openProjects(pickMode) {
            var api = nav.api;
            if (! api)
                return;
            var page = api.createProjectsPage();
            if (! page)
                return; // factory reported the load error
            page.pickForNewSession = pickMode;
            page.api = api;
            page.nav = nav;
            nav.push(page);
            api.fetchProjects();
        }

        // GrokRemote-style chrome: hidden action bar, FreeForm title bar
        // with 44px icon buttons (gear left, folder + reload right).
        titleBar: TitleBar {
            kind: TitleBarKind.FreeForm
            scrollBehavior: TitleBarScrollBehavior.Sticky
            kindProperties: FreeFormTitleBarKindProperties {
                content: Container {
                    horizontalAlignment: HorizontalAlignment.Fill
                    verticalAlignment: VerticalAlignment.Fill
                    leftPadding: 6
                    rightPadding: 6
                    layout: DockLayout {}

                    ImageButton {
                        preferredWidth: nav.api ? nav.api.iconButtonPx : 44
                        preferredHeight: nav.api ? nav.api.iconButtonPx : 44
                        minWidth: nav.api ? nav.api.iconButtonPx : 44
                        minHeight: nav.api ? nav.api.iconButtonPx : 44
                        maxWidth: nav.api ? nav.api.iconButtonPx : 44
                        maxHeight: nav.api ? nav.api.iconButtonPx : 44
                        verticalAlignment: VerticalAlignment.Center
                        horizontalAlignment: HorizontalAlignment.Left
                        defaultImageSource: "asset:///images/ic_settings.png"
                        pressedImageSource: "asset:///images/ic_settings.png"
                        onClicked: settingsSheet.show()
                    }

                    Label {
                        text: (nav.api && nav.api.projectFilterName != "")
                              ? nav.api.projectFilterName
                              : (nav.api ? nav.api.brandName : "")
                        multiline: false
                        textStyle.fontSize: FontSize.Small
                        textStyle.color: Color.White
                        textStyle.fontWeight: FontWeight.Bold
                        verticalAlignment: VerticalAlignment.Center
                        horizontalAlignment: HorizontalAlignment.Center
                    }

                    Container {
                        verticalAlignment: VerticalAlignment.Center
                        horizontalAlignment: HorizontalAlignment.Right
                        layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

                        ImageButton {
                            preferredWidth: nav.api ? nav.api.iconButtonPx : 44
                            preferredHeight: nav.api ? nav.api.iconButtonPx : 44
                            minWidth: nav.api ? nav.api.iconButtonPx : 44
                            minHeight: nav.api ? nav.api.iconButtonPx : 44
                            maxWidth: nav.api ? nav.api.iconButtonPx : 44
                            maxHeight: nav.api ? nav.api.iconButtonPx : 44
                            rightMargin: 10
                            verticalAlignment: VerticalAlignment.Center
                            defaultImageSource: "asset:///images/ic_add.png"
                            pressedImageSource: "asset:///images/ic_add.png"
                            onClicked: sessionsPage.openProjects(true)
                        }
                        ImageButton {
                            preferredWidth: nav.api ? nav.api.iconButtonPx : 44
                            preferredHeight: nav.api ? nav.api.iconButtonPx : 44
                            minWidth: nav.api ? nav.api.iconButtonPx : 44
                            minHeight: nav.api ? nav.api.iconButtonPx : 44
                            maxWidth: nav.api ? nav.api.iconButtonPx : 44
                            maxHeight: nav.api ? nav.api.iconButtonPx : 44
                            verticalAlignment: VerticalAlignment.Center
                            defaultImageSource: "asset:///images/ic_reload.png"
                            pressedImageSource: "asset:///images/ic_reload.png"
                            onClicked: {
                                if (nav.api)
                                    nav.api.refreshSessions();
                            }
                        }
                    }
                }
            }
        }

        onCreationCompleted: {
            // Root context is reliable exactly once - here. Pin it.
            nav.api = _api;
            ready = true;
            if (_api.configured) {
                _api.refreshSessions();
            } else {
                settingsSheet.show();
            }
        }

        Container {
            background: Color.Black
            horizontalAlignment: HorizontalAlignment.Fill
            verticalAlignment: VerticalAlignment.Fill
            layout: StackLayout {}

            // Status strip: spinner while loading, message otherwise.
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 14
                rightPadding: 14
                topPadding: 6
                bottomPadding: 6
                background: Color.create("#161616")
                visible: nav.api ? nav.api.sessionsStatus != "" : false
                layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

                ActivityIndicator {
                    preferredWidth: 28
                    preferredHeight: 28
                    running: nav.api ? nav.api.sessionsStatus == qsTr("Loading...") : false
                    visible: running
                    verticalAlignment: VerticalAlignment.Center
                    rightMargin: 8
                }
                Label {
                    text: nav.api ? nav.api.sessionsStatus : ""
                    multiline: true
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#9a9a9a")
                    verticalAlignment: VerticalAlignment.Center
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                }
            }

            // Daemon-wide activity (from the /ws/status stream): how many
            // sessions are being worked on right now, on any device.
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 14
                rightPadding: 14
                topPadding: 6
                bottomPadding: 6
                background: Color.create("#161616")
                visible: nav.api ? nav.api.workingCount > 0 : false
                layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

                ActivityIndicator {
                    preferredWidth: 28
                    preferredHeight: 28
                    running: parent.visible
                    verticalAlignment: VerticalAlignment.Center
                    rightMargin: 8
                }
                Label {
                    text: nav.api
                          ? (nav.api.workingCount == 1
                             ? qsTr("1 session working")
                             : qsTr("%1 sessions working").arg(nav.api.workingCount))
                          : ""
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: nav.api ? Color.create(nav.api.accentColor)
                                             : Color.create("#9a9a9a")
                    verticalAlignment: VerticalAlignment.Center
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                }
            }

            // Full-text search (server-side). Auto-runs 50ms after typing
            // pauses; clearing the field restores the normal sessions list.
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 10
                rightPadding: 10
                topPadding: 2
                bottomPadding: 4

                TextField {
                    id: searchField
                    hintText: qsTr("Search sessions...")
                    horizontalAlignment: HorizontalAlignment.Fill
                    // textChanging fires on every key; C++ debounces 50ms and
                    // treats empty as "exit search" immediately.
                    onTextChanging: {
                        if (nav.api)
                            nav.api.scheduleSearchSessions(text);
                    }
                }
            }

            ListView {
                // Passport: the capacitive-keyboard swipe scrolls the page's
                // MAIN scrollable. Cascades only auto-picks one when it has no
                // siblings (see ListView::scrollRole), and every list here sits
                // beside chrome - so nothing was ever the main scrollable and the
                // gesture had nothing to drive. Say so explicitly.
                scrollRole: ScrollRole.Main
                id: sessionsList
                dataModel: sessionsModel
                horizontalAlignment: HorizontalAlignment.Fill
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }

                // Item components can't see nav.api - expose the accent here
                // and let rows reach it via ListItem.view (bridge pattern).
                property string accent: nav.api ? nav.api.accentColor : "#00A8DF"
                // Classic 720 / Passport 1440 - Fill is ignored on item roots.
                property int rowWidth: nav.api ? nav.api.screenWidth : 720

                listItemComponents: [
                    ListItemComponent {
                        type: ""
                        Container {
                            background: Color.Black
                            horizontalAlignment: HorizontalAlignment.Fill
                            // Explicit width: Fill is ignored on ListView item
                            // roots (tap highlight would hug the text).
                            preferredWidth: ListItem.view
                                            ? ListItem.view.rowWidth : 720
                            leftPadding: 14
                            rightPadding: 14
                            topPadding: 12
                            bottomPadding: 12

                            // Title row: optional blinking working-dot + title.
                            // ListItemData.working is overlaid from the status
                            // stream (annotateWorkingSessions); the model
                            // rebuilds on sessionsRev so animations restart.
                            Container {
                                horizontalAlignment: HorizontalAlignment.Fill
                                layout: StackLayout {
                                    orientation: LayoutOrientation.LeftToRight
                                }

                                // Blinking accent dot while this session has
                                // an active daemon job (same flag as "⚙ working").
                                Label {
                                    id: workDot
                                    text: "\u25CF"   // ●
                                    visible: ListItemData.working
                                    verticalAlignment: VerticalAlignment.Center
                                    rightMargin: 8
                                    textStyle.fontSize: FontSize.Medium
                                    textStyle.color: Color.create(
                                        ListItemData.accent
                                        ? "" + ListItemData.accent
                                        : (ListItem.view
                                           ? ListItem.view.accent : "#00A8DF"))
                                    opacity: 1.0
                                    animations: [
                                        SequentialAnimation {
                                            id: workBlink
                                            repeatCount: AnimationRepeatCount.Forever
                                            FadeTransition {
                                                duration: 500
                                                fromOpacity: 1.0
                                                toOpacity: 0.15
                                                easingCurve: StockCurve.SineInOut
                                            }
                                            FadeTransition {
                                                duration: 500
                                                fromOpacity: 0.15
                                                toOpacity: 1.0
                                                easingCurve: StockCurve.SineInOut
                                            }
                                        }
                                    ]
                                    onCreationCompleted: {
                                        if (visible)
                                            workBlink.play();
                                    }
                                    onVisibleChanged: {
                                        if (visible)
                                            workBlink.play();
                                        else {
                                            workBlink.stop();
                                            opacity = 1.0;
                                        }
                                    }
                                }

                                // Title: plain accent-colored when browsing. On
                                // the unified build each row carries its own
                                // provider accent (baked in C++); rows without
                                // one fall back to the page accent.
                                Label {
                                    visible: !(ListItemData.title_html
                                               && ListItemData.title_html != "")
                                    text: ListItemData.title
                                    textFormat: TextFormat.Plain
                                    multiline: false
                                    verticalAlignment: VerticalAlignment.Center
                                    layoutProperties: StackLayoutProperties {
                                        spaceQuota: 1
                                    }
                                    textStyle.fontSize: FontSize.Small
                                    textStyle.fontWeight: FontWeight.Bold
                                    textStyle.color: Color.create(
                                        ListItemData.accent
                                        ? "" + ListItemData.accent
                                        : (ListItem.view
                                           ? ListItem.view.accent : "#00A8DF"))
                                }
                                // Title with keyword highlights (search results).
                                // Cascades Html labels must NOT set textStyle.color
                                // - theme white would override every <font color>.
                                Label {
                                    visible: ListItemData.title_html
                                             && ListItemData.title_html != ""
                                    text: ListItemData.title_html
                                    textFormat: TextFormat.Html
                                    multiline: false
                                    verticalAlignment: VerticalAlignment.Center
                                    layoutProperties: StackLayoutProperties {
                                        spaceQuota: 1
                                    }
                                    textStyle.fontSize: FontSize.Small
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                            }
                            // Preview: last_text when browsing.
                            Label {
                                visible: !(ListItemData.snippet_html
                                           && ListItemData.snippet_html != "")
                                         && ListItemData.last_text != ""
                                text: ListItemData.last_text
                                textFormat: TextFormat.Plain
                                multiline: false
                                textStyle.fontSize: FontSize.XSmall
                                textStyle.color: Color.create("#9a9a9a")
                                topMargin: 2
                            }
                            // Match snippet with keyword highlights (search).
                            Label {
                                visible: ListItemData.snippet_html
                                         && ListItemData.snippet_html != ""
                                text: ListItemData.snippet_html
                                textFormat: TextFormat.Html
                                multiline: false
                                textStyle.fontSize: FontSize.XSmall
                                topMargin: 2
                            }
                            // 3rd line: status + the session ID appended at
                            // the end (long-press "Copy session ID" copies it).
                            Label {
                                text: {
                                    var base = ListItemData.working
                                               ? qsTr("⚙ working - ") + ListItemData.status_line
                                               : ("" + ListItemData.status_line);
                                    var id = "" + ListItemData.id;
                                    if (id == "")
                                        return base;
                                    return base != "" ? base + "  ·  " + id : id;
                                }
                                multiline: false
                                textStyle.fontSize: FontSize.XXSmall
                                textStyle.color: Color.create("#8a8a8a")
                                topMargin: 2
                            }

                            contextActions: [
                                ActionSet {
                                    title: ListItemData.title
                                    ActionItem {
                                        title: qsTr("Copy session ID")
                                        imageSource: "asset:///images/ic_copy.png"
                                        onTriggered: {
                                            // ListItem.view and _api are both
                                            // null inside a contextActions
                                            // ActionItem; the only channel that
                                            // resolves is ListItemData, so the
                                            // client is baked into each session
                                            // row (C++) as .api.
                                            var a = ListItemData.api;
                                            if (a && ListItemData.id)
                                                a.copyToClipboard(
                                                    "" + ListItemData.id);
                                        }
                                    }
                                    // Rename: the derived names are often
                                    // unrecognisable with a dozen projects in
                                    // flight, which is the point of Focus.
                                    // The dialog lives in C++ (promptRename-
                                    // Session): a document-scope SystemPrompt
                                    // id does not resolve from inside a
                                    // ListItemComponent, so showing it from
                                    // here silently did nothing.
                                    ActionItem {
                                        title: qsTr("Rename")
                                        imageSource: "asset:///images/ic_edit.png"
                                        onTriggered: {
                                            var a = ListItemData.api;
                                            if (! a || ! ListItemData.id)
                                                return;
                                            var pidx =
                                                (ListItemData.profileIndex
                                                 === undefined
                                                 || ListItemData.profileIndex
                                                 === null)
                                                ? -1
                                                : ListItemData.profileIndex;
                                            a.promptRenameSession(
                                                pidx,
                                                "" + ListItemData.id,
                                                "" + ListItemData.title);
                                        }
                                    }
                                    ActionItem {
                                        title: qsTr("Name it from the transcript")
                                        imageSource: "asset:///images/ic_title.png"
                                        onTriggered: {
                                            var a = ListItemData.api;
                                            if (! a || ! ListItemData.id)
                                                return;
                                            var pidx =
                                                (ListItemData.profileIndex
                                                 === undefined
                                                 || ListItemData.profileIndex
                                                 === null)
                                                ? -1
                                                : ListItemData.profileIndex;
                                            a.regenerateSessionTitle(
                                                pidx, "" + ListItemData.id);
                                        }
                                    }
                                    // Done takes the row out of Focus; the
                                    // session itself is untouched and stays in
                                    // the All list.
                                    ActionItem {
                                        title: ListItemData.focus
                                               ? qsTr("Done - out of Focus")
                                               : qsTr("Track in Focus")
                                        // Check = already tracking (mark done);
                                        // star = not yet tracked (add to Focus).
                                        imageSource: ListItemData.focus
                                            ? "asset:///images/ic_check.png"
                                            : "asset:///images/ic_star.png"
                                        onTriggered: {
                                            var a = ListItemData.api;
                                            if (! a || ! ListItemData.id)
                                                return;
                                            var pidx =
                                                (ListItemData.profileIndex
                                                 === undefined
                                                 || ListItemData.profileIndex
                                                 === null)
                                                ? -1
                                                : ListItemData.profileIndex;
                                            a.setFocusMember(
                                                pidx, "" + ListItemData.id,
                                                ! ListItemData.focus);
                                        }
                                    }
                                }
                            ]
                        }
                    }
                ]

                onTriggered: {
                    // NEVER use bare `_api` or outer ids here (see nav.api).
                    var api = nav.api;
                    if (! api)
                        return;
                    try {
                        var row = 0;
                        if (indexPath && indexPath.length > 0)
                            row = indexPath[0];
                        var session = sessionsModel.data([ row ]);
                        if (! session || ! session.id) {
                            api.reportUiError("No session at row " + row);
                            return;
                        }
                        // Unified rows carry the daemon they live on; -1 =
                        // plain open on the active profile (claude/grok
                        // builds, and rows from before a merge).
                        var pidx = (session.profileIndex === undefined
                                    || session.profileIndex === null)
                                   ? -1 : session.profileIndex;
                        api.openSessionRow(pidx, "" + session.id);
                        // Opening it stops flagging the finished turn.
                        api.markSessionSeen(pidx, "" + session.id);
                        var page = api.createTranscriptPage();
                        if (! page)
                            return; // factory reported the load error
                        page.api = api;
                        page.nav = nav;
                        page.sessionTitle = "" + (session.title || "");
                        nav.push(page);
                    } catch (e) {
                        try { api.reportUiError("open session: " + e); } catch (e2) {}
                    }
                }
            }
        }

        attachedObjects: [
            ArrayDataModel {
                id: sessionsModel
            },
            QueueSheet {
                id: queueSheet
                api: nav.api
            },
            LiveTuiSheet {
                id: liveTuiSheet
                api: nav.api
            },
            SessionSheet {
                id: sessionSheet
                api: nav.api
            },
            UsageSheet {
                id: usageSheet
                api: nav.api
            },
            DropSheet {
                id: dropSheet
                api: nav.api
            },
            SettingsSheet {
                id: settingsSheet
                api: nav.api
                onSaved: {
                    if (nav.api)
                        nav.api.refreshSessions();
                }
            },
            NewSessionSheet {
                id: newSessionSheet
                api: nav.api
                onStartRequested: {
                    var api = nav.api;
                    if (! api)
                        return;
                    // provider is empty on single-provider daemons; multi
                    // root needs it so agentremoted routes the harness.
                    api.startNewSession(cwd, prompt, provider || "");
                    var page = api.createTranscriptPage();
                    if (! page)
                        return;
                    page.api = api;
                    page.nav = nav;
                    page.sessionTitle = qsTr("New session");
                    nav.push(page);
                }
            }
        ]
    }
}
