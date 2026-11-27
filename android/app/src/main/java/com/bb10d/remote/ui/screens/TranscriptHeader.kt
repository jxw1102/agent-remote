package com.bb10d.remote.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.height
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
import androidx.compose.ui.draw.shadow
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
            .shadow(6.dp, shape)
            .clip(shape)
            .background(MaterialTheme.colorScheme.surface)
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
internal fun FaceSwitch(
    tui: Boolean,
    accent: Color,
    /** On the folded bar: only the face in use shows; a tap goes to the other. */
    single: Boolean = false,
    onPick: (Boolean) -> Unit,
) {
    val pal = palette
    val shape = RoundedCornerShape(50)
    Row(
        Modifier
            .padding(horizontal = 4.dp)
            .clip(shape)
            .border(1.dp, if (single) Color.Transparent else pal.hairline, shape)
            .padding(2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (single) {
            if (tui) FaceSide(Icons.Outlined.Terminal, "TUI, switch to Chat", true, accent) { onPick(false) }
            else FaceSide(Icons.Outlined.ChatBubbleOutline, "Chat, switch to TUI", true, accent) { onPick(true) }
        } else {
            FaceSide(Icons.Outlined.ChatBubbleOutline, "Chat", !tui, accent) { if (tui) onPick(false) }
            FaceSide(Icons.Outlined.Terminal, "TUI", tui, accent) { if (!tui) onPick(true) }
        }
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
            .background(if (selected) accent.copy(alpha = 0.16f) else Color.Transparent)
            .selectable(selected = selected, role = Role.Tab, onClick = onClick)
            .semantics { contentDescription = label }
            .size(40.dp),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, contentDescription = null, tint = if (selected) accent else pal.dim,
            modifier = Modifier.size(20.dp))
    }
}

/**
 * The session bar's card, in whatever shape it is in: one rounded rectangle
 * when the bar is open ([fold] 0), two pieces when it is folded (1) - a
 * circle round Back at the start and a pill round the end controls, which
 * are [actionsWidth] wide - and everything between while it changes.
 */
@Composable
internal fun BarCard(
    fold: Float,
    total: androidx.compose.ui.unit.Dp,
    actionsWidth: androidx.compose.ui.unit.Dp,
    modifier: Modifier = Modifier,
) {
    val pal = palette
    val color = MaterialTheme.colorScheme.surface
    val edge = androidx.compose.foundation.BorderStroke(1.dp, pal.hairline)
    if (fold <= 0f) {
        // Open: one card, so there is no seam down the middle.
        androidx.compose.material3.Surface(
            modifier = modifier,
            shape = RoundedCornerShape(18.dp),
            color = color,
            border = edge,
            shadowElevation = 8.dp,
        ) {}
        return
    }
    val lerp = { a: androidx.compose.ui.unit.Dp, b: androidx.compose.ui.unit.Dp ->
        androidx.compose.ui.unit.lerp(a, b, fold)
    }
    // The two parts meet just after Back (its 48 dp button and the bar's
    // 4 dp inset either side), not in the middle: folding, Back's part only
    // rounds off, and the other draws away from it toward the end controls.
    val split = 56.dp
    val outer = lerp(18.dp, 28.dp)
    val inner = lerp(0.dp, 28.dp)
    androidx.compose.foundation.layout.BoxWithConstraints(modifier.padding(vertical = lerp(0.dp, 4.dp))) {
        // Folded, Back's piece is the 48 dp circle of its button, so the
        // button's ripple fills it.
        androidx.compose.material3.Surface(
            modifier = Modifier.align(Alignment.CenterStart)
                .padding(start = lerp(0.dp, 4.dp))
                .width(lerp(split, 48.dp))
                .height(lerp(maxHeight, 48.dp)),
            shape = RoundedCornerShape(topStart = outer, bottomStart = outer, topEnd = inner, bottomEnd = inner),
            color = color,
            border = edge,
            shadowElevation = 6.dp,
        ) {}
        androidx.compose.material3.Surface(
            modifier = Modifier.align(Alignment.CenterEnd)
                .width(lerp(total - split, actionsWidth + 8.dp)).fillMaxHeight(),
            shape = RoundedCornerShape(topStart = inner, bottomStart = inner, topEnd = outer, bottomEnd = outer),
            color = color,
            border = edge,
            shadowElevation = 6.dp,
        ) {}
    }
}
