package com.bb10d.remote.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.PlaylistAdd
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.bb10d.remote.data.Profile
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
    // The field keeps its own caret. Text that changes from outside (a
    // command filled in from the list, a draft restored) puts the caret at
    // the end; with a plain String the caret stayed where it was, in the
    // middle of the command just inserted.
    var field by remember { mutableStateOf(TextFieldValue(text, TextRange(text.length))) }
    // What was typed here and not yet seen coming back as `text`. `text`
    // arrives a moment after it is sent up, so it can still be "a" when the
    // field already says "ab"; taking that for an outside change put "a" back
    // and dropped the "b" (and broke an IME composition).
    val sentUp = remember { ArrayDeque<String>() }
    LaunchedEffect(text) {
        val at = sentUp.indexOf(text)
        if (at >= 0) {
            repeat(at + 1) { sentUp.removeFirst() }
            return@LaunchedEffect
        }
        sentUp.clear()
        if (text != field.text) field = TextFieldValue(text, TextRange(text.length))
    }
    val queues = running && !interactive
    // Inside the floating card: one piece with it. No surface or system bar
    // padding of its own (the card and the page give those); + and Send are
    // round, like the bubbles and the bar's switch.
    Column(Modifier.fillMaxWidth()) {
        // + and Send stand clear of the card's edge (8 dp at the sides, 11 dp
        // at the foot) and centre on a one-line field.
        Row(
            Modifier.padding(start = 8.dp, end = 8.dp, top = 2.dp, bottom = 4.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            IconButton(
                onClick = onAttach,
                modifier = Modifier
                    .padding(bottom = 8.dp)
                    .size(40.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.onSurface.copy(alpha = 0.07f)),
            ) {
                Icon(Icons.Outlined.Add, contentDescription = "Attach a file", tint = pal.dim)
            }
            TextField(
                value = field,
                onValueChange = {
                    field = it
                    if (it.text != text || sentUp.isNotEmpty()) {
                        if (sentUp.lastOrNull() != it.text) {
                            if (sentUp.size >= 16) sentUp.removeFirst()
                            sentUp.addLast(it.text)
                        }
                        onText(it.text)
                    }
                },
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
                modifier = Modifier.padding(bottom = 4.dp),   // level with Send
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
                    .padding(bottom = 7.dp)
                    .size(42.dp)
                    .clip(CircleShape)
                    .background(if (canSend) accent else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.07f)),
            ) {
                // Says what it will do: while a headless turn runs, the
                // message waits its turn on the daemon.
                Icon(
                    if (queues) Icons.AutoMirrored.Outlined.PlaylistAdd
                    else Icons.AutoMirrored.Outlined.Send,
                    contentDescription = if (queues) "Queue after this turn" else "Send",
                    tint = if (canSend) onAccent else pal.dim,
                )
            }
        }
    }
}

/** The harness's list, plus whatever is selected now if it is not on it. */
private fun List<String>.withCurrent(current: String): List<String> =
    if (current.isNotBlank() && current !in this) this + current else this

/** True when [TurnChips] would draw something for this profile. */
internal fun hasTurnChips(profile: Profile, harness: String?): Boolean {
    val caps = profile.caps
    return (caps.canSetModelFor(harness) && caps.modelsFor(harness).isNotEmpty()) ||
        (caps.canSetEffortFor(harness) && caps.effortsFor(harness).isNotEmpty())
}

/**
 * Model and effort for the next message, as chips above the composer.
 *
 * These belong to the OPEN SESSION, not to the daemon: [model] and [effort]
 * are what this one session runs on, and a pick is pinned to it alone. They
 * used to be one value per daemon, which showed a model the open session was
 * not running on and moved every other session when you changed it.
 *
 * Execution is NOT here: it sits in the session options sheet, where the
 * BlackBerry client has always kept it. It used to be the first chip in
 * this row, because a sheet is how a turn once got sent headless by someone
 * who thought it was interactive — the sheet now spells the mode out in
 * words instead of hiding it behind an icon.
 *
 * The values shown are the ones [TranscriptViewModel.send] will use.
 */
@Composable
internal fun TurnChips(
    profile: Profile,
    harness: String?,
    model: String,
    effort: String,
    perSession: Boolean,
    onModel: (String) -> Unit,
    onEffort: (String) -> Unit,
) {
    // Nothing to offer: no divider and no empty strip above the message box.
    if (!hasTurnChips(profile, harness)) return
    val caps = profile.caps
    // A session can run on a model the harness no longer lists (an older id,
    // a [1m] variant). Show it anyway — it is what the next turn will use.
    val models = caps.modelsFor(harness).withCurrent(model)
    val efforts = caps.effortsFor(harness).withCurrent(effort)
    val scope = if (perSession) "This session only"
                else "Used for every session on ${profile.displayName}"
    // No surface or rule of its own: it sits inside the floating card.
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(start = 10.dp, end = 10.dp, top = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (caps.canSetModelFor(harness) && models.isNotEmpty()) {
                TurnChip(
                    label = "Model",
                    options = models,
                    selected = model.ifBlank { models.first() },
                    footer = scope,
                    onSelect = onModel,
                )
            }
            if (caps.canSetEffortFor(harness) && efforts.isNotEmpty()) {
                TurnChip(
                    label = "Effort",
                    options = efforts,
                    selected = effort.ifBlank { efforts.first() },
                    footer = scope,
                    onSelect = onEffort,
                )
            }
        }
    }
}

@Composable
internal fun TurnChip(
    label: String,
    options: List<String>,
    selected: String,
    footer: String,
    onSelect: (String) -> Unit,
    display: (String) -> String = { it },
    hint: (String) -> String = { "" },
) {
    val pal = palette
    var open by remember { mutableStateOf(false) }
    val canChange = options.size > 1
    val shown = display(selected).ifBlank { "default" }
    Box {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                // 40dp of touch height around a 28dp pill.
                .heightIn(min = 40.dp)
                .clip(RoundedCornerShape(20.dp))
                .then(
                    if (canChange) Modifier.clickable(
                        role = Role.DropdownList,
                        onClickLabel = "change",
                    ) { open = true } else Modifier,
                )
                .semantics(mergeDescendants = true) {
                    contentDescription = "$label: $shown"
                }
                .padding(vertical = 6.dp)
                .clip(RoundedCornerShape(14.dp))
                .border(1.dp, pal.hairline, RoundedCornerShape(14.dp))
                .padding(start = 10.dp, end = if (canChange) 2.dp else 10.dp, top = 4.dp, bottom = 4.dp),
        ) {
            Text(label, style = MaterialTheme.typography.labelSmall, color = pal.dim)
            Spacer(Modifier.width(6.dp))
            Text(
                shown,
                style = MaterialTheme.typography.labelMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 170.dp),
            )
            if (canChange) {
                Icon(
                    Icons.Outlined.ArrowDropDown,
                    contentDescription = null,
                    tint = pal.dim,
                    modifier = Modifier.size(18.dp),
                )
            }
        }
        DropdownMenu(open, onDismissRequest = { open = false }) {
            options.forEach { option ->
                val line = hint(option)
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(display(option))
                            if (line.isNotEmpty()) {
                                Text(
                                    line,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = pal.dim,
                                )
                            }
                        }
                    },
                    trailingIcon = {
                        if (option == selected) {
                            Icon(Icons.Outlined.Check, contentDescription = "selected", tint = pal.accent)
                        }
                    },
                    onClick = {
                        open = false
                        if (option != selected) onSelect(option)
                    },
                )
            }
            Text(
                footer,
                style = MaterialTheme.typography.labelSmall,
                color = pal.dim,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
            )
        }
    }
}

/** Commands worth offering for what is typed so far; empty when none apply. */
internal fun slashMatches(text: String, known: List<String>): List<String> {
    if (!Regex("^/[A-Za-z0-9_-]*$").matches(text)) return emptyList()
    val all = (known + listOf("/rewind", "/goal")).distinct()
    val hits = all.filter { it.startsWith(text, ignoreCase = true) }
    // The only match is already typed out in full: nothing left to offer.
    return if (hits.size == 1 && hits[0] == text) emptyList() else hits
}

/**
 * The session's commands, narrowed as a "/" is typed. Tapping one fills it
 * in; it is not sent, because some commands take an argument.
 */
@Composable
internal fun SlashSuggestions(
    matches: List<String>,
    headless: Boolean,
    onPick: (String) -> Unit,
) {
    if (matches.isEmpty()) return
    val pal = palette
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .heightIn(max = 220.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        matches.forEach { command ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .clickable(role = Role.Button, onClickLabel = "fill in") { onPick(command) }
                    .heightIn(min = 44.dp)
                    .padding(horizontal = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    command,
                    style = MaterialTheme.typography.bodyMedium,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier.weight(1f),
                )
                // /rewind is done by the daemon itself, in either mode.
                if (headless && command != "/rewind") {
                    Text(
                        "needs Interactive",
                        style = MaterialTheme.typography.labelSmall,
                        color = pal.dim,
                    )
                }
            }
        }
    }
}
