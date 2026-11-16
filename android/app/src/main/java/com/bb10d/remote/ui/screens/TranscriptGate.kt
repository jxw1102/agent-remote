package com.bb10d.remote.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
            .padding(horizontal = 12.dp, vertical = 8.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .border(1.dp, accent.copy(alpha = 0.4f), RoundedCornerShape(12.dp))
            .padding(14.dp),
    ) {
        Text(
            "Allow $tool?",
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.onSurface,
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
            .padding(horizontal = 12.dp, vertical = 6.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(accent.copy(alpha = 0.14f))
            .clickable(onClick = onAnswer)
            .padding(horizontal = 12.dp, vertical = 8.dp),
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
