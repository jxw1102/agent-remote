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
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.foundation.layout.size
import kotlinx.coroutines.launch
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.foundation.layout.ime
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.DisposableEffect
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


/**
 * The TUI face of a session window: the host tmux pane for an Interactive
 * session, with keys and line text sent through agentremoted. It sits in the
 * session window in place of the chat (TranscriptScreen), not on a screen
 * of its own.
 */
/** The terminal's scrollback as fetched so far. */
private data class TuiHistory(
    val text: String = "",
    val lines: Int = 0,
    val total: Int = 0,
    val more: Boolean = true,
    val loading: Boolean = false,
    /** Just prepended: the next size change shifts the scroll to match. */
    val anchor: Boolean = false,
)

@Composable
fun LiveTuiPane(
    repo: AgentRepository,
    ref: SessionRef,
    /** The session's draft: the same text as the chat's message box. */
    line: String,
    onLine: (String) -> Unit,
    modifier: Modifier = Modifier,
    /** The floating bar over the top: the terminal runs under it, its text starts below. */
    topInset: androidx.compose.ui.unit.Dp = 0.dp,
) {
    val pal = palette
    // The stream and what it last showed are held by the app (TuiFeeds), so
    // leaving this page and coming back finds the pane already there.
    val feed = remember(ref.key) { repo.tuiFeeds.flowFor(ref) }
    DisposableEffect(ref.key) {
        repo.tuiFeeds.acquire(ref)
        onDispose { repo.tuiFeeds.release(ref) }
    }
    val view by feed.collectAsState()
    val text = view.text
    val status = view.status
    val live = view.live
    val seq = view.seq
    val cols = view.cols
    var keyError by remember { mutableStateOf<String?>(null) }
    val error = keyError ?: view.error
    val scroll = rememberScrollState()
    // The bottom of a terminal is where it is used (the prompt, the cursor),
    // so the pane keeps to its bottom: on the first frame, on every frame
    // while the reader is there, and whenever the keyboard comes up and
    // squeezes the pane. Scrolling up to read stops that until the reader
    // comes back down.
    var stick by remember(ref.key) { mutableStateOf(true) }
    LaunchedEffect(scroll) {
        snapshotFlow { scroll.isScrollInProgress to (scroll.value >= scroll.maxValue - 24) }
            .collect { (moving, atBottom) -> if (moving) stick = atBottom else if (atBottom) stick = true }
    }
    LaunchedEffect(scroll.maxValue, text, stick) {
        if (stick) scroll.scrollTo(scroll.maxValue)
    }
    // Scrollback: scrolling the terminal to its top asks the daemon for the
    // lines above the screen, 200 more each time, while there are more.
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var history by remember(ref.key) { mutableStateOf(TuiHistory()) }
    val historyHeight = remember(ref.key) { intArrayOf(0) }
    val canHistory = repo.profile(ref.profileId)?.caps?.tuiHistory == true
    fun loadHistory(lines: Int, anchor: Boolean, keep: Int = 0) {
        if (history.loading) return
        history = history.copy(loading = true)
        scope.launch {
            val client = repo.client(ref.profileId)
            val got = client?.let { runCatching { it.tuiHistory(ref.sessionId, lines) }.getOrNull() }
            history = if (got == null || !got.attached) history.copy(loading = false, more = false)
            else {
                // A refresh keeps exactly what was shown plus what has
                // scrolled off since, so nothing above moves.
                val rows = got.text.split('\n')
                val wanted = if (keep > 0) (keep + (got.history - history.total)).coerceIn(0, rows.size) else rows.size
                TuiHistory(
                    text = rows.takeLast(wanted).joinToString("\n"),
                    lines = wanted,
                    total = got.history,
                    more = wanted < got.history,
                    anchor = anchor,
                )
            }
        }
    }
    val atTop by remember { androidx.compose.runtime.derivedStateOf { scroll.value <= 4 } }
    LaunchedEffect(atTop, seq, canHistory) {
        if (atTop && canHistory && seq > 0L && history.more && !history.loading) {
            loadHistory(history.lines + 200, anchor = true)
        }
    }
    // While scrollback is shown, new output scrolling off the screen joins it.
    LaunchedEffect(seq) {
        if (history.lines == 0 || !canHistory) return@LaunchedEffect
        delay(1200)
        loadHistory(minOf(history.lines + 400, 5000), anchor = false, keep = history.lines)
    }
    val density = androidx.compose.ui.platform.LocalDensity.current
    val imeBottom = androidx.compose.foundation.layout.WindowInsets.ime.getBottom(density)
    val lastIme = remember { intArrayOf(0) }
    LaunchedEffect(imeBottom) {
        val rising = imeBottom > lastIme[0]
        lastIme[0] = imeBottom
        if (rising) {
            stick = true
            scroll.scrollTo(scroll.maxValue)
        }
    }

    // Keys are a stream: one sender, in the order they were pressed. Each tap
    // used to launch its own request, and two taps could arrive swapped.
    val outbox = remember { Channel<Pair<List<String>, String>>(Channel.UNLIMITED) }

    fun sendKeys(keys: List<String> = emptyList(), body: String = "") {
        repo.tuiFeeds.touch(ref)
        outbox.trySend(keys to body)
    }

    LaunchedEffect(ref.key) {
        val client = repo.client(ref.profileId) ?: return@LaunchedEffect
        for ((keys, body) in outbox) {
            runCatching { client.tuiKeys(ref.sessionId, keys, body) }
                .onFailure { keyError = repo.reason(it) }
                .onSuccess { keyError = null }
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
            // The page's own background, as on the chat face; the keys and
            // the text line below carry the controls' surface themselves.
            .background(MaterialTheme.colorScheme.background),
    ) {
        // Whether the pane is live is shown in the session bar's card
        // (TranscriptScreen), folded in like the chat's live strip.
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
                    modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = topInset + 14.dp),
                )
            } else Column(
                Modifier
                    .fillMaxSize()
                    .verticalScroll(scroll)
                    .horizontalScroll(rememberScrollState())
                    .padding(start = 8.dp, end = 8.dp, top = topInset + 6.dp, bottom = 6.dp),
            ) {
                // What scrolled off the top of the terminal, fetched when the
                // reader scrolls up to the top (newest just above the screen).
                if (history.text.isNotEmpty()) AnsiText(
                    text = history.text,
                    modifier = Modifier.onSizeChanged { size ->
                        val grew = size.height - historyHeight[0]
                        historyHeight[0] = size.height
                        // Older lines went in above: keep the reader's line
                        // under their thumb.
                        if (history.anchor && grew > 0) {
                            history = history.copy(anchor = false)
                            scope.launch { scroll.scrollTo(scroll.value + grew) }
                        }
                    },
                    defaultColor = tuiFg,
                    light = tuiLight,
                    fontFamily = FontFamily.Monospace,
                    fontSize = size,
                    lineHeight = size * 1.3f,
                )
                AnsiText(
                    text = text,
                    defaultColor = tuiFg,
                    light = tuiLight,
                    fontFamily = FontFamily.Monospace,
                    fontSize = size,
                    lineHeight = size * 1.3f,
                )
            }
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
        // the row: none is ever scrolled out of sight. The keyboard button
        // in the text line puts the row away or brings it back.
        val settings by repo.settings.collectAsState()
        val keysShown = settings.tuiKeys
        if (keysShown) Row(
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
            onLine("")
        }
        Row(
            Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surface)
                .padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            androidx.compose.material3.IconToggleButton(
                checked = keysShown,
                onCheckedChange = { on -> scope.launch { repo.settingsStore.setTuiKeys(on) } },
            ) {
                androidx.compose.material3.Icon(
                    androidx.compose.material.icons.Icons.Outlined.Keyboard,
                    contentDescription = if (keysShown) "Hide terminal keys" else "Show terminal keys",
                    tint = if (keysShown) MaterialTheme.colorScheme.primary else pal.dim,
                )
            }
            OutlinedTextField(
                value = line,
                onValueChange = onLine,
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
            // An icon, as in the chat's message box.
            androidx.compose.material3.FilledIconButton(
                onClick = { submitLine() },
                enabled = line.isNotBlank(),
                modifier = Modifier.size(48.dp),
            ) {
                androidx.compose.material3.Icon(
                    androidx.compose.material.icons.Icons.AutoMirrored.Outlined.Send,
                    contentDescription = "Send the line",
                )
            }
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
