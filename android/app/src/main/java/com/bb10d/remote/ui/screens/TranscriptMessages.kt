package com.bb10d.remote.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.History
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.bb10d.remote.ui.components.rememberClip
import com.bb10d.remote.ui.markdown.MarkdownText
import com.bb10d.remote.data.StepDto
import com.bb10d.remote.ui.theme.MonoStyle
import com.bb10d.remote.ui.theme.palette

// The transcript rows: messages, and the working steps under them.

@Composable
internal fun LoadOlderRow(loading: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.Center,
    ) {
        if (loading) {
            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
        } else {
            TextButton(onClick = onClick) {
                Icon(Icons.Outlined.History, contentDescription = null)
                Spacer(Modifier.width(6.dp))
                Text("Load earlier messages")
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun MessageRow(
    item: TranscriptItem,
    accent: Color,
    rich: Boolean,
    onLongPress: () -> Unit,
    openSteps: Map<String, String> = emptyMap(),
    onToggleStep: (StepDto) -> Unit = {},
) {
    Column(Modifier.fillMaxWidth()) {
        MessageBody(item, accent, rich, onLongPress)
        if (item.steps.isNotEmpty()) {
            StepsStrip(item.steps, accent, openSteps, onToggleStep)
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun MessageBody(
    item: TranscriptItem,
    accent: Color,
    rich: Boolean,
    onLongPress: () -> Unit,
) {
    val pal = palette
    val clipboard = rememberClip()
    when (item.role) {
        "user" -> Row(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp))
                .background(pal.userWell)
                .combinedClickable(
                    onClick = {},
                    onLongClick = onLongPress,
                )
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Text(
                text = "›",
                color = accent,
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.width(10.dp))
            Text(
                text = item.text,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }

        "status" -> Row(
            Modifier
                .fillMaxWidth()
                .padding(start = 4.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Box(
                Modifier
                    .padding(top = 4.dp)
                    .width(2.dp)
                    .height(14.dp)
                    .background(pal.thought.copy(alpha = 0.5f)),
            )
            Spacer(Modifier.width(10.dp))
            Text(
                text = item.text,
                style = MaterialTheme.typography.bodySmall,
                color = pal.thought,
                fontFamily = if (item.metaKind == "worked") FontFamily.Monospace else null,
            )
        }

        "notice" -> Text(
            text = item.text,
            style = MaterialTheme.typography.bodySmall,
            color = if (item.severity == "error") pal.danger else pal.dim,
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .background(
                    if (item.severity == "error") pal.danger.copy(alpha = 0.10f)
                    else pal.liveWell,
                )
                .padding(horizontal = 12.dp, vertical = 8.dp),
        )

        else -> Box(Modifier.fillMaxWidth()) {
            SelectionContainer {
                // "Rich text off" is the escape hatch for output this parser
                // gets wrong: the raw text, exactly as the agent wrote it.
                if (rich) {
                    MarkdownText(source = item.text)
                } else {
                    Text(
                        text = item.text,
                        style = MonoStyle,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        }
    }
}

/**
 * Process view: the working steps under one message — tool calls (▸), their
 * results (↳) and thinking (✻), in the order they happened. Tapping a row
 * expands the preview; a truncated body is fetched from the daemon on first
 * expand only (mirrors the web client).
 */
@Composable
internal fun StepsStrip(
    steps: List<StepDto>,
    accent: Color,
    openSteps: Map<String, String>,
    onToggleStep: (StepDto) -> Unit,
) {
    val pal = palette
    Column(
        Modifier
            .fillMaxWidth()
            .padding(top = 4.dp, start = 4.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        steps.forEach { step ->
            val isError = step.kind == "tool_result" && !step.ok
            val silent = step.kind == "thinking" && !step.recorded
            val mark = when (step.kind) {
                "tool_use" -> "▸"
                "tool_result" -> "↳"
                "thinking" -> "✻"
                else -> "·"
            }
            val title = when (step.kind) {
                "tool_use" -> step.name.ifBlank { "tool" }
                "tool_result" -> if (isError) "error" else "result"
                "thinking" -> "thinking"
                else -> step.kind
            }
            val detail = step.detail.ifBlank {
                step.preview.lineSequence().firstOrNull().orEmpty().take(120)
            }
            Column(Modifier.fillMaxWidth()) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(6.dp))
                        .then(
                            if (silent) Modifier
                            else Modifier.clickable { onToggleStep(step) },
                        )
                        .padding(horizontal = 6.dp, vertical = 3.dp),
                    verticalAlignment = Alignment.Top,
                ) {
                    Text(
                        text = mark,
                        style = MaterialTheme.typography.labelSmall,
                        color = if (isError) pal.danger else accent.copy(alpha = 0.8f),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = title,
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = if (isError) pal.danger else pal.dim,
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = if (silent) "not recorded by this CLI" else detail,
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = pal.thought,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    if (step.bytes > 0) {
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = humanSize(step.bytes),
                            style = MaterialTheme.typography.labelSmall,
                            color = pal.dim,
                        )
                    }
                }
                if (step.ref in openSteps) {
                    val full = openSteps[step.ref].orEmpty()
                    val body = full.ifEmpty { step.preview }
                    val pending = full.isEmpty() && step.truncated
                    SelectionContainer {
                        Text(
                            text = body + (if (pending) "\n…" else ""),
                            style = MonoStyle.copy(fontSize = 12.sp),
                            color = MaterialTheme.colorScheme.onSurface,
                            softWrap = false,
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(start = 18.dp, top = 2.dp, bottom = 4.dp)
                                .clip(RoundedCornerShape(6.dp))
                                .background(pal.codeBg)
                                .horizontalScroll(rememberScrollState())
                                .padding(8.dp),
                        )
                    }
                }
            }
        }
    }
}

internal fun humanSize(bytes: Long): String = when {
    bytes >= 1024 * 1024 -> "%.1f MB".format(bytes / (1024.0 * 1024.0))
    bytes >= 1024 -> "%.1f KB".format(bytes / 1024.0)
    else -> "$bytes B"
}
