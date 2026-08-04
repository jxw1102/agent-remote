import bb.cascades 1.4

// Project list. Two modes:
//   - filter mode (default): tap a project to filter the sessions list
//   - pick mode (pickForNewSession): tap a project to start a new session
// Created by ApiClient::createProjectsPage(); the pusher pins `api`/`nav`.
// No bare `_api` in this document (context lookups break after push/pop).
Page {
    id: projectsPage
    actionBarVisibility: ChromeVisibility.Hidden

    property variant api
    property variant nav
    property bool pickForNewSession: false
    property bool ready: false

    property int projRev: projectsPage.api ? projectsPage.api.projectsRev : 0
    onProjRevChanged: {
        if (ready && projectsPage.api)
            rebuild();
    }
    onApiChanged: {
        if (ready && projectsPage.api) {
            rebuild();
            rebuildChips();
        }
    }

    // Unified: the daemon is a choice, so it is asked FIRST - these chips
    // switch the active profile and reload the projects underneath. All
    // display fields are baked into the model rows (memory: never call
    // ListItem.view functions from item bindings).
    function rebuildChips() {
        var a = projectsPage.api;
        if (! a || ! a.unified)
            return;
        profChipsModel.clear();
        var ps = a.profiles;
        var act = a.activeProfileIndex;
        for (var i = 0; i < ps.length; ++i) {
            var prov = ps[i].provider ? ("" + ps[i].provider) : "";
            profChipsModel.append({
                label: (i === act ? "● " : "") +
                       (ps[i].name || qsTr("Profile %1").arg(i + 1)),
                bg: i === act ? "#2a2d33" : "#161616",
                fg: a.providerAccent(prov)
            });
        }
    }

    function rebuild() {
        var a = projectsPage.api;
        if (! a)
            return;
        projectsModel.clear();
        if (! pickForNewSession) {
            // Synthetic first row to clear the filter
            projectsModel.append({
                id: "", name: qsTr("All projects"), cwd: "",
                session_count: 0, synthetic: true
            });
        } else {
            // Synthetic first row: start a session in a brand-new (or typed)
            // path. The New Session sheet's directory field is editable, so
            // this just opens it blank - the daemon treats an unknown path as
            // a new project and a matching one as the existing project.
            projectsModel.append({
                id: "", name: qsTr("+  New project (enter a path)..."), cwd: "",
                session_count: 0, synthetic: true, newproj: true
            });
        }
        projectsModel.append(a.projects);
    }

    onCreationCompleted: {
        ready = true;
        if (projectsPage.api) {
            rebuild();
            rebuildChips();
        }
    }

    titleBar: TitleBar {
        title: pickForNewSession ? qsTr("New session - pick a project")
                                 : qsTr("Projects")
        acceptAction: ActionItem {
            title: ""
            imageSource: "asset:///images/ic_close.png"
            onTriggered: {
                if (projectsPage.nav)
                    projectsPage.nav.pop();
            }
        }
    }

    Container {
        background: Color.Black
        horizontalAlignment: HorizontalAlignment.Fill
        verticalAlignment: VerticalAlignment.Fill
        layout: StackLayout {}

        // ---- Unified only: which daemon runs this session. Tap to switch;
        // the project list below reloads from the picked daemon. ----
        Container {
            horizontalAlignment: HorizontalAlignment.Fill
            leftPadding: 14
            rightPadding: 14
            topPadding: 8
            bottomPadding: 2
            visible: projectsPage.api
                     ? (projectsPage.api.unified
                        && projectsPage.api.profiles.length > 1)
                     : false

            Label {
                text: qsTr("Daemon (tap to switch)")
                textStyle.fontSize: FontSize.XSmall
                textStyle.color: Color.create("#888888")
            }
            ListView {
                id: profChips
                dataModel: profChipsModel
                horizontalAlignment: HorizontalAlignment.Fill
                preferredHeight: 76
                layout: StackListLayout {
                    orientation: LayoutOrientation.LeftToRight
                }
                scrollRole: ScrollRole.None

                listItemComponents: [
                    ListItemComponent {
                        type: ""
                        // Explicit preferredWidth: item roots ignore Fill /
                        // minWidth (settings-chip pattern, proven on device).
                        Container {
                            rightMargin: 12
                            preferredHeight: 60
                            maxHeight: 60
                            preferredWidth: 260
                            background: Color.create(ListItemData.bg)
                            leftPadding: 20
                            rightPadding: 20
                            layout: DockLayout {}
                            Label {
                                verticalAlignment: VerticalAlignment.Center
                                horizontalAlignment: HorizontalAlignment.Center
                                text: ListItemData.label
                                multiline: false
                                textStyle.fontSize: FontSize.Small
                                textStyle.color: Color.create(ListItemData.fg)
                                textStyle.fontWeight: FontWeight.Bold
                            }
                        }
                    }
                ]

                onTriggered: {
                    var api = projectsPage.api;
                    if (! api)
                        return;
                    var row = 0;
                    if (indexPath && indexPath.length > 0)
                        row = indexPath[0];
                    // Light switch: the merged session list survives; the
                    // projects below belong to the new daemon.
                    api.switchProfile(row);
                    projectsPage.rebuildChips();
                    api.fetchProjects();
                }
            }
        }

        Container {
            horizontalAlignment: HorizontalAlignment.Fill
            leftPadding: 14
            rightPadding: 14
            topPadding: 6
            bottomPadding: 6
            background: Color.create("#161616")
            visible: projectsPage.api ? projectsPage.api.projectsStatus != "" : false
            layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

            ActivityIndicator {
                preferredWidth: 28
                preferredHeight: 28
                running: projectsPage.api
                         ? projectsPage.api.projectsStatus == qsTr("Loading...") : false
                visible: running
                verticalAlignment: VerticalAlignment.Center
                rightMargin: 8
            }
            Label {
                text: projectsPage.api ? projectsPage.api.projectsStatus : ""
                multiline: true
                textStyle.fontSize: FontSize.XSmall
                textStyle.color: Color.create("#9a9a9a")
                verticalAlignment: VerticalAlignment.Center
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
            }
        }

        ListView {
            id: projectsList
            dataModel: projectsModel
            horizontalAlignment: HorizontalAlignment.Fill
            layoutProperties: StackLayoutProperties { spaceQuota: 1 }

            // Item components can't see the page's `api` pin - bridge the
            // brand accent / width through the view (GrokRemote pattern).
            property string accent: projectsPage.api
                                    ? projectsPage.api.accentColor : "#00A8DF"
            property int rowWidth: projectsPage.api
                                   ? projectsPage.api.screenWidth : 720

            listItemComponents: [
                ListItemComponent {
                    type: ""
                    Container {
                        background: Color.Black
                        horizontalAlignment: HorizontalAlignment.Fill
                        // Fill isn't honored on item roots (see main.qml).
                        preferredWidth: ListItem.view
                                        ? ListItem.view.rowWidth : 720
                        leftPadding: 14
                        rightPadding: 14
                        topPadding: 12
                        bottomPadding: 12

                        // Project name - the row's one brand-colored element.
                        Label {
                            text: ListItemData.name
                            multiline: false
                            textStyle.fontSize: FontSize.Small
                            textStyle.color: Color.create(ListItem.view.accent)
                            textStyle.fontWeight: FontWeight.Bold
                        }
                        Label {
                            text: ListItemData.cwd
                            multiline: false
                            visible: ListItemData.cwd != ""
                            textStyle.fontSize: FontSize.XXSmall
                            textStyle.color: Color.create("#9a9a9a")
                            topMargin: 2
                        }
                        Label {
                            text: ListItemData.session_count + qsTr(" sessions")
                            multiline: false
                            visible: ! ListItemData.synthetic
                            textStyle.fontSize: FontSize.XXSmall
                            textStyle.color: Color.create(ListItem.view.accent)
                            topMargin: 2
                        }

                        contextActions: [
                            ActionSet {
                                title: ListItemData.name
                                ActionItem {
                                    title: qsTr("New session here")
                                    imageSource: "asset:///images/ic_add.png"
                                    enabled: ListItemData.cwd != ""
                                    onTriggered: {
                                        ListItem.view.requestNewSession(
                                            ListItemData.cwd, ListItemData.name);
                                    }
                                }
                            }
                        ]
                    }
                }
            ]

            function requestNewSession(cwd, name) {
                // Root page pins newSessionRequestRev: it pops this page
                // and opens the New Session sheet.
                if (projectsPage.api)
                    projectsPage.api.requestNewSession(cwd, name);
            }

            onTriggered: {
                var api = projectsPage.api;
                if (! api)
                    return;
                try {
                    var row = 0;
                    if (indexPath && indexPath.length > 0)
                        row = indexPath[0];
                    var project = projectsModel.data([ row ]);
                    if (! project)
                        return;
                    if (projectsPage.pickForNewSession) {
                        if (project.newproj) {
                            // Open the sheet with a blank, editable path.
                            api.requestNewSession("", qsTr("New project"));
                            return;
                        }
                        if (project.cwd && project.cwd != "")
                            api.requestNewSession("" + project.cwd, "" + project.name);
                        return;
                    }
                    api.setProjectFilter("" + (project.id || ""),
                                         project.synthetic ? "" : "" + project.name);
                    if (projectsPage.nav)
                        projectsPage.nav.pop();
                } catch (e) {
                    try { api.reportUiError("pick project: " + e); } catch (e2) {}
                }
            }
        }
    }

    attachedObjects: [
        ArrayDataModel {
            id: projectsModel
        },
        ArrayDataModel {
            id: profChipsModel
        }
    ]
}
