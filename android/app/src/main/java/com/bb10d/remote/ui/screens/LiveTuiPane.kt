package com.bb10d.remote.ui.screens

import androidx.compose.material.icons.outlined.ArrowUpward
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
import androidx.compose.material.icons.outlined.Stop
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
/** Scrollback is read a page of this many lines at a time. */
private const val HISTORY_PAGE = 200
/** Lines of scrollback kept on the page; older are not asked for. */
private const val HISTORY_MAX = 20000

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
    /** The keys and the text line's height, for what floats above them. */
    onControlsHeight: (Int) -> Unit = {},
    /** A turn is running: the text line shows Stop, as the chat's box does. */
    running: Boolean = false,
    onStop: () -> Unit = {},
    /**
     * Set on a daemon that picks the mode itself: with no terminal for the
     * session yet, the first line sent starts one through this.
     */
    onStartTerminal: ((String) -> Unit)? = null,
    /** The terminal is there (true once a live frame has come). */
    onTerminal: (Boolean) -> Unit = {},
    /** The session's accent, as on the chat's Send. */
    accentColor: Color? = null,
    onAccentColor: Color? = null,
) {
    val pal = palette
    val accent = accentColor ?: pal.accent
    val onAccent = onAccentColor ?: pal.onAccent
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
    LaunchedEffect(view.live) { onTerminal(view.live) }
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
    // One request carries one page, never the whole history again. `older`
    // asks for the page before what is shown and puts it on top; otherwise
    // it asks for what has scrolled off the screen since, and joins it below.
    fun loadHistory(older: Boolean) {
        if (history.loading) return
        val had = history
        history = had.copy(loading = true)
        scope.launch {
            val client = repo.client(ref.profileId)
            val got = client?.let {
                runCatching {
                    if (older && had.lines > 0) it.tuiHistory(ref.sessionId, HISTORY_PAGE, had.lines, had.total)
                    else it.tuiHistory(ref.sessionId, HISTORY_PAGE)
                }.getOrNull()
            }
            history = if (got == null || !got.attached) history.copy(loading = false, more = false)
            else {
                val rows = if (got.text.isEmpty()) emptyList() else got.text.split('\n')
                if (older) {
                    // What scrolled in since the last reply sits between this
                    // page and the screen (the daemon skipped it); the
                    // refresh fetches it.
                    val lines = had.lines + rows.size
                    had.copy(
                        text = when {
                            rows.isEmpty() -> had.text
                            had.text.isEmpty() -> rows.joinToString("\n")
                            else -> rows.joinToString("\n") + "\n" + had.text
                        },
                        lines = lines,
                        total = if (had.total == 0) got.history else had.total,
                        more = got.more && lines < HISTORY_MAX,
                        loading = false,
                        anchor = rows.isNotEmpty(),
                    )
                } else {
                    val grown = got.history - had.total
                    when {
                        grown in 1..rows.size -> {
                            val add = rows.takeLast(grown)
                            had.copy(
                                text = if (had.text.isEmpty()) add.joinToString("\n")
                                else had.text + "\n" + add.joinToString("\n"),
                                lines = had.lines + add.size,
                                total = got.history,
                                loading = false,
                                anchor = false,
                            )
                        }
                        // More than a page scrolled off at once: start again
                        // from this newest page rather than show a gap.
                        grown > rows.size -> TuiHistory(
                            text = rows.joinToString("\n"),
                            lines = rows.size,
                            total = got.history,
                            more = got.more,
                        )
                        else -> had.copy(total = got.history, loading = false, anchor = false)
                    }
                }
            }
        }
    }
    val atTop by remember { androidx.compose.runtime.derivedStateOf { scroll.value <= 4 } }
    LaunchedEffect(atTop, seq, canHistory) {
        if (atTop && canHistory && seq > 0L && history.more && !history.loading) {
            loadHistory(older = true)
        }
    }
    // While scrollback is shown, new output scrolling off the screen joins it.
    LaunchedEffect(seq) {
        if (history.lines == 0 || !canHistory) return@LaunchedEffect
        delay(1200)
        loadHistory(older = false)
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
        // The bottom bar, the same in both of the session's faces: one
        // floating card, a row of small controls on top (the chat's chips
        // there, the terminal keys here) and under it the text field with
        // Stop and Send. Same card, same field, same buttons in the same
        // places, so switching Chat and TUI moves nothing.
        val settings by repo.settings.collectAsState()
        val keysShown = settings.tuiKeys
        fun submitLine() {
            val t = line.trim()
            if (t.isEmpty()) return
            // The daemon says the session has no terminal (none yet, or the
            // one it had is gone): start one with this line. Merely not
            // connected yet is not that; then the line is keys, as ever.
            if (onStartTerminal != null && view.detached) onStartTerminal(t)
            else sendKeys(listOf("Enter"), t)
            onLine("")
        }
        androidx.compose.material3.Surface(
            shape = RoundedCornerShape(18.dp),
            color = MaterialTheme.colorScheme.surface,
            border = androidx.compose.foundation.BorderStroke(1.dp, pal.hairline),
            shadowElevation = 8.dp,
            modifier = Modifier
                .onSizeChanged { onControlsHeight(it.height) }
                .padding(start = 10.dp, end = 10.dp, bottom = 8.dp),
        ) {
        Column(Modifier.fillMaxWidth()) {
            // The terminal keys, where the chat has its chips. All eight
            // share the row; the keyboard button in the field puts the row
            // away or brings it back.
            if (keysShown) Row(
                Modifier
                    .fillMaxWidth()
                    .padding(start = 8.dp, end = 8.dp, top = 8.dp),
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
            // The chat row's own insets, so Send is clear of the card's edge
            // and in the same place in both faces.
            Row(
                Modifier.padding(start = 8.dp, end = 8.dp, top = 2.dp, bottom = 4.dp),
                verticalAlignment = Alignment.Bottom,
            ) {
                // The terminal keys toggle, at the start of the bar where the
                // chat has its + (the same round button in the same place).
                com.bb10d.remote.ui.components.CircleButton(
                    selected = keysShown,
                    onClick = { scope.launch { repo.settingsStore.setTuiKeys(!keysShown) } },
                    modifier = Modifier.padding(bottom = 8.dp),
                    color = if (keysShown) accent.copy(alpha = 0.16f)
                    else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.07f),
                ) {
                    androidx.compose.material3.Icon(
                        androidx.compose.material.icons.Icons.Outlined.Keyboard,
                        contentDescription = if (keysShown) "Hide terminal keys" else "Show terminal keys",
                        tint = if (keysShown) accent else pal.dim,
                    )
                }
                // The chat's own field: no frame (the card is the frame),
                // the same type, the same height.
                androidx.compose.material3.TextField(
                    value = line,
                    onValueChange = onLine,
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    placeholder = {
                        Text("Type into the terminal", maxLines = 1,
                            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                    },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { submitLine() }),
                    colors = androidx.compose.material3.TextFieldDefaults.colors(
                        focusedContainerColor = Color.Transparent,
                        unfocusedContainerColor = Color.Transparent,
                        focusedIndicatorColor = Color.Transparent,
                        unfocusedIndicatorColor = Color.Transparent,
                    ),
                )
                // Stop keeps its slot, as in the chat's box, so Send never
                // slides when a turn starts.
                com.bb10d.remote.ui.components.CircleButton(
                    onClick = onStop,
                    enabled = running,
                    modifier = Modifier.padding(start = 4.dp, end = 4.dp, bottom = 8.dp),   // level with Send
                    color = if (running) pal.danger.copy(alpha = 0.10f) else Color.Transparent,
                ) {
                    androidx.compose.material3.Icon(
                        androidx.compose.material.icons.Icons.Outlined.Stop,
                        contentDescription = "Stop",
                        tint = if (running) pal.danger else Color.Transparent,
                    )
                }
                val canSend = line.isNotBlank()
                com.bb10d.remote.ui.components.CircleButton(
                    onClick = { submitLine() },
                    enabled = canSend,
                    modifier = Modifier.padding(bottom = 8.dp),
                    color = if (canSend) accent
                    else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.07f),
                ) {
                    androidx.compose.material3.Icon(
                        androidx.compose.material.icons.Icons.Outlined.ArrowUpward,
                        contentDescription = "Send the line",
                        tint = if (canSend) onAccent else pal.dim,
                    )
                }
            }
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
