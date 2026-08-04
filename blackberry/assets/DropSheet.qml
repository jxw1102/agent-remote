import bb.cascades 1.4

// Host->phone inbox. The agent (or you) copies files into the daemon drop
// folder; this sheet lists them and downloads each into Downloads/Inbox.
//
// Rebuild pattern: C++ bumps api.dropRev; the page clears and re-appends.
// Never use Connections{} / signal.connect().
Sheet {
    id: dropSheet

    property variant api
    property bool showing: false

    peekEnabled: false

    property int dRev: dropSheet.api ? dropSheet.api.dropRev : 0
    onDRevChanged: {
        if (showing)
            rebuild();
    }

    function rebuild() {
        var files = dropSheet.api ? dropSheet.api.dropFiles : [];
        // Tapping a row downloads it, and the C++ bumps dropRev for every
        // download/delete/status change — so a clear()+append() here sent the
        // list back to the first row under your finger. When the same files
        // are still listed, replace the rows in place instead: the scroll
        // position (and the row you tapped) stays put.
        if (files.length == dropModel.size()) {
            var same = true;
            for (var i = 0; i < files.length; ++i) {
                var cur = dropModel.data([ i ]);
                if (! cur || ("" + cur.name) != ("" + files[i].name)) {
                    same = false;
                    break;
                }
            }
            if (same) {
                for (var j = 0; j < files.length; ++j)
                    dropModel.replace(j, files[j]);
                return;
            }
        }
        dropModel.clear();
        if (files.length > 0)
            dropModel.append(files);
    }

    function show() {
        if (dropSheet.api)
            dropSheet.api.fetchDropFiles();
        rebuild();
        showing = true;
        open();
    }

    onClosed: {
        showing = false;
    }

    Page {
        id: dropPage

        titleBar: TitleBar {
            title: qsTr("Inbox")
            dismissAction: ActionItem {
                title: qsTr("Done")
                onTriggered: dropSheet.close()
            }
            acceptAction: ActionItem {
                title: qsTr("Refresh")
                onTriggered: {
                    if (dropSheet.api)
                        dropSheet.api.fetchDropFiles();
                }
            }
        }

        Container {
            background: Color.Black
            horizontalAlignment: HorizontalAlignment.Fill
            verticalAlignment: VerticalAlignment.Fill
            layout: StackLayout {}

            // How to use + host / phone paths.
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 20
                rightPadding: 20
                topPadding: 12
                bottomPadding: 10
                background: Color.create("#161616")

                Label {
                    text: qsTr("Ask the agent to put files in the host drop folder. Tap a row to save it into Downloads/Inbox.")
                    multiline: true
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#b0b0b0")
                }
                Label {
                    visible: dropSheet.api && dropSheet.api.dropPath != ""
                    text: dropSheet.api ? ("Host: " + dropSheet.api.dropPath) : ""
                    multiline: true
                    topMargin: 6
                    textStyle.fontSize: FontSize.XXSmall
                    textStyle.color: Color.create("#6a9ab0")
                }
                Label {
                    visible: dropSheet.api && dropSheet.api.dropLocalDir != ""
                    text: dropSheet.api ? ("Phone: " + dropSheet.api.dropLocalDir) : ""
                    multiline: true
                    topMargin: 2
                    textStyle.fontSize: FontSize.XXSmall
                    textStyle.color: Color.create("#6a9ab0")
                }
            }

            // Status strip (loading / empty / download result).
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 20
                rightPadding: 20
                topPadding: 10
                bottomPadding: 10
                background: Color.create("#1a1a1a")
                visible: dropSheet.api ? dropSheet.api.dropStatus != "" : false
                layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

                ActivityIndicator {
                    preferredWidth: 28
                    preferredHeight: 28
                    running: dropSheet.api
                             ? (dropSheet.api.dropStatus == qsTr("Loading...")
                                || ("" + dropSheet.api.dropStatus).indexOf("Downloading") == 0
                                || ("" + dropSheet.api.dropStatus).indexOf("Deleting") == 0)
                             : false
                    visible: running
                    verticalAlignment: VerticalAlignment.Center
                    rightMargin: 8
                }
                Label {
                    text: dropSheet.api ? dropSheet.api.dropStatus : ""
                    multiline: true
                    autoSize.maxLineCount: 2
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#c0a0a0")
                    verticalAlignment: VerticalAlignment.Center
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                }
            }

            ListView {
                id: dropList
                dataModel: dropModel
                horizontalAlignment: HorizontalAlignment.Fill
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                property int rowWidth: dropSheet.api
                                       ? dropSheet.api.screenWidth : 720

                // Whole row downloads (Button inside list items is unreliable
                // on BB10 - same lesson as QueueSheet / Load older).
                onTriggered: {
                    var row = indexPath && indexPath.length > 0 ? indexPath[0] : -1;
                    if (row < 0 || ! dropSheet.api)
                        return;
                    var item = dropModel.data([ row ]);
                    if (item && ("" + item.name) != "") {
                        // Unified rows say which daemon holds the file;
                        // -1 = active profile (single-provider builds).
                        var pidx = (item.profileIndex === undefined
                                    || item.profileIndex === null)
                                   ? -1 : item.profileIndex;
                        dropSheet.api.downloadDropFrom(pidx, "" + item.name);
                    }
                }

                listItemComponents: [
                    ListItemComponent {
                        type: ""
                        Container {
                            id: dropRow
                            background: Color.Black
                            horizontalAlignment: HorizontalAlignment.Fill
                            preferredWidth: ListItem.view
                                            ? ListItem.view.rowWidth : 720
                            leftPadding: 20
                            rightPadding: 12
                            topPadding: 12
                            bottomPadding: 12
                            layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

                            contextActions: [
                                ActionSet {
                                    title: ListItemData.name
                                    ActionItem {
                                        title: qsTr("Download")
                                        imageSource: "asset:///images/ic_download.png"
                                        onTriggered: {
                                            var a = ListItemData.api;
                                            var p = (ListItemData.profileIndex
                                                     === undefined)
                                                    ? -1 : ListItemData.profileIndex;
                                            if (a)
                                                a.downloadDropFrom(p, "" + ListItemData.name);
                                        }
                                    }
                                    ActionItem {
                                        title: qsTr("Delete on host")
                                        imageSource: "asset:///images/ic_delete.png"
                                        onTriggered: {
                                            var a = ListItemData.api;
                                            var p = (ListItemData.profileIndex
                                                     === undefined)
                                                    ? -1 : ListItemData.profileIndex;
                                            if (a)
                                                a.deleteDropFrom(p, "" + ListItemData.name);
                                        }
                                    }
                                }
                            ]

                            ImageView {
                                preferredWidth: 36
                                preferredHeight: 36
                                minWidth: 36
                                minHeight: 36
                                rightMargin: 12
                                verticalAlignment: VerticalAlignment.Center
                                imageSource: "asset:///images/ic_inbox.png"
                            }
                            Container {
                                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                                layout: StackLayout {}
                                verticalAlignment: VerticalAlignment.Center

                                Label {
                                    text: ListItemData.name
                                    multiline: true
                                    autoSize.maxLineCount: 2
                                    textStyle.fontSize: FontSize.Small
                                    textStyle.color: Color.create("#e8e8e8")
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                                Label {
                                    // Size + last-modified (daemon mtime, local).
                                    text: {
                                        var size = ListItemData.size_text
                                                   ? ListItemData.size_text
                                                   : (ListItemData.size + " B");
                                        var when = ListItemData.mtime_text
                                                   ? ("" + ListItemData.mtime_text)
                                                   : "";
                                        var line = when != ""
                                                   ? (size + " · " + when) : size;
                                        // Unified: which daemon holds it (and
                                        // where an identical copy was hidden).
                                        if (ListItemData.profileName)
                                            line = ListItemData.profileName
                                                   + " · " + line;
                                        if (ListItemData.also)
                                            line = line + " · "
                                                   + ListItemData.also;
                                        return line;
                                    }
                                    textStyle.fontSize: FontSize.XXSmall
                                    textStyle.color: Color.create("#8a8a8a")
                                }
                            }
                        }
                    }
                ]
            }
        }
    }

    attachedObjects: [
        ArrayDataModel {
            id: dropModel
        }
    ]
}
