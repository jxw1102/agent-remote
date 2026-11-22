package com.bb10d.remote.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
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
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.SessionRef
import com.bb10d.remote.data.TuiFrameDto
import com.bb10d.remote.net.TuiStream
import com.bb10d.remote.ui.AnsiText
import com.bb10d.remote.ui.theme.palette
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.ui.platform.LocalDensity
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

/** The daemon answered 404 to the frame stream: it predates it. */
private class StreamUnsupported : Exception()

/**
 * The TUI face of a session window: the host tmux pane for an Interactive
 * session, with keys and line text sent through agentremoted. It sits in the
 * session window in place of the chat (TranscriptScreen), not on a screen
 * of its own.
 */
@Composable
fun LiveTuiPane(
    repo: AgentRepository,
    ref: SessionRef,
    modifier: Modifier = Modifier,
) {
    val pal = palette
    var text by remember { mutableStateOf("Connecting to host TUI…") }
    var status by remember { mutableStateOf("Host TUI") }
    var live by remember { mutableStateOf(false) }
    var line by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var seq by remember { mutableStateOf(0L) }
    // The host pane's width in columns (the daemon says; 120 before it does).
    var cols by remember { mutableStateOf(120) }
    val scroll = rememberScrollState()

    // Keys are a stream: one sender, in the order they were pressed. Each tap
    // used to launch its own request, and two taps could arrive swapped.
    val outbox = remember { Channel<Pair<List<String>, String>>(Channel.UNLIMITED) }

    fun sendKeys(keys: List<String> = emptyList(), body: String = "") {
        outbox.trySend(keys to body)
    }

    LaunchedEffect(ref.key) {
        val client = repo.client(ref.profileId) ?: return@LaunchedEffect
        for ((keys, body) in outbox) {
            runCatching { client.tuiKeys(ref.sessionId, keys, body) }
                .onFailure { error = repo.reason(it) }
                .onSuccess { error = null }
        }
    }

    fun show(frame: TuiFrameDto) {
        error = null
        if (!frame.attached) {
            live = false
            status = frame.error.ifBlank { "No host TUI attached" }
            if (seq == 0L) {
                text = frame.error.ifBlank {
                    "No interactive TUI for this session. Start a turn in Interactive mode."
                }
            }
        } else if (frame.seq != seq) {
            seq = frame.seq
            if (frame.cols > 0) cols = frame.cols
            live = true
            status = if (frame.jobId.isNotBlank()) {
                "Live · job ${frame.jobId.take(8)}"
            } else {
                "Live"
            }
            text = frame.text.ifBlank { "(empty pane)" }
        } else {
            live = true
        }
    }

    LaunchedEffect(ref.key) {
        val client = repo.client(ref.profileId) ?: return@LaunchedEffect
        // Daemon ≥ 2.22 pushes a frame when the pane changes; the echo of a
        // key is then one hop away instead of up to a 400 ms poll.
        if (repo.profile(ref.profileId)?.caps?.tuiStream == true) {
            try {
                TuiStream(client, ref.sessionId).events().collect { ev ->
                    when (ev) {
                        is TuiStream.Event.Frame -> show(ev.frame)
                        is TuiStream.Event.Down -> {
                            if (ev.unsupported) throw StreamUnsupported()
                            live = false
                            status = "Reconnecting…"
                        }
                    }
                }
            } catch (_: StreamUnsupported) {
                // Fall through to polling.
            }
        }
        // Older daemons: ask for the pane every 400 ms.
        while (isActive) {
            val frame = runCatching { client.tui(ref.sessionId) }.getOrElse {
                error = repo.reason(it)
                live = false
                status = "Error"
                delay(1200)
                continue
            }
            show(frame)
            delay(400)
        }
    }

    // The terminal takes the app's own theme: its background and text are
    // the screen's, and on a light theme the host's colours (picked for a
    // dark terminal) are adapted so they stay readable.
    val tuiBg = MaterialTheme.colorScheme.background
    val tuiFg = MaterialTheme.colorScheme.onBackground
    val tuiLight = tuiBg.luminance() > 0.5f
    Column(
        modifier
            // The strip under the text line belongs to the controls, not
            // to the terminal.
            .background(MaterialTheme.colorScheme.surface),
    ) {
        // Whether the pane is live: the one line of chrome this face adds to
        // the session window's own bar.
        Text(
            text = buildString {
                append(if (live) "● " else "")
                append(status)
            },
            style = MaterialTheme.typography.labelSmall,
            color = if (live) pal.ok else pal.dim,
            maxLines = 1,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp, vertical = 4.dp),
        )
        // weight must sit on a Column direct child — AnsiText wraps Text
        // in SelectionContainer, so weight on its modifier was ignored and
        // the pane grew to full content height, pushing the input off-screen.
        // The terminal first: it is the whole point of this screen. A TUI
        // is a grid, so it is never reflowed; the glyphs are scaled until
        // the host's own column count fits the screen, down to a floor,
        // after which the pane scrolls sideways (as on the web).
        BoxWithConstraints(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .background(tuiBg),
        ) {
            val density = LocalDensity.current
            val fit = with(density) {
                ((maxWidth - 16.dp).toPx() / (cols * MONO_GLYPH_WIDTH)).toSp()
            }
            val size = fit.value.coerceIn(TUI_FONT_MIN_SP, TUI_FONT_MAX_SP).sp
            if (seq == 0L) {
                // Not a frame yet: words about the terminal, at reading
                // size and in a colour that shows on the dark pane.
                Text(
                    text,
                    color = pal.dim,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
                )
            } else AnsiText(
                text = text,
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(scroll)
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                defaultColor = tuiFg,
                light = tuiLight,
                fontFamily = FontFamily.Monospace,
                fontSize = size,
                lineHeight = size * 1.3f,
            )
        }
        if (error != null) {
            Text(
                error!!,
                color = pal.danger,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surface)
                    .padding(horizontal = 12.dp, vertical = 4.dp),
            )
        }
        // Soft keys, under the terminal and above the text line, within
        // reach of the thumbs. Real chrome buttons (surface + border) so
        // they stay readable beside the near-black pane. All eight share
        // the row: none is ever scrolled out of sight.
        Row(
            Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surface)
                .padding(start = 8.dp, end = 8.dp, top = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            listOf(
                Triple("Esc", listOf("Escape"), 1f),
                Triple("Enter", listOf("Enter"), 1.5f),
                Triple("Tab", listOf("Tab"), 1f),
                Triple("\u2191", listOf("Up"), 1f),
                Triple("\u2193", listOf("Down"), 1f),
                Triple("\u2190", listOf("Left"), 1f),
                Triple("\u2192", listOf("Right"), 1f),
                Triple("^C", listOf("Ctrl+C"), 1f),
            ).forEach { (label, keys, share) ->
                LiveTuiKeyButton(
                    label = label,
                    onClick = { sendKeys(keys) },
                    modifier = Modifier.weight(share),
                )
            }
        }
        // Pinned input chrome — never inside the pane scroll.
        fun submitLine() {
            val t = line.trim()
            if (t.isEmpty()) return
            sendKeys(listOf("Enter"), t)
            line = ""
        }
        Row(
            Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surface)
                .padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = line,
                onValueChange = { line = it },
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = 52.dp),
                singleLine = true,
                placeholder = { Text("Type a line into the TUI") },
                textStyle = MaterialTheme.typography.bodyMedium.copy(
                    fontFamily = FontFamily.Monospace,
                ),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { submitLine() }),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = MaterialTheme.colorScheme.onSurface,
                    unfocusedTextColor = MaterialTheme.colorScheme.onSurface,
                    focusedBorderColor = MaterialTheme.colorScheme.primary,
                    unfocusedBorderColor = pal.hairline,
                    cursorColor = MaterialTheme.colorScheme.primary,
                    focusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                    unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                ),
            )
            Spacer(Modifier.width(8.dp))
            Button(onClick = { submitLine() }) { Text("Send") }
        }
    }
}

// Width of one monospace glyph as a fraction of the font size, and the
// range the terminal's font may be scaled within.
private const val MONO_GLYPH_WIDTH = 0.6f
private const val TUI_FONT_MIN_SP = 8f
private const val TUI_FONT_MAX_SP = 13f

/** Compact mono keycap for the Live TUI toolbar. */
@Composable
private fun LiveTuiKeyButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val pal = palette
    val shape = RoundedCornerShape(8.dp)
    Box(
        modifier = modifier
            .defaultMinSize(minHeight = 40.dp)
            .clip(shape)
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .border(1.dp, pal.hairline, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 4.dp, vertical = 8.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            color = MaterialTheme.colorScheme.onSurface,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
            fontFamily = FontFamily.Monospace,
            maxLines = 1,
            textAlign = TextAlign.Center,
        )
    }
}
