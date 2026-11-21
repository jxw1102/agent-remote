package com.bb10d.remote.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.History
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.data.ExecMode
import com.bb10d.remote.data.Time
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.MetaPill
import com.bb10d.remote.ui.theme.palette

// Bottom sheets opened from the transcript: message actions, queue, session options.

/**
 * What a long press offers. Copy is always here; rewind only appears on your
 * own messages, and only when the session's harness advertises it — an
 * option that would fail is worse than no option.
 *
 * Historical user prompts also show when they were sent (from the journal
 * timestamp) under the text preview.
 */
@Composable
internal fun MessageActionsSheet(
    item: TranscriptItem,
    accent: Color,
    rewindSteps: Int,
    rewindBlockedReason: String?,
    onCopy: () -> Unit,
    onRewind: () -> Unit,
) {
    val pal = palette
    // Live echo has no journal ts yet; only historical rows carry one.
    val whenSent = remember(item.ts, item.live, item.role) {
        if (item.role == "user" && !item.live && item.ts.isNotBlank()) {
            Time.fullStamp(Time.epochMs(item.ts))
        } else {
            ""
        }
    }
    Column(Modifier.padding(bottom = 28.dp)) {
        Column(Modifier.padding(horizontal = 20.dp, vertical = 12.dp)) {
            Text(
                text = item.text.lineSequence().first().take(120),
                style = MaterialTheme.typography.bodyMedium,
                color = pal.dim,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            if (whenSent.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Text(
                    text = whenSent,
                    style = MaterialTheme.typography.labelMedium,
                    color = pal.dim,
                )
            }
        }
        Hairline(inset = 16)

        ActionRow(Icons.Outlined.ContentCopy, "Copy message", accent, onCopy)

        if (item.role == "user" && rewindSteps > 0) {
            if (rewindBlockedReason == null) {
                ActionRow(
                    icon = Icons.Outlined.History,
                    label = "Rewind to here",
                    tint = pal.danger,
                    onClick = onRewind,
                    subtitle = if (rewindSteps == 1) {
                        "Undo your last message"
                    } else {
                        "Undo the last $rewindSteps of your messages"
                    },
                )
            } else {
                Text(
                    text = "Rewind unavailable — $rewindBlockedReason",
                    style = MaterialTheme.typography.bodySmall,
                    color = pal.dim,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 14.dp),
                )
            }
        }
    }
}

@Composable
internal fun ActionRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    tint: Color,
    onClick: () -> Unit,
    subtitle: String = "",
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, tint = tint)
        Spacer(Modifier.width(16.dp))
        Column {
            Text(label, style = MaterialTheme.typography.bodyLarge)
            if (subtitle.isNotEmpty()) {
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = palette.dim,
                )
            }
        }
    }
}

@Composable
internal fun QueueSheet(
    queued: List<com.bb10d.remote.data.QueuedDto>,
    onCancel: (String) -> Unit,
    onClose: () -> Unit,
) {
    Column(Modifier.padding(bottom = 24.dp)) {
        Text(
            "Queued behind this turn",
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(16.dp),
        )
        Text(
            "The daemon owns this queue, so it survives losing the app or the network.",
            style = MaterialTheme.typography.bodySmall,
            color = palette.dim,
            modifier = Modifier.padding(horizontal = 16.dp),
        )
        Spacer(Modifier.height(12.dp))
        queued.forEach { entry ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = entry.prompt,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { onCancel(entry.id) }) { Text("Cancel") }
            }
            Hairline(inset = 16)
        }
        if (queued.isEmpty()) {
            Text(
                "Nothing queued.",
                style = MaterialTheme.typography.bodyMedium,
                color = palette.dim,
                modifier = Modifier.padding(16.dp),
            )
            TextButton(onClick = onClose, modifier = Modifier.padding(start = 8.dp)) {
                Text("Close")
            }
        }
    }
}


/**
 * What is true of this session and its daemon. Execution, model and effort
 * are not here: they are per message, on the composer.
 */
@Composable
internal fun SessionOptionsSheet(vm: TranscriptViewModel, profile: com.bb10d.remote.data.Profile) {
    val caps = profile.caps
    val harness by vm.harnessProvider.collectAsStateWithLifecycle()
    val h = harness.ifBlank { null }
    val session by vm.session.collectAsStateWithLifecycle()
    val lastModel = session?.model.orEmpty()
    Column(Modifier.padding(horizontal = 16.dp).padding(bottom = 28.dp)) {
        Text("Session options", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(4.dp))
        Text(
            buildString {
                append(profile.displayName)
                if (harness.isNotBlank()) append(" · $harness")
                if (lastModel.isNotBlank() && lastModel != "default" && lastModel != "interactive") {
                    append(" · last ran on $lastModel")
                }
            },
            style = MaterialTheme.typography.bodySmall,
            color = palette.dim,
        )
        Spacer(Modifier.height(10.dp))
        Text(
            "Execution, model and effort are set above the message box.",
            style = MaterialTheme.typography.bodySmall,
            color = palette.dim,
        )
        if (caps.goalWatchFor(h)) {
            GoalBlock(vm)
        }
        if (caps.rewindFor(h)) {
            Spacer(Modifier.height(16.dp))
            Text(
                "Long-press one of your messages to rewind the session back to it.",
                style = MaterialTheme.typography.bodySmall,
                color = palette.dim,
            )
        }
        Spacer(Modifier.height(12.dp))
        Row {
            MetaPill("agentremoted ${caps.version}")
            Spacer(Modifier.width(6.dp))
            if (caps.host.isNotEmpty()) MetaPill(caps.host)
        }
    }
}

/**
 * The Codex goal on this thread, and the daemon's watchdog for it.
 *
 * A goal makes codex restart itself after every turn - until it doesn't,
 * and then the thread sits idle with work left. The switch asks the daemon
 * to type `/goal resume` when that happens. Nothing is drawn for a thread
 * without a goal.
 */
@Composable
internal fun GoalBlock(vm: TranscriptViewModel) {
    val goal by vm.goal.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.loadGoal() }
    val row = goal ?: return
    Spacer(Modifier.height(20.dp))
    Text("Goal", style = MaterialTheme.typography.labelLarge, color = palette.dim)
    Spacer(Modifier.height(6.dp))
    Surface(
        color = palette.codeBg,
        shape = MaterialTheme.shapes.small,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            row.objective.ifBlank { "(no objective)" },
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
        )
    }
    Spacer(Modifier.height(8.dp))
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            "Keep it moving",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f),
        )
        Switch(
            checked = row.watching,
            enabled = row.status == "active",
            onCheckedChange = { vm.setGoalWatch(it) },
        )
    }
    Text(
        goalStatusLine(row),
        style = MaterialTheme.typography.bodySmall,
        color = palette.dim,
    )
}

/** "4m" / "2h 10m" - short enough for a status line. */
internal fun shortSpan(seconds: Double): String {
    val s = seconds.toLong().coerceAtLeast(0L)
    if (s < 60) return "${s}s"
    val m = s / 60
    if (m < 60) return "${m}m"
    return "${m / 60}h ${m % 60}m"
}

/** One line of plain truth about a goal's state (same words as web). */
internal fun goalStatusLine(row: com.bb10d.remote.data.GoalDto): String {
    if (row.status != "active") {
        return "This goal is ${row.status.ifBlank { "gone" }}."
    }
    if (row.stoppedWhy.isNotBlank()) {
        return "The daemon stopped: ${row.stoppedWhy}. Switch it back on to try again."
    }
    val bits = mutableListOf<String>()
    when {
        row.working -> bits += "Working now."
        !row.liveTui -> bits += "No live TUI on the host \u2014 send this session a " +
            "message first, so there is a prompt to type into."
        else -> {
            bits += "Idle for ${shortSpan(row.idleS)}."
            if (row.watching) {
                bits += "A resume follows ${shortSpan(row.idleThresholdS)} of quiet."
            }
        }
    }
    if (row.resumes > 0) {
        val last = if (row.lastResumeAt > 0) {
            val ago = System.currentTimeMillis() / 1000.0 - row.lastResumeAt
            ", last ${shortSpan(ago)} ago."
        } else "."
        bits += "Resumed ${row.resumes} " + (if (row.resumes == 1) "time" else "times") + last
    }
    if (!row.watching && row.stoppedWhy.isBlank()) {
        bits += "Codex restarts a goal itself; this is for when it does not."
    }
    return bits.joinToString(" ")
}
