import bb.cascades 1.4
import bb.cascades.pickers 1.0
import bb.system 1.2

// Transcript of the session ApiClient currently has open.
//
// Created by ApiClient::createTranscriptPage(); the pusher pins the client
// on `api` (and the NavigationPane on `nav`) right after creation. Never
// reference bare `_api` here: context lookups break on BB10 Cascades once
// pages are pushed/popped, and Connections{} / signal .connect() are just
// as unreliable - state comes in exclusively through pinned-property
// bindings (GrokRemote pattern, including the visual design).
//
// Rendering: rich blocks arrive pre-rasterized as PNGs ("paintimg" items,
// RichPaint/stb_truetype - the paint pipeline GrokRemote proved on device).
// A block that failed to paint arrives as its original kind and renders as
// a dual Html/Plain Label row (Cascades Html labels must not set
// textStyle.color: theme white overrides <font color>).
Page {
    id: transcriptPage
    actionBarVisibility: ChromeVisibility.Hidden

    property variant api
    property variant nav
    property string sessionTitle: ""
    property bool ready: false

    // Property pin: bumping api.messageRev triggers onLiveRevChanged.
    property int liveRev: transcriptPage.api ? transcriptPage.api.messageRev : 0
    onLiveRevChanged: {
        if (ready && transcriptPage.api)
            rebuild();
    }

    // Attachment uploaded (the "+" button): reference it in the prompt -
    // but only for uploads that finish while THIS page is open. The first
    // change is the api pin arriving (default -> current rev); sync to it
    // without prefilling, or every page would replay the last upload.
    property int attachPin: transcriptPage.api ? transcriptPage.api.attachRev : -1
    property int seenAttachRev: -1
    onAttachPinChanged: {
        if (seenAttachRev < 0 || ! transcriptPage.api) {
            seenAttachRev = attachPin;
            return;
        }
        if (attachPin == seenAttachRev)
            return;
        seenAttachRev = attachPin;
        var path = transcriptPage.api.lastAttachmentPath;
        if (path == "")
            return;
        var sep = promptField.text.length > 0 ? "\n" : "";
        promptField.text = promptField.text + sep + "[attached: " + path + "]";
    }

    // Rewind staged (long-press -> "Rewind to here" passed the C++ guards):
    // destructive, so confirm again with the real consequence spelled out.
    // Same first-change sync as attachPin (the api pin arriving).
    property int rewindPin: transcriptPage.api
                            ? transcriptPage.api.rewindConfirmRev : -1
    property int seenRewindRev: -1
    onRewindPinChanged: {
        if (seenRewindRev < 0 || ! transcriptPage.api) {
            seenRewindRev = rewindPin;
            return;
        }
        if (rewindPin == seenRewindRev)
            return;
        seenRewindRev = rewindPin;
        rewindConfirm.body = transcriptPage.api.rewindConfirmText;
        rewindConfirm.show();
    }

    // AskUserQuestion arrived (interactive mode): the agent's turn is blocked
    // in the host TUI until we pick, so open the sheet as soon as the daemon
    // publishes the questions.
    property bool askPin: transcriptPage.api
                          ? transcriptPage.api.questionPending : false
    onAskPinChanged: {
        if (! ready || ! transcriptPage.api)
            return;
        if (askPin && ! questionSheet.showing)
            questionSheet.show();
    }

    // Process-view step expanded/collapsed: apply the ONE row edit in place.
    // A full rebuild() clears the model, which snaps the ListView back to
    // the top of the transcript — the whole reason this pin exists. The C++
    // index counts m_messages only; the model has an extra "older" row on
    // top whenever canLoadOlder.
    property int stepEditPin: transcriptPage.api
                              ? transcriptPage.api.stepEditRev : -1
    property int seenStepEditRev: -1
    onStepEditPinChanged: {
        if (seenStepEditRev < 0 || ! transcriptPage.api) {
            seenStepEditRev = stepEditPin;
            return;
        }
        if (stepEditPin == seenStepEditRev)
            return;
        seenStepEditRev = stepEditPin;
        var a = transcriptPage.api;
        var idx = a.stepEditIndex + (a.canLoadOlder ? 1 : 0);
        if (idx < 0 || idx > messagesModel.size())
            return;
        var act = "" + a.stepEditAction;
        if (act == "insert")
            messagesModel.insert(idx, a.stepEditItem);
        else if (act == "remove" && idx < messagesModel.size())
            messagesModel.removeAt(idx);
        else if (act == "replace" && idx < messagesModel.size())
            messagesModel.replace(idx, a.stepEditItem);
    }

    // The pusher sets `api` after creation completes.
    onApiChanged: {
        if (ready && transcriptPage.api)
            rebuild();
    }

    function rebuild() {
        var a = transcriptPage.api;
        if (! a)
            return;
        messagesModel.clear();
        if (a.canLoadOlder)
            messagesModel.append({ kind: "older", text: "", rich: "", live: false });
        messagesModel.append(a.messages);
        if (a.scrollToEndHint)
            messagesList.scrollToPosition(ScrollPosition.End, ScrollAnimation.None);
    }

    function sendNow() {
        var a = transcriptPage.api;
        if (! a)
            return;
        // While a job runs the prompt queues (C++ decides); otherwise a
        // session must be open or the text would be silently lost.
        if (! a.jobRunning && a.currentSessionId == "")
            return;
        var prompt = promptField.text.trim();
        if (prompt == "")
            return;
        a.sendPrompt(prompt);
        promptField.text = "";
    }

    onCreationCompleted: {
        ready = true;
        if (transcriptPage.api)
            rebuild();
    }

    // FreeForm: the default TitleBar font truncates long session titles
    // after ~30 chars; a small two-line label shows the whole thing.
    titleBar: TitleBar {
        kind: TitleBarKind.FreeForm
        kindProperties: FreeFormTitleBarKindProperties {
            Container {
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 16
                rightPadding: 4
                topPadding: 6
                bottomPadding: 6
                layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

                Label {
                    text: sessionTitle == "" ? qsTr("Session") : sessionTitle
                    multiline: true
                    autoSize.maxLineCount: 2
                    verticalAlignment: VerticalAlignment.Center
                    textStyle.fontSize: FontSize.Small
                    textStyle.fontWeight: FontWeight.Bold
                    textStyle.color: Color.White
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                }
                ImageButton {
                    preferredWidth: 44
                    preferredHeight: 44
                    minWidth: 44
                    minHeight: 44
                    verticalAlignment: VerticalAlignment.Center
                    defaultImageSource: "asset:///images/ic_close.png"
                    pressedImageSource: "asset:///images/ic_close.png"
                    onClicked: {
                        if (transcriptPage.nav)
                            transcriptPage.nav.pop();
                    }
                }
            }
        }
    }

    Container {
        background: Color.create("#121212")
        horizontalAlignment: HorizontalAlignment.Fill
        verticalAlignment: VerticalAlignment.Fill
        layout: StackLayout {}

        // Top banner: daemon-pushed live status (WebSocket) when available,
        // the local poll ticker as fallback, plain messages otherwise.
        // Shows jobs this client didn't start too (e.g. after a restart).
        Container {
            id: statusBanner
            property bool active: transcriptPage.api
                                  ? (transcriptPage.api.jobRunning
                                     || transcriptPage.api.liveStatusLine != "")
                                  : false
            horizontalAlignment: HorizontalAlignment.Fill
            leftPadding: 10
            rightPadding: 10
            topPadding: 6
            bottomPadding: 6
            background: statusBanner.active
                        ? Color.create(transcriptPage.api
                                       ? transcriptPage.api.themeBannerBg : "#2a1f1a")
                        : Color.create("#161616")
            visible: transcriptPage.api
                     ? (statusBanner.active
                        || transcriptPage.api.transcriptStatus != "")
                     : false
            layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

            ActivityIndicator {
                preferredWidth: 28
                preferredHeight: 28
                running: statusBanner.active
                visible: running
                verticalAlignment: VerticalAlignment.Center
                rightMargin: 8
            }
            Label {
                text: {
                    var a = transcriptPage.api;
                    if (! a)
                        return "";
                    var live = a.liveStatusLine;
                    if (a.jobRunning) {
                        // Streamed status beats the poll ticker (fresher,
                        // includes tool + elapsed straight from the daemon).
                        var line = live != "" ? live
                                 : (a.jobTicker != "" ? a.jobTicker
                                                      : qsTr("%1 is working...").arg(a.agentName));
                        if (a.queuedCount > 0 && a.queueSupported)
                            line += "  ·  " + qsTr("%1 queued").arg(a.queuedCount);
                        return line;
                    }
                    if (live != "")
                        return live; // job running on the daemon, not started here
                    return a.transcriptStatus;
                }
                // Cap wraps: daemon already collapses detail to one short
                // line (claude tool_detail / job.set_phase), and C++ shortens
                // further - but a long single line still wraps on 720px.
                // Two lines matches the session title bar above.
                multiline: true
                autoSize.maxLineCount: 2
                textStyle.fontSize: FontSize.XSmall
                textStyle.color: statusBanner.active
                                 ? Color.create(transcriptPage.api
                                                ? transcriptPage.api.themeBannerText
                                                : "#e8a088")
                                 : Color.create("#9a9a9a")
                verticalAlignment: VerticalAlignment.Center
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
            }
            ImageButton {
                preferredWidth: 44
                preferredHeight: 44
                minWidth: 44
                minHeight: 44
                maxWidth: 44
                maxHeight: 44
                verticalAlignment: VerticalAlignment.Center
                visible: transcriptPage.api ? transcriptPage.api.jobRunning : false
                defaultImageSource: "asset:///images/ic_stop.png"
                pressedImageSource: "asset:///images/ic_stop.png"
                onClicked: {
                    if (transcriptPage.api)
                        transcriptPage.api.stopJob();
                }
            }
            ImageButton {
                preferredWidth: 44
                preferredHeight: 44
                minWidth: 44
                minHeight: 44
                maxWidth: 44
                maxHeight: 44
                verticalAlignment: VerticalAlignment.Center
                visible: transcriptPage.api
                         ? (! transcriptPage.api.jobRunning
                            && transcriptPage.api.currentSessionId != "")
                         : false
                defaultImageSource: "asset:///images/ic_reload.png"
                pressedImageSource: "asset:///images/ic_reload.png"
                onClicked: {
                    if (transcriptPage.api)
                        transcriptPage.api.refreshTranscript();
                }
            }
        }

        ListView {
            id: messagesList
            dataModel: messagesModel
            horizontalAlignment: HorizontalAlignment.Fill
            layoutProperties: StackLayoutProperties { spaceQuota: 1 }
            // Breathing room so the last row isn't visually clipped when the
            // list auto-scrolls to the end after a new message.
            bottomPadding: 10

            // Called from the "older" list item (ListItem.view bridge -
            // list item components can't see the page's `api` pin).
            function loadOlderNow() {
                if (transcriptPage.api)
                    transcriptPage.api.loadOlder();
            }

            // Brand theme bridged to the item components (they can't see
            // the page's `api` pin - GrokRemote lesson).
            property string userWell: transcriptPage.api
                                      ? transcriptPage.api.themeUserWell : "#2a2a2a"
            property string liveWell: transcriptPage.api
                                      ? transcriptPage.api.themeLiveWell : "#20180f"
            property string accent: transcriptPage.api
                                    ? transcriptPage.api.accentColor : "#00A8DF"
            // Rich-text accents (Label fallbacks / plain meta rows). Painted
            // blocks already carry these baked in via C++ remapAccent().
            property string heading: transcriptPage.api
                                     ? transcriptPage.api.themeHeading : "#c678dd"
            property string metaThought: transcriptPage.api
                                         ? transcriptPage.api.themeMetaThought : "#9a8fb0"

            // Measured list width, bridged to item components the same way.
            // Rows here are content-sized, so a row whose only content has no
            // intrinsic width (the "hr" rule) would collapse; it binds to this
            // instead of a literal so it survives rotation/other screen sizes.
            // Prefer live layout width; fall back to DisplayInfo (Classic 720 /
            // Passport 1440) before the first layout pass.
            property real rowWidth: transcriptPage.api
                                    ? transcriptPage.api.screenWidth : 720
            attachedObjects: [
                LayoutUpdateHandler {
                    onLayoutFrameChanged: {
                        if (layoutFrame.width > 0)
                            messagesList.rowWidth = layoutFrame.width
                    }
                }
            ]

            // Tapping the "Load older messages" row pages in more history
            // (a Button inside the item can't receive the tap on BB10).
            // A process-view step row expands/collapses the same way.
            onTriggered: {
                var row = indexPath && indexPath.length > 0 ? indexPath[0] : -1;
                if (row < 0)
                    return;
                var item = messagesModel.data([ row ]);
                if (! item)
                    return;
                var k = "" + item.kind;
                if (k == "older")
                    messagesList.loadOlderNow();
                else if (k == "step" && item.api)
                    item.api.toggleStep("" + item.stepRef);
            }

            // Typed rows (GrokRemote pattern): route each display item by
            // kind instead of stacking hidden Containers per row.
            function itemType(data, indexPath) {
                if (! data || ! data.kind)
                    return "p";
                var k = "" + data.kind;
                if (k == "older" || k == "gap" || k == "hr" || k == "user"
                        || k == "meta" || k == "h" || k == "li" || k == "code"
                        || k == "paintimg" || k == "th" || k == "tr"
                        || k == "step" || k == "stepbody")
                    return k;
                return "p";
            }

            listItemComponents: [
                ListItemComponent {
                    type: "older"
                    // A Button inside a ListItemComponent never gets the tap
                    // (the ListView consumes it for row selection), so this
                    // is a plain centered row handled by messagesList's
                    // onTriggered instead.
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        preferredWidth: ListItem.view
                                        ? ListItem.view.rowWidth : 720
                        background: Color.create("#121212")
                        topPadding: 12
                        bottomPadding: 12
                        layout: DockLayout {}
                        Label {
                            horizontalAlignment: HorizontalAlignment.Center
                            text: qsTr("↑  Load older messages")
                            textStyle.fontSize: FontSize.Small
                            textStyle.color: Color.create(ListItem.view.accent)
                        }
                    }
                },
                ListItemComponent {
                    type: "gap"
                    Container {
                        background: Color.create("#121212")
                        preferredHeight: 10
                    }
                },
                // Process view: one working step (▸ tool, ↳ result, ✻ thinking).
                // Tap expands (handled by messagesList.onTriggered — a control
                // inside the item never receives the tap on BB10).
                ListItemComponent {
                    type: "step"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        preferredWidth: ListItem.view
                                        ? ListItem.view.rowWidth : 720
                        background: Color.create("#121212")
                        leftPadding: 20
                        rightPadding: 8
                        topPadding: 2
                        bottomPadding: 2
                        Label {
                            text: ListItemData.text
                            textFormat: TextFormat.Plain
                            textStyle.fontSize: FontSize.XSmall
                            textStyle.color: ListItemData.stepErr
                                             ? Color.create("#e0806c")
                                             : Color.create("#8a92a4")
                        }
                    }
                },
                // The expanded body under a step: preview first, the full
                // text swapped in once /steps/<ref> answers.
                ListItemComponent {
                    type: "stepbody"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        preferredWidth: ListItem.view
                                        ? ListItem.view.rowWidth : 720
                        background: Color.create("#121212")
                        leftPadding: 32
                        rightPadding: 12
                        contextActions: [
                            ActionSet {
                                title: qsTr("Step")
                                ActionItem {
                                    title: qsTr("Copy")
                                    imageSource: "asset:///images/ic_copy.png"
                                    onTriggered: {
                                        var a = ListItemData.api;
                                        if (a)
                                            a.copyToClipboard("" + ListItemData.text);
                                    }
                                }
                            }
                        ]
                        Container {
                            horizontalAlignment: HorizontalAlignment.Fill
                            background: Color.create("#1a1a1a")
                            leftPadding: 10
                            rightPadding: 10
                            topPadding: 6
                            bottomPadding: 6
                            topMargin: 2
                            bottomMargin: 4
                            Label {
                                text: ListItemData.text
                                textFormat: TextFormat.Plain
                                multiline: true
                                textStyle.fontSize: FontSize.XSmall
                                textStyle.color: Color.create("#9aa4b2")
                            }
                        }
                    }
                },
                ListItemComponent {
                    type: "hr"
                    Container {
                        id: hrRow
                        horizontalAlignment: HorizontalAlignment.Fill
                        // The rule has no intrinsic width, so Fill alone leaves
                        // the row at leftPadding+rightPadding (24px stub).
                        preferredWidth: ListItem.view ? ListItem.view.rowWidth : 0
                        background: Color.create("#121212")
                        leftPadding: 12
                        rightPadding: 12
                        topPadding: 8
                        bottomPadding: 8
                        Container {
                            horizontalAlignment: HorizontalAlignment.Fill
                            preferredWidth: Math.max(0, hrRow.preferredWidth - 24)
                            background: Color.create("#4a4a5a")
                            preferredHeight: 2
                        }
                    }
                },
                // User prompt - Grok/Claude chat bar: left ">" + body on a
                // full-width dark strip. No timestamp (the desktop TUI shows
                // one; we deliberately omit it on the phone).
                ListItemComponent {
                    type: "user"
                    Container {
                        id: userRow
                        horizontalAlignment: HorizontalAlignment.Fill
                        // Full device width (Classic 720 / Passport 1440) -
                        // hardcoding 720 left half the Passport screen empty.
                        preferredWidth: ListItem.view
                                        ? ListItem.view.rowWidth : 720
                        background: Color.create("#121212")
                        leftPadding: 8
                        rightPadding: 8
                        topPadding: 4
                        bottomPadding: 4
                        contextActions: [
                            ActionSet {
                                title: qsTr("Message")
                                ActionItem {
                                    title: qsTr("Copy")
                                    imageSource: "asset:///images/ic_copy.png"
                                    enabled: ListItemData.text != ""
                                    onTriggered: {
                                        // Copy the body only (chevron is chrome).
                                        var t = ListItemData.text || "";
                                        if (t.indexOf("> ") == 0)
                                            t = t.substring(2);
                                        // ListItem.view and the _api context
                                        // property are BOTH null inside a
                                        // contextActions ActionItem; the only
                                        // channel that resolves is ListItemData
                                        // (the row map), so the C++ client is
                                        // baked into every row as .api.
                                        var a = ListItemData.api;
                                        if (a && t != "")
                                            a.copyToClipboard(t);
                                    }
                                }
                                ActionItem {
                                    title: qsTr("Rewind to here")
                                    imageSource: "asset:///images/ic_reload.png"
                                    // The daemon (>= 2.5) rewinds the session
                                    // journal itself, any harness, any exec
                                    // mode. ActionItem has no `visible` in
                                    // Cascades, so an old daemon shows it
                                    // greyed instead of failing after the tap.
                                    enabled: ListItemData.api
                                             ? ListItemData.api.canRewindHere
                                             : false
                                    onTriggered: {
                                        // Stages the rewind: C++ guards,
                                        // then the rewindPin raises the
                                        // confirmation dialog — nothing
                                        // destructive happens on the tap.
                                        var a = ListItemData.api;
                                        if (a)
                                            a.rewindToRow(ListItemData.rowId | 0);
                                    }
                                }
                            }
                        ]
                        // Chat-bar style (matches the desktop reference):
                        // dark well, accent ">" chevron, compact monospace
                        // body in muted gray. No timestamp (deliberately).
                        Container {
                            horizontalAlignment: HorizontalAlignment.Fill
                            preferredWidth: Math.max(0, userRow.preferredWidth - 16)
                            // Literal, NOT ListItem.view.userWell: a structural
                            // `background:` paint bound through ListItem.view
                            // evaluates before the view attaches and stays
                            // transparent (the well vanished). #2a2a2a is the
                            // brand BRAND_USER_WELL for both variants.
                            background: Color.create("#2a2a2a")
                            leftPadding: 12
                            rightPadding: 14
                            topPadding: 9
                            bottomPadding: 9
                            layout: StackLayout {
                                orientation: LayoutOrientation.LeftToRight
                            }

                            // Chevron - brand accent, kept as-is.
                            Label {
                                text: ">"
                                textStyle.fontSize: FontSize.Small
                                textStyle.fontWeight: FontWeight.Bold
                                textStyle.color: Color.create(ListItem.view.accent)
                                verticalAlignment: VerticalAlignment.Top
                                rightMargin: 10
                            }
                            Label {
                                // C++ now stores plain body text; still strip
                                // a legacy "> " so older live rows look right.
                                text: {
                                    var t = ListItemData.text || "";
                                    if (t.indexOf("> ") == 0)
                                        return t.substring(2);
                                    return t;
                                }
                                multiline: true
                                // Compact monospace on the muted-gray well -
                                // the terminal look from the reference. Falls
                                // back to the default face if the device
                                // lacks this family (size still applies).
                                textStyle.fontFamily: "Andale Mono WT"
                                textStyle.fontSize: FontSize.XSmall
                                textStyle.color: Color.create("#e4e4e4")
                                verticalAlignment: VerticalAlignment.Top
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: 1
                                }
                            }
                        }
                    }
                },
                // Thought for / Worked for timing rows (grok TUI parity).
                ListItemComponent {
                    type: "meta"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        background: Color.create("#121212")
                        leftPadding: 12
                        rightPadding: 12
                        topPadding: 3
                        bottomPadding: 3
                        Label {
                            text: ListItemData.text
                            textFormat: TextFormat.Plain
                            multiline: false
                            textStyle.color: ListItemData.metaKind == "thought"
                                             ? Color.create(ListItem.view.metaThought)
                                             : Color.create("#6b7280")
                            textStyle.fontSize: FontSize.XXSmall
                            textStyle.fontStyle: FontStyle.Italic
                        }
                    }
                },
                // Paint row: rich block pre-rendered to PNG by RichPaint
                // (stb_truetype). Plain ImageView - the safe half of the
                // paint approach (RichProbe: PNG+ImageView never crashed).
                ListItemComponent {
                    type: "paintimg"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        background: ListItemData.codeBg
                                    ? Color.create("#1a1a1a")
                                    : Color.create("#121212")
                        leftPadding: 12
                        rightPadding: 12
                        topPadding: 2
                        bottomPadding: 2
                        contextActions: [
                            ActionSet {
                                title: qsTr("Message")
                                ActionItem {
                                    title: qsTr("Copy")
                                    imageSource: "asset:///images/ic_copy.png"
                                    enabled: true
                                    onTriggered: {
                                        // Only ListItemData resolves in a
                                        // contextActions ActionItem; the client
                                        // is baked into each row as .api.
                                        var a = ListItemData.api;
                                        if (a)
                                            a.copyToClipboard("" + ListItemData.text);
                                    }
                                }
                            }
                        ]
                        Label {
                            visible: ListItemData.lang && ("" + ListItemData.lang).length > 0
                            text: ListItemData.lang ? ListItemData.lang : ""
                            textFormat: TextFormat.Plain
                            textStyle.color: Color.create("#7ec87e")
                            textStyle.fontSize: FontSize.XXSmall
                            bottomMargin: 2
                        }
                        ImageView {
                            imageSource: ListItemData.imgPath
                            // Prefer painted size; on Passport fall back to
                            // full-row width (not the Classic 692 default).
                            preferredWidth: ListItemData.imgW > 0
                                            ? ListItemData.imgW
                                            : (ListItem.view
                                               ? Math.max(120, ListItem.view.rowWidth - 28)
                                               : 692)
                            preferredHeight: ListItemData.imgH > 0
                                             ? ListItemData.imgH : 40
                            scalingMethod: ScalingMethod.AspectFit
                            loadEffect: ImageViewLoadEffect.None
                        }
                    }
                },
                // Label fallbacks below: only used when RichPaint failed
                // for a block (or for plain-text-only content).
                ListItemComponent {
                    type: "h"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        background: Color.create("#121212")
                        leftPadding: 12
                        rightPadding: 12
                        topMargin: 8
                        contextActions: [
                            ActionSet {
                                title: qsTr("Message")
                                ActionItem {
                                    title: qsTr("Copy")
                                    imageSource: "asset:///images/ic_copy.png"
                                    enabled: true
                                    onTriggered: {
                                        var a = ListItemData.api;
                                        if (a)
                                            a.copyToClipboard("" + ListItemData.text);
                                    }
                                }
                            }
                        ]
                        Label {
                            visible: ListItemData.rich != ""
                            text: ListItemData.rich
                            textFormat: TextFormat.Html
                            multiline: true
                            textStyle.fontSize: FontSize.Medium
                        }
                        Label {
                            visible: ListItemData.rich == ""
                            text: ListItemData.text
                            multiline: true
                            textStyle.fontSize: FontSize.Medium
                            textStyle.color: Color.create(ListItem.view.heading)
                            textStyle.fontWeight: FontWeight.Bold
                        }
                    }
                },
                ListItemComponent {
                    type: "p"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        background: ListItemData.live
                                    ? Color.create(ListItemData.liveWell)
                                    : Color.create("#121212")
                        leftPadding: 12
                        rightPadding: 12
                        topMargin: 8
                        contextActions: [
                            ActionSet {
                                title: qsTr("Message")
                                ActionItem {
                                    title: qsTr("Copy")
                                    imageSource: "asset:///images/ic_copy.png"
                                    enabled: true
                                    onTriggered: {
                                        var a = ListItemData.api;
                                        if (a)
                                            a.copyToClipboard("" + ListItemData.text);
                                    }
                                }
                            }
                        ]
                        Label {
                            visible: ListItemData.rich != ""
                            text: ListItemData.rich
                            textFormat: TextFormat.Html
                            multiline: true
                            textStyle.fontSize: FontSize.Small
                        }
                        Label {
                            visible: ListItemData.rich == ""
                            text: ListItemData.text
                            multiline: true
                            textStyle.fontSize: FontSize.Small
                            textStyle.color: Color.create("#d0d0d0")
                        }
                    }
                },
                // Bullet / numbered list item fallback: the prefix is its
                // own column so wrapped lines keep a hanging indent.
                ListItemComponent {
                    type: "li"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        background: ListItemData.live
                                    ? Color.create(ListItemData.liveWell)
                                    : Color.create("#121212")
                        leftPadding: 12
                        rightPadding: 12
                        topMargin: 4
                        contextActions: [
                            ActionSet {
                                title: qsTr("Message")
                                ActionItem {
                                    title: qsTr("Copy")
                                    imageSource: "asset:///images/ic_copy.png"
                                    enabled: true
                                    onTriggered: {
                                        var a = ListItemData.api;
                                        if (a)
                                            a.copyToClipboard("" + ListItemData.text);
                                    }
                                }
                            }
                        ]
                        Container {
                            horizontalAlignment: HorizontalAlignment.Fill
                            layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                            Label {
                                text: ListItemData.prefix ? ListItemData.prefix : "*"
                                multiline: false
                                minWidth: 36
                                textStyle.fontSize: FontSize.Small
                                textStyle.color: Color.create("#9a9a9a")
                                verticalAlignment: VerticalAlignment.Top
                            }
                            Container {
                                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                                Label {
                                    visible: ListItemData.rich != ""
                                    text: ListItemData.rich
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.Small
                                }
                                Label {
                                    visible: ListItemData.rich == ""
                                    text: ListItemData.text
                                    multiline: true
                                    textStyle.fontSize: FontSize.Small
                                    textStyle.color: Color.create("#d0d0d0")
                                }
                            }
                        }
                    }
                },
                // Code block fallback (dark well, syntax colors from daemon)
                ListItemComponent {
                    type: "code"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        background: Color.create("#121212")
                        leftPadding: 12
                        rightPadding: 12
                        contextActions: [
                            ActionSet {
                                title: qsTr("Message")
                                ActionItem {
                                    title: qsTr("Copy")
                                    imageSource: "asset:///images/ic_copy.png"
                                    enabled: true
                                    onTriggered: {
                                        var a = ListItemData.api;
                                        if (a)
                                            a.copyToClipboard("" + ListItemData.text);
                                    }
                                }
                            }
                        ]
                        Container {
                            horizontalAlignment: HorizontalAlignment.Fill
                            background: Color.create("#1a1a1a")
                            leftPadding: 10
                            rightPadding: 10
                            topPadding: 6
                            bottomPadding: 6
                            topMargin: 4
                            bottomMargin: 4
                            Label {
                                visible: ListItemData.lang && ("" + ListItemData.lang).length > 0
                                text: ListItemData.lang ? ListItemData.lang : ""
                                textFormat: TextFormat.Plain
                                textStyle.color: Color.create("#7ec87e")
                                textStyle.fontSize: FontSize.XXSmall
                                bottomMargin: 2
                            }
                            Label {
                                visible: ListItemData.rich != ""
                                text: ListItemData.rich
                                textFormat: TextFormat.Html
                                multiline: true
                                textStyle.fontSize: FontSize.XSmall
                            }
                            Label {
                                visible: ListItemData.rich == ""
                                text: ListItemData.text
                                multiline: true
                                textStyle.fontSize: FontSize.XSmall
                                textStyle.color: Color.create("#7ec87e")
                            }
                        }
                    }
                },
                // Table header row - real columns c0..c5 (GrokRemote).
                ListItemComponent {
                    type: "th"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        background: Color.create("#1a1a28")
                        leftPadding: 12
                        rightPadding: 12
                        topPadding: 6
                        bottomPadding: 2
                        Container {
                            horizontalAlignment: HorizontalAlignment.Fill
                            layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                            Container {
                                visible: ListItemData.hasC0 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w0 > 0 ? ListItemData.w0 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c0r && ("" + ListItemData.c0r).length
                                          ? ListItemData.c0r
                                          : (ListItemData.c0 ? ListItemData.c0 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                            }
                            Container {
                                visible: ListItemData.hasC1 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w1 > 0 ? ListItemData.w1 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c1r && ("" + ListItemData.c1r).length
                                          ? ListItemData.c1r
                                          : (ListItemData.c1 ? ListItemData.c1 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                            }
                            Container {
                                visible: ListItemData.hasC2 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w2 > 0 ? ListItemData.w2 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c2r && ("" + ListItemData.c2r).length
                                          ? ListItemData.c2r
                                          : (ListItemData.c2 ? ListItemData.c2 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                            }
                            Container {
                                visible: ListItemData.hasC3 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w3 > 0 ? ListItemData.w3 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c3r && ("" + ListItemData.c3r).length
                                          ? ListItemData.c3r
                                          : (ListItemData.c3 ? ListItemData.c3 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                            }
                            Container {
                                visible: ListItemData.hasC4 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w4 > 0 ? ListItemData.w4 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c4r && ("" + ListItemData.c4r).length
                                          ? ListItemData.c4r
                                          : (ListItemData.c4 ? ListItemData.c4 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                            }
                            Container {
                                visible: ListItemData.hasC5 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w5 > 0 ? ListItemData.w5 : 1
                                }
                                Label {
                                    text: ListItemData.c5r && ("" + ListItemData.c5r).length
                                          ? ListItemData.c5r
                                          : (ListItemData.c5 ? ListItemData.c5 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                    textStyle.fontWeight: FontWeight.Bold
                                }
                            }
                        }
                    }
                },
                // Table body row - same columns, regular weight.
                ListItemComponent {
                    type: "tr"
                    Container {
                        horizontalAlignment: HorizontalAlignment.Fill
                        background: Color.create("#161620")
                        leftPadding: 12
                        rightPadding: 12
                        topPadding: 2
                        bottomPadding: 2
                        contextActions: [
                            ActionSet {
                                title: qsTr("Row")
                                ActionItem {
                                    title: qsTr("Copy")
                                    imageSource: "asset:///images/ic_copy.png"
                                    enabled: true
                                    onTriggered: {
                                        var a = ListItemData.api;
                                        if (a)
                                            a.copyToClipboard("" + ListItemData.text);
                                    }
                                }
                            }
                        ]
                        Container {
                            horizontalAlignment: HorizontalAlignment.Fill
                            layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                            Container {
                                visible: ListItemData.hasC0 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w0 > 0 ? ListItemData.w0 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c0r && ("" + ListItemData.c0r).length
                                          ? ListItemData.c0r
                                          : (ListItemData.c0 ? ListItemData.c0 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                }
                            }
                            Container {
                                visible: ListItemData.hasC1 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w1 > 0 ? ListItemData.w1 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c1r && ("" + ListItemData.c1r).length
                                          ? ListItemData.c1r
                                          : (ListItemData.c1 ? ListItemData.c1 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                }
                            }
                            Container {
                                visible: ListItemData.hasC2 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w2 > 0 ? ListItemData.w2 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c2r && ("" + ListItemData.c2r).length
                                          ? ListItemData.c2r
                                          : (ListItemData.c2 ? ListItemData.c2 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                }
                            }
                            Container {
                                visible: ListItemData.hasC3 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w3 > 0 ? ListItemData.w3 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c3r && ("" + ListItemData.c3r).length
                                          ? ListItemData.c3r
                                          : (ListItemData.c3 ? ListItemData.c3 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                }
                            }
                            Container {
                                visible: ListItemData.hasC4 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w4 > 0 ? ListItemData.w4 : 1
                                }
                                rightPadding: 4
                                Label {
                                    text: ListItemData.c4r && ("" + ListItemData.c4r).length
                                          ? ListItemData.c4r
                                          : (ListItemData.c4 ? ListItemData.c4 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                }
                            }
                            Container {
                                visible: ListItemData.hasC5 == 1
                                layoutProperties: StackLayoutProperties {
                                    spaceQuota: ListItemData.w5 > 0 ? ListItemData.w5 : 1
                                }
                                Label {
                                    text: ListItemData.c5r && ("" + ListItemData.c5r).length
                                          ? ListItemData.c5r
                                          : (ListItemData.c5 ? ListItemData.c5 : " ")
                                    textFormat: TextFormat.Html
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                }
                            }
                        }
                    }
                }
            ]
        }

        // Permission request panel (claude, non-auto modes): a tool wants
        // to run and is waiting for the user to Allow / Deny. Grok daemons
        // never raise these (capability-gated server-side).
        Container {
            visible: transcriptPage.api ? transcriptPage.api.permissionPending : false
            horizontalAlignment: HorizontalAlignment.Fill
            background: Color.create("#2a2410")
            leftPadding: 12
            rightPadding: 12
            topPadding: 10
            bottomPadding: 10

            Label {
                text: qsTr("Permission needed")
                textStyle.fontSize: FontSize.XSmall
                textStyle.color: Color.create("#e8c060")
                textStyle.fontWeight: FontWeight.Bold
            }
            Label {
                text: transcriptPage.api ? transcriptPage.api.permissionTool : ""
                multiline: true
                textStyle.fontSize: FontSize.Small
                textStyle.color: Color.create("#e8e8e8")
                textStyle.fontWeight: FontWeight.Bold
                topMargin: 2
            }
            Label {
                text: transcriptPage.api ? transcriptPage.api.permissionDetail : ""
                visible: transcriptPage.api
                         ? transcriptPage.api.permissionDetail != "" : false
                multiline: true
                textStyle.fontSize: FontSize.XSmall
                textStyle.color: Color.create("#9a9a9a")
                topMargin: 2
            }
            Container {
                layout: StackLayout { orientation: LayoutOrientation.LeftToRight }
                topMargin: 8
                horizontalAlignment: HorizontalAlignment.Fill

                Button {
                    text: qsTr("Deny")
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    onClicked: {
                        if (transcriptPage.api)
                            transcriptPage.api.resolvePermission(false);
                    }
                }
                Button {
                    text: qsTr("Allow")
                    layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                    leftMargin: 8
                    onClicked: {
                        if (transcriptPage.api)
                            transcriptPage.api.resolvePermission(true);
                    }
                }
            }
        }

        // AskUserQuestion banner: the sheet opens by itself when the ask
        // arrives, but closing it (or opening this page later) leaves the
        // turn still blocked - this reopens it.
        Container {
            visible: transcriptPage.api ? transcriptPage.api.questionPending : false
            horizontalAlignment: HorizontalAlignment.Fill
            background: Color.create("#12251b")
            leftPadding: 12
            rightPadding: 12
            topPadding: 10
            bottomPadding: 10

            Label {
                text: qsTr("A question is waiting for your answer")
                textStyle.fontSize: FontSize.XSmall
                textStyle.color: Color.create("#6fdc6f")
                textStyle.fontWeight: FontWeight.Bold
            }
            Button {
                text: qsTr("Answer")
                horizontalAlignment: HorizontalAlignment.Fill
                topMargin: 8
                onClicked: questionSheet.show()
            }
        }

        // Compose strip (GrokRemote: near-black well, Enter sends).
        // "+" attaches a file. Slash commands are typed directly; the
        // client validates them against the daemon's list before sending.
        Container {
            background: Color.create("#0e0e0e")
            horizontalAlignment: HorizontalAlignment.Fill
            leftPadding: 8
            rightPadding: 8
            topPadding: 4
            bottomPadding: 4
            layout: StackLayout { orientation: LayoutOrientation.LeftToRight }

            ImageButton {
                preferredWidth: 44
                preferredHeight: 44
                minWidth: 44
                minHeight: 44
                maxWidth: 44
                maxHeight: 44
                verticalAlignment: VerticalAlignment.Center
                rightMargin: 6
                defaultImageSource: "asset:///images/ic_add.png"
                pressedImageSource: "asset:///images/ic_add.png"
                enabled: transcriptPage.api ? transcriptPage.api.configured : false
                onClicked: filePicker.open()
            }

            TextArea {
                id: promptField
                // Drop the default gray input frame - sit flat on the compose
                // strip (#0e0e0e).
                backgroundVisible: false
                hintText: {
                    var a = transcriptPage.api;
                    if (! a || ! a.jobRunning)
                        return a ? qsTr("Message %1...").arg(a.agentName) : "";
                    if (! a.queueSupported)
                        return qsTr("Working - Enter sends to the TUI");
                    if (a.queuedCount > 0)
                        return qsTr("%1 queued - Enter queues more").arg(a.queuedCount);
                    return qsTr("Working - Enter queues the message");
                }
                minHeight: 64
                maxHeight: 220
                maximumLength: 8000
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                // Stays enabled while a job runs - prompts queue (TUI parity).
                enabled: transcriptPage.api
                         ? (transcriptPage.api.jobRunning
                            || transcriptPage.api.currentSessionId != "")
                         : false
                // Classic/Q20 hardware keyboard: Enter sends (GrokRemote UX)
                input {
                    submitKey: SubmitKey.Send
                    onSubmitted: transcriptPage.sendNow()
                }
            }

        }
    }

    attachedObjects: [
        ArrayDataModel {
            id: messagesModel
        },
        QuestionSheet {
            id: questionSheet
            api: transcriptPage.api
        },
        FilePicker {
            id: filePicker
            type: FileType.Other
            title: qsTr("Attach a file")
            onFileSelected: {
                if (selectedFiles && selectedFiles.length > 0
                        && transcriptPage.api)
                    transcriptPage.api.uploadAttachment("" + selectedFiles[0]);
            }
        },
        SystemDialog {
            id: rewindConfirm
            title: qsTr("Rewind the session?")
            confirmButton.label: qsTr("Rewind")
            cancelButton.label: qsTr("Cancel")
            onFinished: {
                if (value != SystemUiResult.ConfirmButtonSelection)
                    return;
                if (transcriptPage.api)
                    transcriptPage.api.confirmRewind();
            }
        }
    ]
}
