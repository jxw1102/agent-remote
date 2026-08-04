package com.bb10d.remote.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckBox
import androidx.compose.material.icons.outlined.CheckBoxOutlineBlank
import androidx.compose.material.icons.outlined.RadioButtonChecked
import androidx.compose.material.icons.outlined.RadioButtonUnchecked
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.bb10d.remote.data.QuestionDto
import com.bb10d.remote.ui.markdown.MarkdownText
import com.bb10d.remote.ui.theme.palette

/**
 * The agent's own selection panel, mirrored to the phone.
 *
 * On the host this is a blocking TUI panel; the daemon parks the questions and
 * drives the real keystrokes once an answer arrives. That means two things
 * matter here: the answer must be one label list per question (the daemon maps
 * labels back to option positions), and cancelling must be possible — Escape
 * is a valid answer that lets the turn continue.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QuestionSheet(
    questions: List<QuestionDto>,
    accent: Color,
    onAnswer: (answers: List<List<String>>, notes: List<String>) -> Unit,
    onCancel: () -> Unit,
) {
    if (questions.isEmpty()) return
    val pal = palette
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    // One selection set + one note per question, keyed by the question list so
    // a second AskUserQuestion in the same turn starts clean.
    val picks = remember(questions) {
        List(questions.size) { mutableStateListOf<String>() }
    }
    val notes = remember(questions) {
        mutableStateListOf<String>().apply { questions.forEach { add("") } }
    }

    val complete = questions.indices.all { picks[it].isNotEmpty() }

    ModalBottomSheet(
        onDismissRequest = onCancel,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .imePadding(),
        ) {
            Column(
                Modifier
                    .weight(1f, fill = false)
                    .heightIn(max = 560.dp)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp),
            ) {
                Text(
                    text = if (questions.size == 1) "The agent is asking"
                    else "The agent is asking ${questions.size} things",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = "The turn is paused until you answer or cancel.",
                    style = MaterialTheme.typography.bodySmall,
                    color = pal.dim,
                )
                Spacer(Modifier.height(16.dp))

                questions.forEachIndexed { index, question ->
                    if (index > 0) {
                        Spacer(Modifier.height(20.dp))
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .height(1.dp)
                                .background(pal.hairline),
                        )
                        Spacer(Modifier.height(16.dp))
                    }
                    QuestionBlock(
                        question = question,
                        accent = accent,
                        selected = picks[index],
                        note = notes[index],
                        onNote = { notes[index] = it },
                        onToggle = { label ->
                            val set = picks[index]
                            if (question.multiSelect) {
                                if (set.contains(label)) set.remove(label) else set.add(label)
                            } else {
                                set.clear()
                                set.add(label)
                            }
                        },
                    )
                }
                Spacer(Modifier.height(20.dp))
            }

            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = onCancel) { Text("Cancel", color = pal.dim) }
                Spacer(Modifier.width(12.dp))
                Button(
                    onClick = {
                        onAnswer(picks.map { it.toList() }, notes.toList())
                    },
                    enabled = complete,
                ) { Text("Send answer") }
            }
        }
    }
}

@Composable
private fun QuestionBlock(
    question: QuestionDto,
    accent: Color,
    selected: List<String>,
    note: String,
    onNote: (String) -> Unit,
    onToggle: (String) -> Unit,
) {
    val pal = palette
    if (question.header.isNotBlank()) {
        Text(
            text = question.header,
            style = MaterialTheme.typography.labelMedium,
            color = accent,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(6.dp))
    }
    if (question.question.isNotBlank()) {
        // Grok sends whole plan documents through this channel, so the body is
        // markdown, not a one-liner.
        MarkdownText(source = question.question)
        Spacer(Modifier.height(10.dp))
    }
    if (question.multiSelect) {
        Text(
            text = "Pick as many as apply",
            style = MaterialTheme.typography.labelSmall,
            color = pal.dim,
        )
        Spacer(Modifier.height(6.dp))
    }
    question.options.forEach { option ->
        val active = selected.contains(option.label)
        Row(
            Modifier
                .fillMaxWidth()
                .padding(vertical = 3.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(if (active) accent.copy(alpha = 0.12f) else Color.Transparent)
                .border(
                    1.dp,
                    if (active) accent.copy(alpha = 0.5f) else pal.hairline,
                    RoundedCornerShape(10.dp),
                )
                .clickable { onToggle(option.label) }
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Icon(
                imageVector = when {
                    question.multiSelect && active -> Icons.Outlined.CheckBox
                    question.multiSelect -> Icons.Outlined.CheckBoxOutlineBlank
                    active -> Icons.Outlined.RadioButtonChecked
                    else -> Icons.Outlined.RadioButtonUnchecked
                },
                contentDescription = null,
                tint = if (active) accent else pal.dim,
                modifier = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    text = option.label,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                if (option.description.isNotBlank()) {
                    Text(
                        text = option.description,
                        style = MaterialTheme.typography.bodySmall,
                        color = pal.dim,
                    )
                }
            }
        }
        // Some options take free text with the pick (grok's "Request changes"
        // becomes the revision note it then waits for).
        val wantsNote = question.noteFor.isNotBlank() && question.noteFor == option.label
        AnimatedVisibility(visible = wantsNote && active) {
            OutlinedTextField(
                value = note,
                onValueChange = onNote,
                placeholder = { Text(question.noteHint.ifBlank { "Your answer" }) },
                minLines = 2,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 6.dp, bottom = 4.dp),
            )
        }
    }
}
