package com.bb10d.remote.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.bb10d.remote.data.ChangedFileDto
import com.bb10d.remote.data.ChangesDto
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.theme.MonoStyle
import com.bb10d.remote.ui.theme.palette

// What the agent changed, before you answer it. Read-only on the daemon;
// commit and push are explicit actions here, run through the same shell route
// as a "!command". The web client's Changes pane is the same view.
//
// Two scopes, because they answer two questions: "This turn" (daemon 2.26,
// measured from a snapshot taken beside the folder — it works in a folder
// that is not a repository) and "Since last commit", the working tree
// against HEAD.

/** One row of the list under the file summary. */
internal sealed interface DiffRow {
    data class File(val path: String) : DiffRow
    data class Line(val text: String, val kind: Char) : DiffRow
}

/**
 * A unified diff as rows: a header per file, then its lines tagged
 * '+' added, '-' removed, '@' hunk, 'm' metadata, ' ' context.
 */
internal fun diffRows(diff: String): List<DiffRow> {
    val out = ArrayList<DiffRow>()
    var inFile = false
    // False in a file's header, the only place "--- a/x" and "+++ b/x" are
    // names. After the first "@@", a line starting "---" is a removed line
    // whose text starts "--" (a SQL comment, a Markdown rule).
    var inHunk = false
    for (line in diff.split('\n')) {
        if (line.startsWith("diff --git ")) {
            val path = Regex("^diff --git a/(.*) b/(.*)$").find(line)?.groupValues?.get(2)
                ?: line.removePrefix("diff --git ")
            out += DiffRow.File(path)
            inFile = true
            inHunk = false
            continue
        }
        if (!inFile) continue
        if (line.startsWith("@@")) inHunk = true
        if (!inHunk && HIDDEN_META.any { line.startsWith(it) }) continue
        val kind = when {
            line.startsWith("@@") -> '@'
            !inHunk -> 'm'
            line.startsWith("+") -> '+'
            line.startsWith("-") -> '-'
            line.startsWith(" ") || line.isEmpty() -> ' '
            else -> 'm'
        }
        out += DiffRow.Line(line, kind)
    }
    // A trailing newline in the diff is not a line of it.
    if ((out.lastOrNull() as? DiffRow.Line)?.text?.isEmpty() == true) out.removeAt(out.lastIndex)
    return out
}

private val HIDDEN_META = listOf(
    "index ", "similarity index ", "new file mode", "deleted file mode", "old mode", "new mode",
)

/** /Users/me/x or /home/me/x as ~/x: the rest of the path is the news. */
internal fun homeShort(path: String): String =
    path.replace(Regex("^/(?:Users|home)/[^/]+(?=/|$)"), "~")

/** "app" when the diff is measured below the session folder, else "". */
internal fun projectLabel(d: ChangesDto): String =
    d.projects.firstOrNull { it.path == d.project }?.label?.takeIf { it != "." }.orEmpty()

internal fun changesSummary(d: ChangesDto): String = buildList {
    projectLabel(d).takeIf { it.isNotEmpty() }?.let { add(it) }
    if (d.scope == "turn") {
        add(
            if (d.since > 0) "this turn · started ${shortSpan(nowSeconds() - d.since)} ago"
            else "this turn",
        )
    } else {
        add(d.branch.ifBlank { "detached" })
        if (d.ahead > 0) add("↑${d.ahead}")
        if (d.behind > 0) add("↓${d.behind}")
    }
    add(
        if (d.files.isEmpty()) "no changes"
        else "${d.files.size} ${if (d.files.size == 1) "file" else "files"} · +${d.added} −${d.removed}",
    )
}.joinToString(" · ")

private fun nowSeconds(): Double = System.currentTimeMillis() / 1000.0

/**
 * Which question the sheet is answering. Shown only where the daemon can
 * answer both; on an older one the sheet is exactly what it was.
 */
@Composable
private fun ScopeBar(turn: Boolean, onPick: (Boolean) -> Unit) {
    val pal = palette
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        listOf(true to "This turn", false to "Since last commit").forEach { (value, label) ->
            val on = value == turn
            Text(
                label,
                style = MaterialTheme.typography.labelMedium,
                color = if (on) pal.onAccent else pal.dim,
                modifier = Modifier
                    .clip(RoundedCornerShape(14.dp))
                    .background(if (on) pal.accent else androidx.compose.ui.graphics.Color.Transparent)
                    .clickable(role = Role.Tab) { onPick(value) }
                    .padding(horizontal = 12.dp, vertical = 6.dp)
                    .semantics { selected = on },
            )
        }
    }
}

/** Which project the diff is measured in, when the agent wrote in several. */
@Composable
private fun ProjectBar(d: ChangesDto, onPick: (String) -> Unit) {
    val pal = palette
    Row(
        Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 12.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        d.projects.forEach { p ->
            val on = p.path == d.project
            Text(
                if (p.label == ".") "This folder" else p.label,
                style = MaterialTheme.typography.labelMedium,
                color = if (on) pal.onAccent else pal.dim,
                modifier = Modifier
                    .clip(RoundedCornerShape(14.dp))
                    .background(if (on) pal.accent else androidx.compose.ui.graphics.Color.Transparent)
                    .clickable(role = Role.Tab) { onPick(p.path) }
                    .padding(horizontal = 12.dp, vertical = 6.dp)
                    .semantics { selected = on },
            )
        }
    }
}

@Composable
internal fun ChangesSheet(
    ui: TranscriptViewModel.ChangesUi,
    onRefresh: () -> Unit,
    onCommit: (String) -> Unit,
    onPush: () -> Unit,
    canScope: Boolean = false,
    onScope: (Boolean) -> Unit = {},
    onProject: (String) -> Unit = {},
) {
    val pal = palette
    val data = ui.data
    var message by remember { mutableStateOf("") }
    // Cleared when a commit went through, not when it was sent: a failed
    // one (a hook, no user.email, a timeout) keeps what was typed.
    LaunchedEffect(ui.committed) { if (ui.committed > 0) message = "" }
    var confirmPush by remember { mutableStateOf(false) }
    val rows = remember(data?.diff) { diffRows(data?.diff.orEmpty()) }

    Column(
        Modifier
            .fillMaxWidth()
            .navigationBarsPadding()
            .imePadding(),
    ) {
        Row(
            Modifier.padding(start = 16.dp, end = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    "Changes",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.semantics { heading() },
                )
                if (data != null && data.repo) {
                    Text(
                        changesSummary(data),
                        style = MaterialTheme.typography.labelSmall,
                        color = pal.dim,
                    )
                }
            }
            if (ui.loading || ui.busy) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(8.dp))
            }
            TextButton(onClick = onRefresh, enabled = !ui.loading && !ui.busy) { Text("Refresh") }
        }
        if (canScope) ScopeBar(turn = ui.turnScope, onPick = onScope)
        if (data != null && data.projects.size > 1) ProjectBar(data, onProject)
        // The folder the diff is read from, spelled out: the session folder
        // is often a level above it. `root` is git's top level (commit
        // scope) or the snapshotted folder (turn scope).
        val where = data?.let { it.root.ifBlank { it.project.ifBlank { it.cwd } } }.orEmpty()
        if (where.isNotEmpty()) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, end = 16.dp, bottom = 6.dp),
                verticalAlignment = Alignment.Top,
            ) {
                Text(
                    "Folder",
                    style = MaterialTheme.typography.labelSmall,
                    color = pal.dim,
                    modifier = Modifier.padding(end = 8.dp),
                )
                Text(
                    homeShort(where),
                    style = MonoStyle.copy(fontSize = 12.sp),
                    modifier = Modifier.weight(1f),
                )
            }
        }
        Hairline()

        LazyColumn(
            Modifier
                .fillMaxWidth()
                .weight(1f, fill = false)
                .heightIn(min = 120.dp),
        ) {
            if (ui.output.isNotEmpty()) {
                item(key = "output") {
                    Text(
                        ui.output,
                        style = MonoStyle.copy(fontSize = 12.sp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 8.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(pal.codeBg)
                            .padding(10.dp),
                    )
                }
            }
            if (data != null && data.notice.isNotEmpty()) {
                item(key = "notice") { Note(data.notice, pal.dim) }
            }
            when {
                ui.error != null -> item(key = "error") { Note(ui.error, pal.danger) }
                data == null -> item(key = "wait") {
                    Note(
                        if (ui.turnScope) "Comparing with the start of the turn…"
                        else "Reading the working tree…",
                        pal.dim,
                    )
                }
                // scope=turn with no mark to measure from: the daemon says why.
                !data.available -> item(key = "nomark") {
                    Note(
                        data.reason.ifBlank {
                            "There is nothing to measure this turn against yet."
                        },
                        pal.dim,
                    )
                }
                !data.repo -> item(key = "norepo") {
                    Note(
                        "This session's folder is not a git repository, so there is " +
                            "nothing to compare with." +
                            if (canScope) {
                                " “This turn” still works: the daemon snapshots the " +
                                    "folder when a turn starts, outside the folder."
                            } else "",
                        pal.dim,
                    )
                }
                data.files.isEmpty() -> item(key = "clean") {
                    Note(
                        if (data.scope == "turn") {
                            "Nothing here changed during the last turn."
                        } else {
                            "Nothing differs from the last commit." + when (data.ahead) {
                                0 -> ""
                                1 -> " 1 commit is not pushed yet."
                                else -> " ${data.ahead} commits are not pushed yet."
                            }
                        },
                        pal.dim,
                    )
                }
                else -> {
                    items(data.files, key = { "f:" + it.path }) { FileRow(it) }
                    if (data.moreFiles > 0) {
                        item(key = "more") { Note("…and ${data.moreFiles} more files not listed.", pal.dim) }
                    }
                    item(key = "gap") { Spacer(Modifier.height(8.dp)) }
                    items(rows.size, key = { "d:$it" }) { index ->
                        when (val row = rows[index]) {
                            is DiffRow.File -> Text(
                                row.path,
                                style = MonoStyle.copy(fontSize = 12.sp),
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(top = 10.dp)
                                    .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                                    .padding(horizontal = 12.dp, vertical = 6.dp)
                                    .semantics { heading() },
                            )
                            is DiffRow.Line -> Text(
                                row.text.ifEmpty { " " },
                                style = MonoStyle.copy(fontSize = 12.sp),
                                color = when (row.kind) {
                                    '@', 'm' -> pal.dim
                                    else -> MaterialTheme.colorScheme.onSurface
                                },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .background(
                                        when (row.kind) {
                                            '+' -> pal.ok.copy(alpha = 0.16f)
                                            '-' -> pal.danger.copy(alpha = 0.16f)
                                            else -> androidx.compose.ui.graphics.Color.Transparent
                                        },
                                    )
                                    .padding(horizontal = 12.dp),
                            )
                        }
                    }
                    if (data.truncated) {
                        item(key = "cut") {
                            Note(
                                "The diff is too long to show in full. The file list above is complete.",
                                pal.dim,
                            )
                        }
                    }
                }
            }
        }

        // Git actions. Each one says what it will do — and they act on the
        // project's repository, so they are not offered in the turn scope:
        // `git add -A` would take in more than the diff above shows.
        val canPush = data != null && ((data.upstream.isNotEmpty() && data.ahead > 0) || data.canPublish)
        if (data != null && data.repo && data.scope != "turn" &&
            (data.files.isNotEmpty() || canPush)
        ) {
            Hairline()
            Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
                if (data.files.isNotEmpty()) {
                    OutlinedTextField(
                        value = message,
                        onValueChange = { message = it },
                        label = { Text("Commit message") },
                        maxLines = 3,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        "Commit stages all ${data.files.size} changed " +
                            "${if (data.files.size == 1) "file" else "files"} on " +
                            "${data.branch.ifBlank { "this branch" }}.",
                        style = MaterialTheme.typography.bodySmall,
                        color = pal.dim,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    // Publish only where the daemon says there is somewhere
                    // to publish to (not a detached HEAD, not without an origin).
                    if (canPush) {
                        TextButton(onClick = { confirmPush = true }, enabled = !ui.busy) {
                            Text(if (data.upstream.isEmpty()) "Publish branch" else "Push ${data.ahead}")
                        }
                    }
                    if (data.files.isNotEmpty()) {
                        TextButton(
                            onClick = { onCommit(message) },
                            enabled = message.isNotBlank() && !ui.busy,
                        ) { Text("Commit") }
                    }
                }
            }
        }
    }

    if (confirmPush && data != null) {
        val where = data.upstream.ifEmpty { "origin" }
        AlertDialog(
            onDismissRequest = { confirmPush = false },
            title = { Text("Push ${data.branch.ifBlank { "this branch" }} to $where?") },
            text = { Text("Others will see these commits.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmPush = false
                    onPush()
                }) { Text("Push") }
            },
            dismissButton = { TextButton(onClick = { confirmPush = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun Note(text: String, color: androidx.compose.ui.graphics.Color) {
    Text(
        text,
        style = MaterialTheme.typography.bodyMedium,
        color = color,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 18.dp),
    )
}

@Composable
private fun FileRow(file: ChangedFileDto) {
    val pal = palette
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 5.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Text(
            if (file.from.isNotEmpty()) "${file.from} → ${file.path}" else file.path,
            style = MonoStyle.copy(fontSize = 12.sp),
            fontFamily = FontFamily.Monospace,
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            file.status,
            style = MaterialTheme.typography.labelSmall,
            color = when (file.status) {
                "deleted", "conflicted" -> pal.danger
                "added", "untracked" -> pal.ok
                else -> pal.dim
            },
        )
        if (file.binary) {
            Spacer(Modifier.width(8.dp))
            Text("binary", style = MaterialTheme.typography.labelSmall, color = pal.dim)
        } else if (file.added > 0 || file.removed > 0) {
            Spacer(Modifier.width(8.dp))
            Text("+${file.added}", style = MonoStyle.copy(fontSize = 11.sp), color = pal.ok)
            Spacer(Modifier.width(4.dp))
            Text("−${file.removed}", style = MonoStyle.copy(fontSize = 11.sp), color = pal.danger)
        }
    }
}
