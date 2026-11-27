package com.bb10d.remote.ui.screens

import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.ui.draw.shadow
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.bb10d.remote.ui.components.WorkingPulse
import com.bb10d.remote.ui.theme.MonoStyle
import com.bb10d.remote.ui.theme.palette

// The agent is waiting on you: a permission to grant, a question to answer.

@Composable
internal fun PermissionPrompt(
    tool: String,
    detail: String,
    accent: Color,
    onAllow: () -> Unit,
    onDeny: () -> Unit,
) {
    val pal = palette
    Column(
        Modifier
            .fillMaxWidth()
            // The same card as the bars it floats between: their edges,
            // corners, surface and shadow; the accent is a wash over it.
            .shadow(8.dp, RoundedCornerShape(18.dp))
            .clip(RoundedCornerShape(18.dp))
            .background(MaterialTheme.colorScheme.surface)
            .background(accent.copy(alpha = 0.06f))
            .border(1.dp, accent.copy(alpha = 0.4f), RoundedCornerShape(18.dp))
            .padding(14.dp),
    ) {
        Text(
            "Allow $tool?",
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.onSurface,
            // The agent has stopped for this: announce it when it appears.
            modifier = Modifier.semantics {
                heading()
                liveRegion = LiveRegionMode.Assertive
            },
        )
        if (detail.isNotBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(
                text = detail,
                style = MonoStyle,
                color = pal.dim,
                maxLines = 6,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
            TextButton(onClick = onDeny) { Text("Deny", color = pal.danger) }
            Spacer(Modifier.width(8.dp))
            TextButton(onClick = onAllow) { Text("Allow", color = accent) }
        }
    }
}

@Composable
internal fun QuestionBanner(accent: Color, onAnswer: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .shadow(6.dp, RoundedCornerShape(18.dp))
            .clip(RoundedCornerShape(18.dp))
            .background(MaterialTheme.colorScheme.surface)
            .background(accent.copy(alpha = 0.14f))
            .border(1.dp, accent.copy(alpha = 0.4f), RoundedCornerShape(18.dp))
            .heightIn(min = 44.dp)
            .clickable(role = Role.Button, onClickLabel = "answer", onClick = onAnswer)
            .semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Assertive }
            .padding(horizontal = 14.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        WorkingPulse(accent, size = 7)
        Spacer(Modifier.width(10.dp))
        Text(
            "A question is waiting for you",
            style = MaterialTheme.typography.labelMedium,
            color = accent,
            modifier = Modifier.weight(1f),
        )
        Text(
            "Answer",
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.SemiBold,
            color = accent,
        )
    }
}
