import bb.cascades 1.4

// Subscription usage (claude only): same buckets as Claude desktop
// "Your usage limits" - 5-hour + weekly rows - each as a labelled progress
// bar. The daemon fetches /api/oauth/usage and hands ready-to-render rows
// {title, percent, resets_text, severity}; no date math on the phone.
//
// Rebuild pattern (GrokRemote): C++ bumps api.usageRev, the page clears and
// re-appends the ArrayDataModel. Never use Connections{} / signal.connect().
Sheet {
    id: usageSheet

    property variant api          // pinned by main.qml (api: nav.api)

    peekEnabled: false

    function show() {
        if (usageSheet.api)
            usageSheet.api.fetchUsage();
        open();
    }

    Page {
        id: usagePage

        // Property pin: bumping api.usageRev rebuilds the list.
        property int uRev: usageSheet.api ? usageSheet.api.usageRev : 0
        onURevChanged: {
            if (! usageSheet.api)
                return;
            usageModel.clear();
            usageModel.append(usageSheet.api.usageBuckets);
        }

        titleBar: TitleBar {
            title: qsTr("Usage")
            dismissAction: ActionItem {
                title: qsTr("Done")
                onTriggered: usageSheet.close()
            }
            acceptAction: ActionItem {
                title: qsTr("Refresh")
                onTriggered: {
                    if (usageSheet.api)
                        usageSheet.api.fetchUsage();
                }
            }
        }

        Container {
            background: Color.Black
            horizontalAlignment: HorizontalAlignment.Fill
            verticalAlignment: VerticalAlignment.Fill
            layout: StackLayout {}

            // Loading / error / empty strip.
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 20
                rightPadding: 20
                topPadding: 12
                bottomPadding: 12
                background: Color.create("#161616")
                visible: usageSheet.api ? usageSheet.api.usageStatus != "" : false
                layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

                ActivityIndicator {
                    preferredWidth: 28
                    preferredHeight: 28
                    running: usageSheet.api
                             ? usageSheet.api.usageStatus == qsTr("Loading...")
                             : false
                    visible: running
                    verticalAlignment: VerticalAlignment.Center
                    rightMargin: 8
                }
                Label {
                    text: usageSheet.api ? usageSheet.api.usageStatus : ""
                    multiline: true
                    textStyle.fontSize: FontSize.XSmall
                    textStyle.color: Color.create("#c0a0a0")
                    verticalAlignment: VerticalAlignment.Center
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                }
            }

            ListView {
                id: usageList
                dataModel: usageModel
                horizontalAlignment: HorizontalAlignment.Fill
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                property int rowWidth: usageSheet.api
                                       ? usageSheet.api.screenWidth : 720

                listItemComponents: [
                    ListItemComponent {
                        type: ""
                        Container {
                            background: Color.Black
                            horizontalAlignment: HorizontalAlignment.Fill
                            preferredWidth: ListItem.view
                                            ? ListItem.view.rowWidth : 720
                            leftPadding: 20
                            rightPadding: 20
                            topPadding: 16
                            bottomPadding: 16

                            // Unified: which daemon this bucket belongs to,
                            // in its provider accent. Baked into the model
                            // row (C++); absent on single-provider builds.
                            // ListItemData-driven visible/text/color are the
                            // reliable channels (memory: resets_text below).
                            Label {
                                visible: ListItemData.source ? true : false
                                text: ListItemData.source
                                      ? "" + ListItemData.source : ""
                                multiline: false
                                bottomMargin: 2
                                textStyle.fontSize: FontSize.XSmall
                                textStyle.fontWeight: FontWeight.Bold
                                textStyle.color: Color.create(
                                    ListItemData.accent
                                    ? "" + ListItemData.accent : "#00A8DF")
                            }

                            // Bucket name, e.g. "Weekly · all models".
                            Label {
                                text: ListItemData.title
                                multiline: true
                                textStyle.fontSize: FontSize.Small
                                textStyle.color: Color.create("#e4e4e4")
                                textStyle.fontWeight: FontWeight.Bold
                            }

                            // Progress bar: filled portion + remainder (track
                            // shows through the transparent remainder). Filled
                            // color follows severity (green / amber / red).
                            Container {
                                horizontalAlignment: HorizontalAlignment.Fill
                                preferredHeight: 16
                                topMargin: 8
                                bottomMargin: 6
                                background: Color.create("#2a2a2a")
                                layout: StackLayout {
                                    orientation: LayoutOrientation.LeftToRight
                                }
                                Container {
                                    verticalAlignment: VerticalAlignment.Fill
                                    background: Color.create(
                                        ListItemData.severity == "critical"
                                        ? "#e0524f"
                                        : (ListItemData.severity == "warning"
                                           ? "#e0a020" : "#4a9d5b"))
                                    layoutProperties: StackLayoutProperties {
                                        spaceQuota: ListItemData.percent
                                    }
                                }
                                Container {
                                    verticalAlignment: VerticalAlignment.Fill
                                    layoutProperties: StackLayoutProperties {
                                        spaceQuota: 100 - ListItemData.percent
                                    }
                                }
                            }

                            // Desktop layout: "Resets in ..." on the left, "99%"
                            // on the right (no "used" suffix).
                            Container {
                                horizontalAlignment: HorizontalAlignment.Fill
                                layout: StackLayout {
                                    orientation: LayoutOrientation.LeftToRight
                                }
                                Label {
                                    text: ListItemData.resets_text
                                    visible: ListItemData.resets_text != ""
                                    textStyle.fontSize: FontSize.XSmall
                                    textStyle.color: Color.create("#8a8a8a")
                                    layoutProperties: StackLayoutProperties {
                                        spaceQuota: 1
                                    }
                                }
                                Label {
                                    text: ListItemData.percent + "%"
                                    textStyle.fontSize: FontSize.XSmall
                                    textStyle.color: Color.create("#b0b0b0")
                                    textStyle.textAlign: TextAlign.Right
                                    verticalAlignment: VerticalAlignment.Center
                                }
                            }
                        }
                    }
                ]
            }
        }

        attachedObjects: [
            ArrayDataModel {
                id: usageModel
            }
        ]
    }
}
