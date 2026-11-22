package com.bb10d.remote.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.clickable
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.bb10d.remote.data.Time
import com.bb10d.remote.ui.components.WorkingPulse
import androidx.compose.foundation.selection.selectable
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.material3.Icon
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.layout.Box
import com.bb10d.remote.ui.theme.MonoStyle
import com.bb10d.remote.ui.theme.palette
import kotlinx.coroutines.delay

// What sits under the title bar while a turn runs.

/** The live strip: what the agent is doing right now, and for how long. */
@Composable
internal fun StatusBanner(
    running: Boolean,
    phase: String,
    phaseDetail: String,
    tool: String,
    toolDetail: String,
    elapsed: Int,
    accent: Color,
    agent: String,
) {
    var ticks by remember { mutableStateOf(0) }
    LaunchedEffect(running) {
        while (running) {
            delay(1000)
            ticks++
        }
    }
    AnimatedVisibility(visible = running) {
        // Line 1: human description (daemon phase_detail). Line 2: raw
        // command / path / [attached: …] (tool_detail), when distinct.
        val headline = when {
            phase.isNotEmpty() && phaseDetail.isNotEmpty() -> {
                val verb = phase.replaceFirstChar { it.uppercase() }
                "$verb · $phaseDetail"
            }
            phase.isNotEmpty() -> phase.replaceFirstChar { it.uppercase() }
            tool.isNotEmpty() -> tool
            else -> "$agent is working"
        }
        val second = when {
            toolDetail.isNotBlank() && toolDetail != phaseDetail -> toolDetail
            tool.isNotBlank() && tool != phaseDetail -> tool
            else -> ""
        }
        Column(
            Modifier
                .fillMaxWidth()
                .background(accent.copy(alpha = 0.10f))
                .padding(horizontal = 14.dp, vertical = 8.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                WorkingPulse(accent, size = 7)
                Spacer(Modifier.width(8.dp))
                Text(
                    text = headline,
                    style = MaterialTheme.typography.labelMedium,
                    color = accent,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (elapsed > 0) {
                    Text(
                        text = Time.elapsed(elapsed),
                        style = MaterialTheme.typography.labelSmall,
                        color = accent.copy(alpha = 0.8f),
                    )
                }
            }
            if (second.isNotEmpty() && second != headline) {
                Text(
                    text = second,
                    style = MonoStyle.copy(fontSize = MaterialTheme.typography.labelSmall.fontSize),
                    color = palette.dim,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(start = 15.dp, top = 2.dp),
                )
            }
        }
    }
}

/**
 * Back to the newest message. Shown while reading further up; tinted when
 * output has arrived that has not been seen yet.
 */
@Composable
internal fun JumpToLatest(
    unseen: Boolean,
    accent: Color,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(50)
    Row(
        modifier
            .heightIn(min = 40.dp)
            .clip(shape)
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .border(1.dp, if (unseen) accent else palette.hairline, shape)
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            if (unseen) "↓ New output" else "↓ Jump to latest",
            style = MaterialTheme.typography.labelLarge,
            color = if (unseen) accent else MaterialTheme.colorScheme.onSurface,
        )
    }
}

/**
 * The session window's two faces, Chat and TUI, as one switch in the bar.
 * The TUI used to be a screen of its own on top of the session; now it is
 * the same window showing the host terminal instead of the chat.
 */
@Composable
internal fun FaceSwitch(tui: Boolean, accent: Color, onPick: (Boolean) -> Unit) {
    val pal = palette
    val shape = RoundedCornerShape(50)
    Row(
        Modifier
            .padding(horizontal = 4.dp)
            .clip(shape)
            .border(1.dp, pal.hairline, shape)
            .padding(2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        FaceSide(Icons.Outlined.ChatBubbleOutline, "Chat", !tui, accent) { if (tui) onPick(false) }
        FaceSide(Icons.Outlined.Terminal, "TUI", tui, accent) { if (!tui) onPick(true) }
    }
}

@Composable
private fun FaceSide(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    selected: Boolean,
    accent: Color,
    onClick: () -> Unit,
) {
    val pal = palette
    val shape = RoundedCornerShape(50)
    Box(
        Modifier
            .clip(shape)
            .background(if (selected) accent.copy(alpha = 0.18f) else Color.Transparent)
            .selectable(selected = selected, role = Role.Tab, onClick = onClick)
            .semantics { contentDescription = label }
            .heightIn(min = 36.dp)
            .width(40.dp),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, contentDescription = null, tint = if (selected) accent else pal.dim,
            modifier = Modifier.size(20.dp))
    }
}
