package com.bb10d.remote.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.bb10d.remote.ui.theme.palette

// Where the next message is written, and what it will be sent with.

/**
 * Uploaded files as removable chips above the composer. Kept out of the
 * TextField on purpose: the old design spliced `[attached: …]` into the
 * field text and the async update raced TextField state (markers vanished).
 */
@Composable
internal fun AttachmentChips(
    chips: List<TranscriptViewModel.ComposerAttachment>,
    onRemove: (String) -> Unit,
) {
    if (chips.isEmpty()) return
    val pal = palette
    Row(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface)
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 10.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        chips.forEach { chip ->
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .border(1.dp, pal.hairline, RoundedCornerShape(8.dp))
                    .padding(start = 8.dp),
            ) {
                if (chip.uploading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(12.dp),
                        strokeWidth = 1.5.dp,
                        color = pal.dim,
                    )
                    Spacer(Modifier.width(6.dp))
                }
                Text(
                    chip.name,
                    style = MaterialTheme.typography.labelSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.widthIn(max = 180.dp),
                )
                IconButton(
                    onClick = { onRemove(chip.id) },
                    modifier = Modifier.size(28.dp),
                ) {
                    Icon(
                        Icons.Outlined.Close,
                        contentDescription = "Remove attachment",
                        tint = pal.dim,
                        modifier = Modifier.size(14.dp),
                    )
                }
            }
        }
    }
}

@Composable
internal fun Composer(
    text: String,
    onText: (String) -> Unit,
    canSend: Boolean,
    running: Boolean,
    interactive: Boolean,
    accent: Color,
    onAccent: Color,
    onSend: () -> Unit,
    onStop: () -> Unit,
    onAttach: () -> Unit,
) {
    val pal = palette
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surface)
            .navigationBarsPadding(),
    ) {
        HorizontalDivider(color = pal.hairline)
        Row(
            Modifier.padding(horizontal = 6.dp, vertical = 6.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            IconButton(onClick = onAttach) {
                Icon(Icons.Outlined.Add, contentDescription = "Attach a file", tint = pal.dim)
            }
            TextField(
                value = text,
                onValueChange = onText,
                placeholder = {
                    Text(
                        text = when {
                            running && interactive -> "Type into the session…"
                            running -> "Queue a follow-up…"
                            else -> "Message, /command or !shell"
                        },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                maxLines = 6,
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = Color.Transparent,
                    unfocusedContainerColor = Color.Transparent,
                    focusedIndicatorColor = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                ),
                modifier = Modifier
                    .weight(1f)
                    .heightIn(max = 160.dp),
            )
            // Always reserve the Stop slot so Send never slides when a turn
            // starts — same mis-click class as web Live TUI vs Stop.
            IconButton(
                onClick = onStop,
                enabled = running,
            ) {
                Icon(
                    Icons.Outlined.Stop,
                    contentDescription = "Stop",
                    tint = if (running) pal.danger else Color.Transparent,
                )
            }
            IconButton(
                onClick = onSend,
                enabled = canSend,
                modifier = Modifier
                    .padding(bottom = 4.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (canSend) accent else Color.Transparent),
            ) {
                Icon(
                    Icons.AutoMirrored.Outlined.Send,
                    contentDescription = "Send",
                    tint = if (canSend) onAccent else pal.dim,
                )
            }
        }
    }
}
