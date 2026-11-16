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
import androidx.compose.material3.MaterialTheme
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
