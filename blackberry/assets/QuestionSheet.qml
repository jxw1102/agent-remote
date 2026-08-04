import bb.cascades 1.4

// AskUserQuestion: the agent stopped mid-turn to ask. api.questions holds
// [{question, header, multi_select, options:[{label, description}]}]; the
// daemon is blocked on our reply and types it into the host TUI's panel.
//
// Rows are flat (question header row + one row per option) and typed by
// kind, the same pattern as the transcript: a Button inside a
// ListItemComponent never receives the tap on BB10, so the whole row is
// the affordance and the ListView's onTriggered does the work.
Sheet {
    id: questionSheet

    // Pinned by TranscriptPage (api: transcriptPage.api).
    property variant api
    property bool showing: false

    peekEnabled: false

    // picks[qi] = array of chosen option indices (single-select: 0 or 1 long)
    property variant picks: []

    property variant snapshot: questionSheet.api ? questionSheet.api.questions : []
    onSnapshotChanged: {
        // The daemon cancelled/timed out this ask while the sheet was open.
        if (showing && (! questionSheet.api || ! questionSheet.api.questionPending))
            questionSheet.close();
    }

    // Which pick (per question) accepts a typed note, and its placeholder.
    property variant noteFor: []
    property variant noteHint: []

    function rebuild() {
        questionModel.clear();
        var qs = questionSheet.api ? questionSheet.api.questions : [];
        var p = [];
        var nf = [];
        var nh = [];
        var nt = [];
        for (var i = 0; i < qs.length; ++i) {
            var q = qs[i];
            var multi = q.multi_select ? 1 : 0;
            p.push(multi ? [] : [ 0 ]);
            nf.push("" + (q.note_for ? q.note_for : ""));
            nh.push("" + (q.note_hint ? q.note_hint : qsTr("Add a note")));
            nt.push("");
            questionModel.append({
                kind: "q",
                qi: i,
                header: "" + (q.header ? q.header : ""),
                text: "" + (q.question ? q.question : ""),
                hint: multi ? qsTr("Pick any") : qsTr("Pick one")
            });
            // Body rendered by the transcript's painter (markdown, not raw).
            var blocks = q.blocks ? q.blocks : [];
            if (blocks.length > 0 && questionSheet.api) {
                var rows = questionSheet.api.renderBlocks(blocks);
                for (var r = 0; r < rows.length; ++r) {
                    var it = rows[r];
                    var k = "" + it.kind;
                    if (k == "paintimg") {
                        questionModel.append({
                            kind: "img", qi: i,
                            imgPath: "" + it.imgPath,
                            imgW: it.imgW ? it.imgW : 0,
                            imgH: it.imgH ? it.imgH : 0
                        });
                    } else if (("" + (it.rich ? it.rich : "")).length > 0
                               || ("" + (it.text ? it.text : "")).length > 0) {
                        questionModel.append({
                            kind: "body", qi: i,
                            rich: "" + (it.rich ? it.rich : ""),
                            text: "" + (it.text ? it.text : "")
                        });
                    }
                }
            }
            var opts = q.options ? q.options : [];
            for (var j = 0; j < opts.length; ++j) {
                questionModel.append({
                    kind: "opt",
                    qi: i,
                    oi: j,
                    multi: multi,
                    label: "" + (opts[j].label ? opts[j].label : ""),
                    desc: "" + (opts[j].description ? opts[j].description : ""),
                    // Marks the option that opens the note box, so it is
                    // discoverable before you tap it.
                    takesNote: ("" + (opts[j].label ? opts[j].label : ""))
                               == ("" + nf[i]) ? 1 : 0,
                    picked: (! multi && j == 0) ? 1 : 0
                });
            }
        }
        picks = p;
        noteFor = nf;
        noteHint = nh;
        noteTexts = nt;
        activeNote = -1;
        noteField.text = "";
    }

    // One note per question (a 3-question ask can want text on each), kept
    // here because a TextArea inside a ListItemComponent never gets the tap
    // on BB10. The single box below the list serves whichever question's
    // note-taking option is currently picked; its text is parked per index.
    property variant noteTexts: []
    property int activeNote: -1

    // Does question qi have its note-taking option picked?
    function noteWanted(qi) {
        if (! noteFor || qi >= noteFor.length || ("" + noteFor[qi]).length == 0)
            return false;
        var qs = questionSheet.api ? questionSheet.api.questions : [];
        if (qi >= qs.length)
            return false;
        var opts = qs[qi].options ? qs[qi].options : [];
        var chosen = qi < picks.length ? picks[qi] : [];
        for (var k = 0; k < chosen.length; ++k) {
            var o = opts[chosen[k]];
            if (o && ("" + o.label) == ("" + noteFor[qi]))
                return true;
        }
        return false;
    }

    // Park the box's text on the question it belongs to, then point it at
    // `qi` (-1 hides it).
    function focusNote(qi) {
        var next = [];
        for (var i = 0; i < noteTexts.length; ++i)
            next.push(noteTexts[i]);
        if (activeNote >= 0 && activeNote < next.length)
            next[activeNote] = "" + noteField.text;
        noteTexts = next;
        activeNote = qi;
        noteField.text = (qi >= 0 && qi < next.length) ? "" + next[qi] : "";
        noteBox.visible = qi >= 0;
    }

    // Show the box for the last question that asked for one (the one just
    // tapped, in practice), or hide it when none does.
    function syncNote(preferQi) {
        if (preferQi >= 0 && noteWanted(preferQi)) {
            focusNote(preferQi);
            return;
        }
        var qs = questionSheet.api ? questionSheet.api.questions : [];
        for (var i = qs.length - 1; i >= 0; --i) {
            if (noteWanted(i)) {
                focusNote(i);
                return;
            }
        }
        focusNote(-1);
    }

    function show() {
        rebuild();
        // Single-select starts on option 1, so a note may be due already.
        syncNote(-1);
        showing = true;
        open();
    }

    onClosed: {
        showing = false;
    }

    // Redraw every option row of one question from picks[qi].
    function refreshQuestion(qi) {
        var chosen = picks[qi];
        for (var r = 0; r < questionModel.size(); ++r) {
            var item = questionModel.data([ r ]);
            if (! item || "" + item.kind != "opt" || item.qi != qi)
                continue;
            var on = 0;
            for (var k = 0; k < chosen.length; ++k) {
                if (chosen[k] == item.oi)
                    on = 1;
            }
            if (item.picked != on) {
                item.picked = on;
                questionModel.replace(r, item);
            }
        }
    }

    function toggle(qi, oi, multi) {
        // picks is a value property: mutate a copy, then assign it back.
        var next = [];
        for (var i = 0; i < picks.length; ++i)
            next.push(picks[i]);
        var chosen = [];
        for (var k = 0; k < next[qi].length; ++k)
            chosen.push(next[qi][k]);
        if (multi) {
            var at = -1;
            for (var m = 0; m < chosen.length; ++m) {
                if (chosen[m] == oi)
                    at = m;
            }
            if (at >= 0)
                chosen.splice(at, 1);
            else
                chosen.push(oi);
        } else {
            chosen = [ oi ];
        }
        next[qi] = chosen;
        picks = next;
        refreshQuestion(qi);
        syncNote(qi);
    }

    function submit() {
        if (! questionSheet.api)
            return;
        var qs = questionSheet.api.questions;
        var answers = [];
        for (var i = 0; i < qs.length; ++i) {
            var opts = qs[i].options ? qs[i].options : [];
            var chosen = i < picks.length ? picks[i] : [];
            var labels = [];
            for (var k = 0; k < chosen.length; ++k) {
                var o = opts[chosen[k]];
                if (o && o.label)
                    labels.push("" + o.label);
            }
            // Nothing ticked: the panel needs a pick, so fall back to the
            // first option (what the TUI's own cursor starts on).
            if (labels.length == 0 && opts.length > 0)
                labels.push("" + opts[0].label);
            answers.push(labels);
        }
        // One note per question: park whatever is in the box first, then send
        // the text of every question whose note-taking option is picked.
        focusNote(activeNote);
        var notes = [];
        for (var n = 0; n < qs.length; ++n) {
            notes.push(noteWanted(n) && n < noteTexts.length
                       ? "" + noteTexts[n] : "");
        }
        questionSheet.api.resolveQuestion(answers, notes);
        questionSheet.close();
    }

    Page {
        titleBar: TitleBar {
            title: questionSheet.api
                   ? qsTr("%1 is asking").arg(questionSheet.api.agentName)
                   : qsTr("A question for you")
            dismissAction: ActionItem {
                title: qsTr("Cancel")
                onTriggered: {
                    if (questionSheet.api)
                        questionSheet.api.cancelQuestion();
                    questionSheet.close();
                }
            }
            acceptAction: ActionItem {
                title: qsTr("Send")
                onTriggered: questionSheet.submit()
            }
        }

        Container {
            background: Color.create("#121212")
            horizontalAlignment: HorizontalAlignment.Fill
            verticalAlignment: VerticalAlignment.Fill
            layout: StackLayout {}

            ListView {
                id: questionList
                dataModel: questionModel
                horizontalAlignment: HorizontalAlignment.Fill
                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                property int rowWidth: questionSheet.api
                                       ? questionSheet.api.screenWidth : 720

                function itemType(data, indexPath) {
                    if (! data)
                        return "opt";
                    var k = "" + data.kind;
                    if (k == "q" || k == "img" || k == "body")
                        return k;
                    return "opt";
                }

                onTriggered: {
                    var row = indexPath && indexPath.length > 0 ? indexPath[0] : -1;
                    if (row < 0)
                        return;
                    var item = questionModel.data([ row ]);
                    if (! item || "" + item.kind != "opt")
                        return;
                    questionSheet.toggle(item.qi, item.oi, item.multi == 1);
                }

                listItemComponents: [
                    ListItemComponent {
                        type: "q"
                        Container {
                            background: Color.create("#121212")
                            preferredWidth: ListItem.view
                                            ? ListItem.view.rowWidth : 720
                            leftPadding: 14
                            rightPadding: 14
                            topPadding: 16
                            bottomPadding: 6

                            Label {
                                text: ListItemData.header
                                visible: ("" + ListItemData.header).length > 0
                                textStyle.fontSize: FontSize.XSmall
                                textStyle.color: Color.create("#e8c060")
                                textStyle.fontWeight: FontWeight.Bold
                            }
                            Label {
                                text: ListItemData.hint
                                textStyle.fontSize: FontSize.XXSmall
                                textStyle.color: Color.create("#7a7a7a")
                            }
                        }
                    },
                    // Question body, painted by RichPaint (markdown look).
                    ListItemComponent {
                        type: "img"
                        Container {
                            background: Color.create("#121212")
                            preferredWidth: ListItem.view
                                            ? ListItem.view.rowWidth : 720
                            leftPadding: 14
                            rightPadding: 14
                            topPadding: 1
                            bottomPadding: 1
                            ImageView {
                                imageSource: ListItemData.imgPath
                                preferredWidth: ListItemData.imgW
                                preferredHeight: ListItemData.imgH
                                minWidth: ListItemData.imgW
                                minHeight: ListItemData.imgH
                                scalingMethod: ScalingMethod.None
                                horizontalAlignment: HorizontalAlignment.Left
                            }
                        }
                    },
                    // Fallback when a paint failed: the daemon's rich markup.
                    ListItemComponent {
                        type: "body"
                        Container {
                            background: Color.create("#121212")
                            preferredWidth: ListItem.view
                                            ? ListItem.view.rowWidth : 720
                            leftPadding: 14
                            rightPadding: 14
                            topPadding: 2
                            bottomPadding: 2
                            Label {
                                text: ("" + ListItemData.rich).length > 0
                                      ? ListItemData.rich : ListItemData.text
                                textFormat: ("" + ListItemData.rich).length > 0
                                            ? TextFormat.Html : TextFormat.Plain
                                multiline: true
                                textStyle.fontSize: FontSize.Small
                                textStyle.color: Color.create("#d0d0d0")
                            }
                        }
                    },
                    ListItemComponent {
                        type: "opt"
                        Container {
                            background: ListItemData.picked == 1
                                        ? Color.create("#1e2a1e")
                                        : Color.create("#181818")
                            preferredWidth: ListItem.view
                                            ? ListItem.view.rowWidth : 720
                            leftPadding: 14
                            rightPadding: 14
                            topPadding: 10
                            bottomPadding: 10
                            topMargin: 2
                            layout: StackLayout {
                                orientation: LayoutOrientation.LeftToRight
                            }

                            Label {
                                // Radio for single-select, checkbox for multi.
                                text: ListItemData.picked == 1
                                      ? (ListItemData.multi == 1 ? "[x]" : ">")
                                      : (ListItemData.multi == 1 ? "[ ]" : " ")
                                textStyle.fontSize: FontSize.Small
                                textStyle.color: Color.create("#6fdc6f")
                                verticalAlignment: VerticalAlignment.Center
                                rightMargin: 10
                            }
                            Container {
                                layoutProperties: StackLayoutProperties { spaceQuota: 1 }
                                Label {
                                    // ✎ marks an option that takes typed text.
                                    text: ListItemData.takesNote == 1
                                          ? "✎ " + ListItemData.label
                                          : ListItemData.label
                                    multiline: true
                                    textStyle.fontSize: FontSize.Small
                                    textStyle.color: Color.create("#e8e8e8")
                                }
                                Label {
                                    text: ListItemData.desc
                                    visible: ("" + ListItemData.desc).length > 0
                                    multiline: true
                                    textStyle.fontSize: FontSize.XXSmall
                                    textStyle.color: Color.create("#9a9a9a")
                                }
                            }
                        }
                    }
                ]
            }

            // Free text for the pick that takes one (grok: "Request changes",
            // or an ask's "Type my own answer"). One box, pointed at whichever
            // question currently wants a note — its header is shown so a
            // multi-question ask makes clear which one it belongs to.
            Container {
                id: noteBox
                visible: false
                background: Color.create("#181818")
                horizontalAlignment: HorizontalAlignment.Fill
                leftPadding: 14
                rightPadding: 14
                topPadding: 8
                bottomPadding: 10
                Label {
                    text: {
                        var qi = questionSheet.activeNote;
                        var hint = (questionSheet.noteHint && qi >= 0
                                    && qi < questionSheet.noteHint.length)
                                   ? "" + questionSheet.noteHint[qi]
                                   : qsTr("Add a note");
                        var qs = questionSheet.api
                                 ? questionSheet.api.questions : [];
                        if (qs.length > 1 && qi >= 0 && qi < qs.length
                                && qs[qi].header)
                            return "" + qs[qi].header + " — " + hint;
                        return hint;
                    }
                    multiline: true
                    textStyle.fontSize: FontSize.XXSmall
                    textStyle.color: Color.create("#e8c060")
                }
                TextArea {
                    id: noteField
                    hintText: qsTr("Type your note, then Send")
                    preferredHeight: 220
                    horizontalAlignment: HorizontalAlignment.Fill
                    input.submitKey: SubmitKey.None
                    textStyle.fontSize: FontSize.Small
                }
            }
        }
    }

    attachedObjects: [
        ArrayDataModel {
            id: questionModel
        }
    ]
}
