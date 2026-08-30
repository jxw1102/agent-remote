import bb.cascades 1.4

// Queued messages waiting behind the running job. The queue lives on the
// daemon; api.queuedPrompts mirrors it ([{id, prompt}]) and each row's X
// cancels that entry server-side.
Sheet {
    id: queueSheet

    // Pinned by main.qml (api: nav.api) - no bare `_api` in this document.
    property variant api
    property bool showing: false

    peekEnabled: false

    // Property pin: queueChanged bumps queuedPrompts; rebuild while open.
    property variant queueSnapshot: queueSheet.api ? queueSheet.api.queuedPrompts : []
    onQueueSnapshotChanged: {
        if (showing)
            rebuild();
    }

    function rebuild() {
        queueModel.clear();
        if (queueSheet.api)
            queueModel.append(queueSheet.api.queuedPrompts);
    }

    function show() {
        rebuild();
        showing = true;
        open();
    }

    onClosed: {
        showing = false;
    }

    Page {
        titleBar: TitleBar {
            title: qsTr("Queued messages")
            dismissAction: ActionItem {
                title: qsTr("Close")
                onTriggered: queueSheet.close()
            }
        }

        Container {
            background: Color.create("#121212")
            horizontalAlignment: HorizontalAlignment.Fill
            verticalAlignment: VerticalAlignment.Fill
            layout: StackLayout {}

            Label {
                visible: ! queueSheet.api || queueSheet.api.queuedCount == 0
                text: qsTr("No queued messages")
                horizontalAlignment: HorizontalAlignment.Center
                topMargin: 30
                textStyle.fontSize: FontSize.Small
                textStyle.color: Color.create("#9a9a9a")
            }

            ListView {
                // Passport: the capacitive-keyboard swipe scrolls the page's
                // MAIN scrollable. Cascades only auto-picks one when it has no
                // siblings (see ListView::scrollRole), and every list here sits
                // beside chrome - so nothing was ever the main scrollable and the
                // gesture had nothing to drive. Say so explicitly.
                scrollRole: ScrollRole.Main
                id: queueList
                dataModel: queueModel
                horizontalAlignment: HorizontalAlignment.Fill
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                property int rowWidth: queueSheet.api
                                       ? queueSheet.api.screenWidth : 720
                // Bridged to the row like rowWidth: inside a ListItemComponent
                // only ListItem.view / ListItemData resolve, and preferredWidth
                // is one of the bindings that propagates reliably.
                property int iconPx: queueSheet.api
                                     ? queueSheet.api.iconButtonPx : 44

                // A tap cancels that queued prompt. A Button/ImageButton inside
                // a list item never gets the tap on BB10 (the ListView consumes
                // it for selection - same lesson as the transcript's "Load
                // older" row), so the whole row is the cancel affordance and we
                // act on the ListView's onTriggered where the `api` pin is in
                // scope.
                onTriggered: {
                    var row = indexPath && indexPath.length > 0 ? indexPath[0] : -1;
                    if (row < 0 || ! queueSheet.api)
                        return;
                    var item = queueModel.data([ row ]);
                    if (item && ("" + item.id) != "")
                        queueSheet.api.cancelQueued("" + item.id);
                }

                listItemComponents: [
                    ListItemComponent {
                        type: ""
                        Container {
                            background: Color.create("#121212")
                            horizontalAlignment: HorizontalAlignment.Fill
                            // Fill isn't honored on item roots (see main.qml).
                            preferredWidth: ListItem.view
                                            ? ListItem.view.rowWidth : 720
                            leftPadding: 14
                            rightPadding: 8
                            topPadding: 10
                            bottomPadding: 10
                            layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

                            Label {
                                text: "> " + ListItemData.prompt
                                multiline: true
                                textStyle.fontSize: FontSize.Small
                                textStyle.color: Color.create("#e8e8e8")
                                verticalAlignment: VerticalAlignment.Center
                                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                            }
                            // Visual affordance only - the tap is handled by the
                            // ListView's onTriggered above (see note there).
                            ImageView {
                                preferredWidth: ListItem.view
                                                ? ListItem.view.iconPx : 44
                                preferredHeight: ListItem.view
                                                 ? ListItem.view.iconPx : 44
                                minWidth: ListItem.view
                                          ? ListItem.view.iconPx : 44
                                minHeight: ListItem.view
                                           ? ListItem.view.iconPx : 44
                                leftMargin: 10
                                verticalAlignment: VerticalAlignment.Center
                                imageSource: "asset:///images/ic_close.png"
                            }
                        }
                    }
                ]
            }
        }
    }

    attachedObjects: [
        ArrayDataModel {
            id: queueModel
        }
    ]
}
