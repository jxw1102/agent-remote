package com.bb10d.remote.ui.components

import androidx.compose.ui.graphics.luminance
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.clickable
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.palette

/**
 * Which agent answered, at a glance.
 *
 * Coloured dot = harness; text = host profile name (or harness label when
 * compact). No vector brand logos — those broke release builds (PathBuilder
 * has curveTo, not cubicTo) and match the web client's text chips.
 */
@Composable
fun ProviderChip(
    provider: String,
    profileName: String,
    modifier: Modifier = Modifier,
    compact: Boolean = false,
    /** The coloured dot before the name; the session rows leave it out. */
    dot: Boolean = true,
) {
    val accent = Accent.forProvider(provider)
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(6.dp))
            .background(accent.tint.copy(alpha = 0.14f))
            .border(1.dp, accent.tint.copy(alpha = 0.35f), RoundedCornerShape(6.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (dot) {
            Box(
                Modifier
                    .size(6.dp)
                    .clip(CircleShape)
                    .background(accent.tint),
            )
            Spacer(Modifier.width(5.dp))
        }
        Text(
            text = if (compact) accent.label else profileName.ifBlank { accent.label },
            style = MaterialTheme.typography.labelSmall,
            color = accent.tint,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** Slowly breathing dot: something is running on the other end. */
@Composable
fun WorkingPulse(color: Color, modifier: Modifier = Modifier, size: Int = 8) {
    val transition = rememberInfiniteTransition(label = "pulse")
    val alpha by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse),
        label = "alpha",
    )
    Box(
        modifier
            .size(size.dp)
            .alpha(alpha)
            .clip(CircleShape)
            .background(color),
    )
}

/** Three dots rising in turn: the transcript's own "still working" mark. */
@Composable
fun WorkingDots(color: Color, modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "dots")
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        for (i in 0 until 3) {
            val phase by transition.animateFloat(
                initialValue = 0f,
                targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    tween(1200, easing = androidx.compose.animation.core.LinearEasing),
                    RepeatMode.Restart,
                    initialStartOffset = androidx.compose.animation.core.StartOffset(i * 150),
                ),
                label = "dot$i",
            )
            // Up and back in the first 60% of the cycle, then rest.
            val up = if (phase < 0.6f) kotlin.math.sin(phase / 0.6f * Math.PI).toFloat() else 0f
            Box(
                Modifier
                    .size(6.dp)
                    .offset(y = (-4f * up).dp)
                    .alpha(0.35f + 0.65f * up)
                    .clip(CircleShape)
                    .background(color),
            )
        }
    }
}

@Composable
fun ErrorBanner(
    text: String,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
) {
    val pal = palette
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(pal.danger.copy(alpha = 0.12f))
            .border(1.dp, pal.danger.copy(alpha = 0.35f), RoundedCornerShape(10.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Outlined.ErrorOutline,
            contentDescription = null,
            tint = pal.danger,
            modifier = Modifier.size(18.dp),
        )
        Spacer(Modifier.width(10.dp))
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f),
        )
        if (action != null) {
            Spacer(Modifier.width(8.dp))
            action()
        }
    }
}

@Composable
fun EmptyState(
    title: String,
    body: String,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 32.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = body,
            style = MaterialTheme.typography.bodyMedium,
            color = palette.dim,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        if (action != null) {
            Spacer(Modifier.height(20.dp))
            action()
        }
    }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text.uppercase(),
        style = MaterialTheme.typography.labelSmall.copy(
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 1.sp,
        ),
        color = palette.dim,
        modifier = modifier.padding(horizontal = 16.dp, vertical = 8.dp),
    )
}

@Composable
fun Hairline(modifier: Modifier = Modifier, inset: Int = 0) {
    Box(
        modifier
            .fillMaxWidth()
            .padding(start = inset.dp)
            .height(1.dp)
            .background(palette.hairline),
    )
}

/** Small monospace pill for models, branches, modes. */
@Composable
fun MetaPill(text: String, modifier: Modifier = Modifier, tint: Color? = null) {
    val pal = palette
    val color = tint ?: pal.dim
    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        color = color,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier
            .clip(RoundedCornerShape(5.dp))
            .background(color.copy(alpha = 0.10f))
            .padding(horizontal = 5.dp, vertical = 1.dp),
    )
}

/**
 * Focus state tag. Colour carries urgency: amber wants you now, green is busy,
 * neutral can wait. Same pill shape as every other row chip — Focus is a
 * filter over the session list, not a different layout.
 */
@Composable
fun FocusPill(state: String, modifier: Modifier = Modifier, unread: Boolean = false) {
    val pal = palette
    val builtin = when (state) {
        "needs_answer" -> "needs answer"
        "failed" -> "failed"
        "working" -> "working"
        "turn_finished" -> "turn finished"
        else -> return
    }
    // The daemon's wording when it has sent it, so every client says the same.
    val label = com.bb10d.remote.ui.theme.DaemonVocabulary.focusLabel(state) ?: builtin
    // Lit until opened, dim afterwards: an unread result should stand out
    // in the list, a read one is just waiting on you.
    val color = if (state == "turn_finished") {
        if (unread) MaterialTheme.colorScheme.onSurface else pal.dim
    } else stateTint(state)
    // The label carries the state's dot: breathing while the agent works,
    // steady while it waits on you. There is no separate dot beside it.
    val dotted = state == "working" || state == "needs_answer"
    Row(
        modifier
            .clip(RoundedCornerShape(5.dp))
            .background(color.copy(alpha = 0.10f))
            .padding(horizontal = 5.dp, vertical = 1.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (dotted) {
            if (state == "working") WorkingPulse(color, size = 6)
            else Box(Modifier.size(6.dp).clip(CircleShape).background(color))
            Spacer(Modifier.width(5.dp))
        }
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = color,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/**
 * One colour per session state, wherever it is shown (rows, the tab bubbles,
 * the live strip, the working row): the daemon's when it has sent them, else
 * amber for "waits on you", green for "working", red for "failed".
 */
@Composable
fun stateTint(state: String): Color {
    val pal = palette
    val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
    return com.bb10d.remote.ui.theme.DaemonVocabulary.stateColour(state, dark) ?: when (state) {
        "needs_answer" -> pal.warn
        "failed" -> pal.danger
        "working" -> pal.ok
        else -> pal.dim
    }
}

val ScreenPadding = PaddingValues(horizontal = 16.dp)

/** Words for a connection state; empty for live (nothing to say). */
fun connLabel(state: com.bb10d.remote.data.ConnState?): String = when (state) {
    com.bb10d.remote.data.ConnState.RECONNECTING -> "reconnecting…"
    com.bb10d.remote.data.ConnState.OFFLINE -> "offline"
    com.bb10d.remote.data.ConnState.CONNECTING, null -> "connecting…"
    com.bb10d.remote.data.ConnState.LIVE -> ""
}

@Composable
fun connTint(state: com.bb10d.remote.data.ConnState?): Color = when (state) {
    com.bb10d.remote.data.ConnState.LIVE -> palette.ok
    com.bb10d.remote.data.ConnState.OFFLINE -> palette.danger
    com.bb10d.remote.data.ConnState.RECONNECTING -> palette.warn
    else -> palette.dim
}


/**
 * A round button whose ripple is the button: it starts at the touch and
 * fills the whole disc, to its edge. (Material's IconButton draws its own
 * smaller state layer inside whatever background it is given.)
 */
@Composable
fun CircleButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    size: androidx.compose.ui.unit.Dp = 40.dp,
    enabled: Boolean = true,
    color: Color = Color.Transparent,
    selected: Boolean? = null,
    content: @Composable () -> Unit,
) {
    androidx.compose.foundation.layout.Box(
        modifier
            .size(size)
            .clip(androidx.compose.foundation.shape.CircleShape)
            .background(color)
            .then(
                if (selected != null) Modifier.toggleable(
                    value = selected,
                    enabled = enabled,
                    role = androidx.compose.ui.semantics.Role.Switch,
                    onValueChange = { onClick() },
                ) else Modifier.clickable(
                    enabled = enabled,
                    role = androidx.compose.ui.semantics.Role.Button,
                    onClick = onClick,
                ),
            ),
        contentAlignment = Alignment.Center,
    ) { content() }
}
