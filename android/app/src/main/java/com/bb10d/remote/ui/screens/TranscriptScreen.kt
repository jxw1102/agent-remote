package com.bb10d.remote.ui.screens

import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.alpha
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.ui.graphics.Brush
import androidx.compose.animation.core.tween
import androidx.compose.animation.togetherWith
import androidx.compose.animation.fadeOut
import androidx.compose.animation.fadeIn
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import kotlinx.coroutines.delay
import androidx.compose.foundation.layout.widthIn
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.foundation.interaction.collectIsDraggedAsState
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.BorderStroke
import androidx.compose.material3.Surface
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Difference
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.automirrored.outlined.PlaylistPlay
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.Tag
import androidx.compose.material.icons.outlined.Timeline
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material.icons.outlined.PowerSettingsNew
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.derivedStateOf
import com.bb10d.remote.ui.components.MetaPill
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import com.bb10d.remote.ui.components.connLabel
import com.bb10d.remote.ui.components.connTint
import com.bb10d.remote.ui.components.CircleButton
import com.bb10d.remote.ui.components.stateTint
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.data.ExecMode
import com.bb10d.remote.data.SessionTab
import com.bb10d.remote.ui.components.ErrorBanner
import com.bb10d.remote.ui.components.WorkingDots
import androidx.compose.ui.graphics.Color
import com.bb10d.remote.data.Time
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.IntrinsicSize
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.rememberClip
import com.bb10d.remote.ui.markdown.LocalHostLinkHandler
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.AgentRemoteTheme
import com.bb10d.remote.ui.theme.MonoStyle
import com.bb10d.remote.ui.theme.palette
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun TranscriptScreen(
    vm: TranscriptViewModel,
    onBack: () -> Unit,
    /** Another open conversation's bubble was tapped. */
    onOpenTab: (SessionTab) -> Unit = {},
) {
    val ui by vm.ui.collectAsStateWithLifecycle()
    val jobState by vm.job.collectAsStateWithLifecycle()
    val live by vm.liveStatus.collectAsStateWithLifecycle()
    val session by vm.session.collectAsStateWithLifecycle()
    val profile by vm.profile.collectAsStateWithLifecycle()
    val settings by vm.settings.collectAsStateWithLifecycle()
    val harness by vm.harnessProvider.collectAsStateWithLifecycle()
    val conn by vm.conn.collectAsStateWithLifecycle()

    // Theme follows THIS session's harness (multi hosts), not the profile's
    // default ping provider — matches BB/web chrome.
    val accent = Accent.forProvider(harness)
    val dark = when (settings.theme) {
        "dark" -> true
        "light" -> false
        else -> isSystemInDarkTheme()
    }
    AgentRemoteTheme(accent = accent, dark = dark) {
        val sheet = isSheetLayout()
        val body: @Composable () -> Unit = { TranscriptScreenBody(
            vm = vm,
            onBack = onBack,
            sheet = sheet,
            ui = ui,
            jobState = jobState,
            live = live,
            session = session,
            profile = profile,
            settings = settings,
            accent = accent,
            harness = harness,
            conn = conn,
        ) }
        if (!sheet) {
            // A window of its own now (over the list): Back closes it.
            androidx.activity.compose.BackHandler { onBack() }
            // Over the list in the same window: it takes every touch.
            Box(Modifier.fillMaxSize().pointerInput(Unit) { detectTapGestures { } }) {
                body()
                // The page runs to the top of the screen under a translucent
                // status bar.
                StatusBarScrim()
            }
        } else {
            val tabs by vm.repository.tabStore.tabs.collectAsStateWithLifecycle()
            val ref by vm.ref.collectAsStateWithLifecycle()
            val working by vm.repository.workingKeys.collectAsStateWithLifecycle()
            val blocked by vm.repository.blockedKeys.collectAsStateWithLifecycle()
            val connAll by vm.repository.conn.collectAsStateWithLifecycle()
            SessionSheetFrame(
                tabs = tabs,
                current = ref,
                working = working,
                blocked = blocked,
                conn = connAll,
                onHome = onBack,
                onOpen = onOpenTab,
                onClose = { tab ->
                    val next = vm.repository.tabStore.close(tab.ref)
                    // The open one closed: its neighbour comes forward, as in
                    // a browser; with none left the sheet goes.
                    if (tab.ref.key != ref.key) false
                    else if (next != null) { onOpenTab(next); false }
                    else true
                },
            ) { body() }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun TranscriptScreenBody(
    vm: TranscriptViewModel,
    onBack: () -> Unit,
    sheet: Boolean,
    ui: TranscriptUi,
    jobState: com.bb10d.remote.data.JobState,
    live: com.bb10d.remote.data.ActiveJobDto?,
    session: com.bb10d.remote.data.SessionDto?,
    profile: com.bb10d.remote.data.Profile?,
    settings: com.bb10d.remote.data.AppSettings,
    accent: Accent,
    harness: String,
    conn: com.bb10d.remote.data.ConnState?,
) {
    val pal = palette
    val ref by vm.ref.collectAsStateWithLifecycle()
    val showLiveTui = ref.sessionId.isNotEmpty()
        && (profile?.caps?.liveTuiFor(harness.ifBlank { null }) == true)
    // The session window's two faces: the chat, or the host terminal. Each
    // session reopens on the face it was left on.
    val tuiView by vm.tuiView.collectAsStateWithLifecycle()
    val tuiOn = showLiveTui && tuiView
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val clipboard = rememberClip()
    val context = LocalContext.current

    // Composer draft is owned by the ViewModel so attachment inserts survive
    // the system file picker (local remember state was dropped on return).
    val composerText by vm.composerText.collectAsStateWithLifecycle()
    val attachments by vm.attachments.collectAsStateWithLifecycle()
    val processView by vm.processView.collectAsStateWithLifecycle()
    val openSteps by vm.openSteps.collectAsStateWithLifecycle()
    val share by vm.share.collectAsStateWithLifecycle()
    val hostFile by vm.hostFile.collectAsStateWithLifecycle()
    val changes by vm.changes.collectAsStateWithLifecycle()
    var menuOpen by remember { mutableStateOf(false) }
    var showQueue by remember { mutableStateOf(false) }
    var showOptions by remember { mutableStateOf(false) }
    // Long-press opens a menu; nothing destructive happens on the gesture
    // itself. Rewind then asks again, because it cannot be undone.
    var actionItem by remember { mutableStateOf<TranscriptItem?>(null) }
    var confirmRewind by remember { mutableStateOf<TranscriptItem?>(null) }
    // Sheet can be dismissed without cancelling on the daemon; banner reopens it.
    var questionSheetOpen by remember { mutableStateOf(false) }
    var lastQuestionRequestId by remember { mutableStateOf<String?>(null) }

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        scope.launch {
            val name = queryFileName(context, uri)
            // IO thread: camera photos are several MB and cloud-backed
            // (Google Photos) documents can block while they download.
            val bytes = withContext(Dispatchers.IO) {
                runCatching {
                    context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                }.getOrNull()
            }
            if (bytes == null || bytes.isEmpty()) {
                // Empty happens for cloud-only files the provider couldn't
                // fetch — the old build uploaded these as 0 bytes (HTTP 400).
                vm.note(if (bytes == null) "Could not read that file" else "File is empty on this device (cloud-only?)")
                return@launch
            }
            // Uploads become chips above the composer; markers join the
            // prompt at send time (vm.sendComposer).
            vm.attach(name, bytes)
        }
    }

    // Two different jumps. An explicit tick (opened the session, sent a
    // message) always wins — the user is looking for something specific. A new
    // streamed line only follows if they were already at the bottom, so
    // reading back through history is never yanked away mid-sentence.
    // The list's real last row: the "Load earlier" header and the working
    // tail are rows too.
    val bottomRow = ui.items.lastIndex + (if (ui.canLoadOlder) 1 else 0) +
        (if (jobState.running) 1 else 0)
    LaunchedEffect(ui.scrollTick) {
        if (ui.items.isNotEmpty()) listState.scrollToItem(bottomRow)
    }
    // The keyboard coming up shrinks the list from below (imePadding). The
    // list keeps its first row, so the newest ones slid under the keyboard;
    // now the conversation follows it up to its newest row, so the reply
    // being typed sits right under what it answers. Tracked through the
    // keyboard's whole slide, not only where it ends.
    val density = LocalDensity.current
    val imeBottom = WindowInsets.ime.getBottom(density)
    val lastIme = remember { intArrayOf(0) }
    LaunchedEffect(imeBottom) {
        val rising = imeBottom > lastIme[0]
        lastIme[0] = imeBottom
        if (!rising || tuiView || ui.items.isEmpty()) return@LaunchedEffect
        listState.scrollToItem(bottomRow)
        listState.scrollBy(1_000_000f)
    }
    // The tail appearing at the bottom is followed like new output.
    LaunchedEffect(jobState.running) {
        if (!jobState.running || ui.items.isEmpty()) return@LaunchedEffect
        val last = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
        if (last >= bottomRow - 2) listState.animateScrollToItem(bottomRow)
    }
    // Keyed on the LAST item, not the count, so loading older messages (which
    // also changes the count) can never be mistaken for new output arriving.
    // Two distances from the newest output. "Away": the newest message is
    // not on screen at all. "Behind": it is, but its end is more than a
    // little below the screen. Pixels, not rows, for the second: one reply
    // can be several screens tall, so "within three rows of the end" used to
    // be true a long way up, and new output pulled the reader down.
    val slackPx = with(LocalDensity.current) { 160.dp.roundToPx() }
    val awayFromLatest by remember {
        derivedStateOf {
            val info = listState.layoutInfo
            val tail = info.visibleItemsInfo.lastOrNull() ?: return@derivedStateOf false
            tail.index < info.totalItemsCount - 1
        }
    }
    val behindLatest by remember(slackPx) {
        derivedStateOf {
            val info = listState.layoutInfo
            val tail = info.visibleItemsInfo.lastOrNull() ?: return@derivedStateOf false
            tail.offset + tail.size > info.viewportEndOffset + slackPx
        }
    }
    // New output that arrived while reading further up: the pill says so.
    var unseen by remember { mutableStateOf(false) }
    // Where the reader was BEFORE the newest row was added; by the time the
    // effect below runs, the list already contains it.
    val wasReadingBack = remember { booleanArrayOf(false) }
    // True while the list is being scrolled to new output by the effect
    // below. A row that has just arrived is under the screen until that
    // scroll reaches it, which looks exactly like the reader having scrolled
    // away; a second row arriving meanwhile was taken for that and the
    // transcript stopped following.
    val following = remember { booleanArrayOf(false) }
    val lastId = ui.items.lastOrNull()?.id
    LaunchedEffect(lastId) {
        if (ui.items.isEmpty()) return@LaunchedEffect
        if (wasReadingBack[0]) {
            unseen = true
            return@LaunchedEffect
        }
        following[0] = true
        try {
            listState.animateScrollToItem(bottomRow)
        } finally {
            following[0] = false
        }
        // Reached only when the scroll ran to its end (a newer row cancels
        // it above). Where the reader is now: a reply taller than the screen
        // leaves them at its first line, reading, and the next row must not
        // pull them off it.
        wasReadingBack[0] = awayFromLatest || behindLatest
    }
    LaunchedEffect(awayFromLatest, behindLatest, lastId) {
        if (following[0]) return@LaunchedEffect
        wasReadingBack[0] = awayFromLatest || behindLatest
        if (!wasReadingBack[0]) unseen = false
    }
    // Reaching the top asks for the earlier messages by itself, as if the
    // "Load earlier messages" row had been tapped.
    // Only once the reader has scrolled: the list also starts at the top
    // for a moment before it jumps to the newest row.
    var readerScrolled by remember { mutableStateOf(false) }
    LaunchedEffect(listState.isScrollInProgress) {
        if (listState.isScrollInProgress) readerScrolled = true
    }
    val atTop by remember {
        derivedStateOf { listState.firstVisibleItemIndex == 0 && listState.layoutInfo.totalItemsCount > 1 }
    }
    LaunchedEffect(atTop, readerScrolled, ui.canLoadOlder, ui.loadingOlder) {
        if (atTop && readerScrolled && ui.canLoadOlder && !ui.loadingOlder) vm.loadOlder()
    }
    // Older messages went in above: shift the anchor by exactly as many rows,
    // so the message you were reading stays under your thumb.
    LaunchedEffect(ui.prependTick) {
        if (ui.prependTick == 0 || ui.prependCount == 0) return@LaunchedEffect
        // The "Load earlier messages" row is item 0 and disappears once the
        // whole transcript is loaded, which shifts everything up by one more.
        val headerShift = if (ui.canLoadOlder) 0 else -1
        val target = listState.firstVisibleItemIndex + ui.prependCount + headerShift
        listState.scrollToItem(
            target.coerceIn(0, ui.items.lastIndex.coerceAtLeast(0)),
            listState.firstVisibleItemScrollOffset,
        )
    }

    // The session bar opens expanded and folds down to a slim pill (the
    // title) a few seconds later, or as soon as the reader drags the
    // conversation. A tap on the pill opens it again. It stays open while
    // its menu is open.
    var confirmReset by remember(ref.key) { mutableStateOf(false) }
    if (confirmReset) AlertDialog(
        onDismissRequest = { confirmReset = false },
        title = { Text("Force stop this session on its host?") },
        text = {
            Text(
                "Its running turn is stopped, its terminal is closed, and Claude's " +
                    "background copy of it is stopped. The conversation is kept; the " +
                    "next message opens it again.",
            )
        },
        confirmButton = {
            TextButton(onClick = { confirmReset = false; vm.resetOnHost() }) { Text("Reset") }
        },
        dismissButton = { TextButton(onClick = { confirmReset = false }) { Text("Cancel") } },
    )
    var barOpen by remember(ref.key) { mutableStateOf(true) }
    var barTick by remember { mutableIntStateOf(0) }
    // Phones only (the sheet): a tablet or a phone held sideways has the
    // room, and folding there would only take the title and buttons away.
    // Nor does it fold on an upright screen with room to spare (at least
    // 480 x 720 dp): the open bar costs nothing there.
    val cfg = androidx.compose.ui.platform.LocalConfiguration.current
    val roomy = cfg.screenHeightDp > cfg.screenWidthDp &&
        cfg.screenWidthDp >= 480 && cfg.screenHeightDp >= 720
    val folds = sheet && !roomy
    LaunchedEffect(folds) { if (!folds) barOpen = true }
    LaunchedEffect(barOpen, barTick, menuOpen, folds) {
        if (barOpen && !menuOpen && folds) {
            delay(5000)
            barOpen = false
        }
    }
    val dragged by listState.interactionSource.collectIsDraggedAsState()
    // The reader's own scrolling: down (toward the latest) folds the bar
    // away, up (back through the conversation) brings it out again. Only
    // while a finger is on the list, so new output arriving does neither.
    val scrollGate = androidx.compose.runtime.rememberUpdatedState(dragged && !menuOpen && folds)
    LaunchedEffect(listState) {
        var prev = listState.firstVisibleItemIndex to listState.firstVisibleItemScrollOffset
        androidx.compose.runtime.snapshotFlow {
            listState.firstVisibleItemIndex to listState.firstVisibleItemScrollOffset
        }.collect { cur ->
            val by = when {
                // Earlier messages put on top move the index by a page at
                // once: that is not the reader's scrolling.
                kotlin.math.abs(cur.first - prev.first) > 3 -> 0
                cur.first != prev.first -> (cur.first - prev.first) * 1000
                else -> cur.second - prev.second
            }
            prev = cur
            if (!scrollGate.value || kotlin.math.abs(by) < 6) return@collect
            if (by > 0) barOpen = false
            else if (!barOpen) { barOpen = true; barTick++ }
        }
    }

    Scaffold(
        topBar = {
            // The bar floats: a rounded card over the top of the
            // conversation, which scrolls on underneath it (as on the web).
            // The live strip is a second, smaller card under it.
            Column(
                Modifier
                    .fillMaxWidth()
                    .statusBarsPadding()
                    .padding(
                        start = 10.dp, end = 10.dp, bottom = 4.dp,
                        top = 8.dp,
                    ),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                // Folding and unfolding is one motion: the card narrows to
                // its end (where the pill sits) and the pill's content fades
                // in, and back.
                Box(Modifier.fillMaxWidth()) {
                // One bar in both states; only its card changes shape, and
                // that change is animated. Open, the card is one rounded
                // rectangle. Folded, it is two pieces: a circle round Back and
                // a pill round the Chat | TUI switch and More, with the title
                // faded out between them. The buttons never move.
                val fold by animateFloatAsState(
                    targetValue = if (barOpen) 0f else 1f,
                    animationSpec = tween(300, easing = FastOutSlowInEasing),
                    label = "bar-fold",
                )
                var actionsPx by remember { mutableIntStateOf(0) }
                BoxWithConstraints(
                    Modifier
                        .fillMaxWidth()
                        // Any touch on the open bar keeps it open a while
                        // longer. Folded, the gap between the pieces takes no
                        // touch, so the page under it still scrolls.
                        .then(
                            if (barOpen) Modifier.pointerInput(Unit) {
                                awaitEachGesture {
                                    awaitFirstDown(requireUnconsumed = false)
                                    barTick++
                                }
                            } else Modifier,
                        ),
                ) {
                BarCard(
                    fold = fold,
                    total = maxWidth,
                    actionsWidth = with(LocalDensity.current) { actionsPx.toDp() },
                    modifier = Modifier.matchParentSize(),
                )
                Column(Modifier.clip(RoundedCornerShape(18.dp))) {
                TopAppBar(
                    windowInsets = WindowInsets(0, 0, 0, 0),
                    title = {
                        // Gone the moment the bar starts to fold; it only fades
                        // on the way back.
                        Column(Modifier.alpha(if (barOpen) 1f - fold else 0f)) {
                            Text(
                                text = session?.title?.ifBlank { null } ?: "Session",
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.titleSmall,
                            )
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    text = buildSubtitle(
                                        profile?.displayName.orEmpty(),
                                        session?.cwd.orEmpty(),
                                        accent.label,
                                    ),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = pal.dim,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                    modifier = Modifier.weight(1f, fill = false),
                                )
                                // Only when it is NOT live: what is on screen
                                // may be out of date.
                                if (conn == com.bb10d.remote.data.ConnState.RECONNECTING ||
                                    conn == com.bb10d.remote.data.ConnState.OFFLINE
                                ) {
                                    Spacer(Modifier.width(6.dp))
                                    MetaPill(connLabel(conn), tint = connTint(conn))
                                }
                            }
                        }
                    },
                    // Back to the list, at the start of the bar.
                    navigationIcon = {
                        // 48 dp, the folded bar's circle exactly: the ripple
                        // fills it.
                        CircleButton(onClick = onBack, size = 48.dp) {
                            Icon(
                                Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "Back to sessions",
                            )
                        }
                    },
                    actions = {
                        // Measured, so the folded pill knows what to hug.
                        Row(
                            Modifier.onSizeChanged { actionsPx = it.width },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                        if (jobState.queued.isNotEmpty()) {
                            IconButton(onClick = { showQueue = true }) {
                                Box {
                                    Icon(Icons.AutoMirrored.Outlined.PlaylistPlay, contentDescription = "Queue")
                                    Text(
                                        text = jobState.queued.size.toString(),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = accent.tint,
                                        modifier = Modifier.align(Alignment.TopEnd),
                                    )
                                }
                            }
                        }
                        // In the bar: the Chat | TUI switch and More. Session
                        // options and the rest are under More.
                        if (showLiveTui) {
                            FaceSwitch(
                                tui = tuiOn,
                                accent = accent.tint,
                                onPick = vm::setTuiView,
                            )
                        }
                        Box {
                            // Folded, More joins the bar again as it opens its menu.
                            IconButton(onClick = { barOpen = true; barTick++; menuOpen = true }) {
                                Icon(Icons.Outlined.MoreVert, contentDescription = "More")
                            }
                            DropdownMenu(menuOpen, onDismissRequest = { menuOpen = false }) {
                                // How the next turn runs: first in the menu.
                                DropdownMenuItem(
                                    text = { Text("Session options") },
                                    leadingIcon = { Icon(Icons.Outlined.Tune, null) },
                                    onClick = {
                                        menuOpen = false
                                        showOptions = true
                                    },
                                )
                                // Only where it can work: a daemon that has
                                // the route, and a session with a folder.
                                if (profile?.caps?.changes == true &&
                                    !session?.cwd.isNullOrBlank() && ref.sessionId.isNotEmpty()
                                ) {
                                    DropdownMenuItem(
                                        text = { Text("Changes") },
                                        leadingIcon = { Icon(Icons.Outlined.Difference, null) },
                                        onClick = {
                                            menuOpen = false
                                            vm.openChanges()
                                        },
                                    )
                                }
                                if (profile?.share == true) {
                                    DropdownMenuItem(
                                        text = { Text("Share link") },
                                        leadingIcon = { Icon(Icons.Outlined.Share, null) },
                                        enabled = ref.sessionId.isNotEmpty() && !share.busy,
                                        onClick = {
                                            menuOpen = false
                                            vm.shareSession()
                                        },
                                    )
                                }
                                DropdownMenuItem(
                                    text = { Text("Refresh") },
                                    leadingIcon = { Icon(Icons.Outlined.Refresh, null) },
                                    onClick = { menuOpen = false; vm.refresh() },
                                )
                                // Adds the agent's working steps (tool calls,
                                // results, thinking) under each message. This
                                // session only, off by default — web parity.
                                DropdownMenuItem(
                                    text = { Text(if (processView) "Process view ✓" else "Process view") },
                                    leadingIcon = { Icon(Icons.Outlined.Timeline, null) },
                                    enabled = ref.sessionId.isNotEmpty(),
                                    onClick = {
                                        menuOpen = false
                                        vm.setProcessView(!processView)
                                    },
                                )
                                DropdownMenuItem(
                                    text = { Text("Copy transcript") },
                                    leadingIcon = { Icon(Icons.Outlined.ContentCopy, null) },
                                    onClick = {
                                        menuOpen = false
                                        clipboard.copy(plainTranscript(ui.items))
                                    },
                                )
                                if (profile?.caps?.sessionReset == true) DropdownMenuItem(
                                    text = { Text("Reset on host") },
                                    leadingIcon = { Icon(Icons.Outlined.PowerSettingsNew, null) },
                                    onClick = {
                                        menuOpen = false
                                        confirmReset = true
                                    },
                                )
                                DropdownMenuItem(
                                    text = { Text("Copy session id") },
                                    leadingIcon = { Icon(Icons.Outlined.Tag, null) },
                                    onClick = {
                                        menuOpen = false
                                        clipboard.copy(ref.sessionId)
                                    },
                                )
                            }
                        }
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Color.Transparent,
                    ),
                )
                // The live strip folded into the bar's card, as its bottom
                // row, while a turn runs.
                // On a phone the bubbles' dot says it instead.
                // Nor while the working row at the end of the conversation
                // is in view: one of the two is enough.
                StatusBanner(
                    running = jobState.running && !sheet && (tuiOn || awayFromLatest),
                    phase = live?.phase.orEmpty(),
                    phaseDetail = live?.phaseDetail.orEmpty(),
                    tool = live?.tool.orEmpty().ifEmpty { jobState.toolLine },
                    toolDetail = live?.toolDetail.orEmpty(),
                    elapsed = live?.elapsedS ?: 0,
                    accent = com.bb10d.remote.ui.components.stateTint("working"),
                    agent = accent.label,
                )
                // On the TUI face: whether the terminal is live, in the same
                // place.
                if (tuiOn && !sheet) {
                    val tui by remember(ref.key) { vm.repository.tuiFeeds.flowFor(ref) }
                        .collectAsStateWithLifecycle()
                    Text(
                        text = (if (tui.live) "\u25CF " else "") + tui.status,
                        style = MaterialTheme.typography.labelMedium,
                        color = if (tui.live) stateTint("working") else pal.dim,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(stateTint("working").copy(alpha = if (tui.live) 0.10f else 0f))
                            .padding(horizontal = 16.dp, vertical = 7.dp),
                    )
                }
                }
                }
                }
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        var chipMenuOpen by remember { mutableStateOf(false) }
        // The floating bottom bar's height, so the list ends above it.
        var bottomPx by remember { mutableIntStateOf(0) }
        val bottomPad = with(LocalDensity.current) { bottomPx.toDp() }
        // On the TUI face only a gate card floats there (no message box).
        val gatePad = if (jobState.pendingPermission != null ||
            (jobState.pendingQuestion != null && !questionSheetOpen)) bottomPad else 0.dp
        // The sheet's tab column stands just above the bar.
        // On the TUI face, above the terminal's keys and text line instead.
        var tuiControlsPx by remember { mutableIntStateOf(0) }
        val tuiControls = with(LocalDensity.current) { tuiControlsPx.toDp() }
        val sheetInset = LocalSheetBottomInset.current
        androidx.compose.runtime.SideEffect {
            sheetInset.value = if (tuiOn) maxOf(tuiControls, gatePad) else bottomPad
        }
        // Everything starts below the floating bar but may scroll under it.
        // Folded, the bar is shorter, but the page keeps the room it had
        // open: padding that changed with it would shift the whole page.
        val barRoom = remember(ref.key) { arrayOf(0.dp) }
        if (barOpen) barRoom[0] = padding.calculateTopPadding()
        val topPad = maxOf(barRoom[0], padding.calculateTopPadding())
        Column(
            Modifier
                .fillMaxSize()
                .padding(bottom = padding.calculateBottomPadding())
                .imePadding(),
        ) {
            Box(Modifier.weight(1f)) {
                when {
                    // Keyed on the session: the pane's own state (last
                    // frame, its number, the column count) belongs to one
                    // session and must not carry over if the ref rebinds.
                    tuiOn -> key(ref.key) {
                        LiveTuiPane(
                            repo = vm.repository,
                            ref = ref,
                            // One draft per session, whichever face it is
                            // typed in.
                            line = composerText,
                            onLine = vm::updateComposer,
                            // Under the floating bar, to the top of the screen;
                            // above a gate card floating at the bottom.
                            modifier = Modifier.fillMaxSize().padding(bottom = gatePad),
                            onControlsHeight = { tuiControlsPx = it },
                            running = jobState.running,
                            onStop = vm::stop,
                            // No terminal yet: the first line sent starts one.
                            // Any daemon that runs interactive turns can start one, so
                            // this does not wait on what the last ping said of auto mode.
                            onStartTerminal = if (profile?.caps?.interactive == true) vm::startTerminal else null,
                            startError = if (jobState.status == "error") jobState.error else "",
                            accentColor = accent.tint,
                            onAccentColor = accent.onTint,
                            onTerminal = vm::terminalSeen,
                            topInset = topPad,
                            // The top bar follows the terminal's scrolling
                            // as it does the chat's: away going down, back
                            // going up.
                            onReaderScroll = { by ->
                                if (folds && !menuOpen) {
                                    if (by > 0) barOpen = false
                                    else if (!barOpen) { barOpen = true; barTick++ }
                                }
                            },
                        )
                    }

                    ui.loading && ui.items.isEmpty() -> Box(
                        Modifier.fillMaxSize().padding(top = topPad),
                        contentAlignment = Alignment.Center,
                    ) { CircularProgressIndicator(color = accent.tint) }

                    ui.error != null && ui.items.isEmpty() -> Box(
                        Modifier
                            .fillMaxSize()
                            .padding(top = topPad)
                            .padding(16.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        ErrorBanner(ui.error!!, action = {
                            TextButton(onClick = { vm.loadTail() }) { Text("Retry") }
                        })
                    }

                    else -> CompositionLocalProvider(
                        LocalHostLinkHandler provides vm::openHostFile,
                    ) {
                    val readSide = ((androidx.compose.ui.platform.LocalConfiguration.current.screenWidthDp.dp - 840.dp) / 2)
                        .coerceAtLeast(14.dp)
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        // On a wide window (tablet, unfolded, a phone held
                        // sideways) the text keeps a readable measure: the
                        // column is 840 dp at most, centred.
                        contentPadding = PaddingValues(
                            start = readSide,
                            end = readSide,
                            top = topPad + 10.dp,
                            // 48 dp of air between the last row and the bar.
                            bottom = bottomPad + 48.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        if (ui.canLoadOlder) {
                            item(key = "older") {
                                LoadOlderRow(ui.loadingOlder) { vm.loadOlder() }
                            }
                        }
                        items(ui.items, key = { it.id }) { item ->
                            MessageRow(
                                item = item,
                                accent = accent.tint,
                                rich = settings.richText,
                                onLongPress = { actionItem = item },
                                openSteps = openSteps,
                                onToggleStep = vm::toggleStep,
                            )
                        }
                        if (jobState.running) {
                            item(key = "working-tail") {
                                val waiting = jobState.pendingQuestion != null
                                    || jobState.pendingPermission != null
                                WorkingTail(
                                    text = if (waiting) "Waiting for your answer"
                                    else liveHeadline(
                                        live?.phase.orEmpty(),
                                        live?.phaseDetail.orEmpty(),
                                        live?.tool.orEmpty().ifEmpty { jobState.toolLine },
                                        accent.label,
                                    ),
                                    elapsed = if (waiting) 0 else live?.elapsedS ?: 0,
                                    // The state's own colour, as in the list.
                                    color = com.bb10d.remote.ui.components.stateTint(
                                        if (waiting) "needs_answer" else "working"),
                                )
                            }
                        }
                    } }
                }
                if (!tuiOn && (awayFromLatest || unseen) && ui.items.isNotEmpty()) {
                    JumpToLatest(
                        unseen = unseen,
                        accent = accent.tint,
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .padding(bottom = bottomPad + 8.dp),
                    ) {
                        scope.launch {
                            // Far away: go straight there. A smooth scroll
                            // over a long transcript takes seconds.
                            val last = ui.items.lastIndex
                            val visible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
                            if (last - visible > 30) listState.scrollToItem(last)
                            else listState.animateScrollToItem(last)
                            // To the END of it, not its first line.
                            listState.scrollBy(1_000_000f)
                            unseen = false
                        }
                    }
                }

            // Soft fades where the page runs under the floating pieces, so
            // the text does not collide with them.
            val pageBg = MaterialTheme.colorScheme.background
            Box(
                Modifier
                    .align(Alignment.TopCenter)
                    .fillMaxWidth()
                    .height(if (barOpen) topPad + 14.dp else topPad)
                    .background(Brush.verticalGradient(0f to pageBg, 0.6f to pageBg.copy(alpha = 0.85f), 1f to Color.Transparent)),
            )
            if (!tuiOn) Box(
                Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .height(bottomPad + 18.dp)
                    .background(Brush.verticalGradient(0f to Color.Transparent, 0.45f to pageBg.copy(alpha = 0.85f), 1f to pageBg)),
            )

            // The bottom bar floats, like the session bar: the gate and the
            // message box are cards over the bottom of the conversation,
            // which scrolls on underneath them. Its height pads the list.
            Column(
                Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .onSizeChanged { bottomPx = it.height }
                    .padding(start = 10.dp, end = 10.dp, bottom = 8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
            jobState.pendingPermission?.let { pending ->
                PermissionPrompt(
                    tool = pending.toolName,
                    detail = pending.detail,
                    accent = accent.tint,
                    onAllow = { vm.answerPermission(true) },
                    onDeny = { vm.answerPermission(false) },
                )
            }

            // Daemon still has a pending ask, but the sheet was dismissed —
            // keep a re-open control (web "Answer" banner parity).
            if (jobState.pendingQuestion != null && !questionSheetOpen) {
                QuestionBanner(
                    accent = accent.tint,
                    onAnswer = {
                        jobState.pendingQuestion?.requestId
                            ?.let(vm::reopenQuestion)
                        questionSheetOpen = true
                    },
                )
            }

            // The TUI face types straight into the host terminal, so the
            // chat's composer and its chips step aside.
            if (!tuiOn) Surface(
                shape = RoundedCornerShape(18.dp),
                color = MaterialTheme.colorScheme.surface,
                border = BorderStroke(1.dp, pal.hairline),
                shadowElevation = 8.dp,
            ) {
            Column {
            AnimatedVisibility(ui.status.isNotBlank()) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 14.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        ui.status,
                        style = MaterialTheme.typography.labelSmall,
                        color = pal.dim,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = vm::clearStatus) { Text("Dismiss") }
                }
            }

            val h = harness.ifBlank { null }
            // Typed into the session's terminal, or a headless turn: the
            // daemon's pick on a new daemon, the Execution setting on an old.
            val interactiveNow by vm.sendsToTerminal.collectAsStateWithLifecycle()
            SlashSuggestions(
                matches = slashMatches(composerText, profile?.caps?.slashFor(h).orEmpty()),
                headless = !interactiveNow,
                onPick = { vm.updateComposer("$it ") },
            )
            AttachmentChips(
                chips = attachments,
                onRemove = vm::removeAttachment,
            )
            Composer(
                text = composerText,
                onText = vm::updateComposer,
                canSend = composerText.isNotBlank() ||
                    attachments.any { !it.uploading },
                running = jobState.running,
                interactive = interactiveNow,
                accent = accent.tint,
                onAccent = accent.onTint,
                onSend = vm::sendComposer,
                onStop = vm::stop,
                onAttach = { picker.launch(arrayOf("*/*")) },
            )
            }
            }
            }
            // Model and effort float above the start of the bottom bar, in a
            // card of their own, and only while the keyboard is up (that is
            // when the next message is being written). They sit over the
            // page, not in the bar's column, so nothing else moves.
            if (!tuiOn) Box(
                Modifier
                    .align(Alignment.BottomStart)
                    // Stops short of the tab bubble at the end of the bar
                    // (and scrolls sideways if it needs more).
                    .padding(end = 76.dp, bottom = (bottomPad - 8.dp).coerceAtLeast(0.dp)),
            ) {
                // Opening a chip's menu takes the focus, which puts the
                // keyboard away: the chips stay for as long as a menu is
                // open, or they would vanish and take the menu with them.
                androidx.compose.animation.AnimatedVisibility(
                    visible = imeBottom > 0 || chipMenuOpen,
                    enter = fadeIn(tween(160)),
                    exit = fadeOut(tween(120)),
                ) {
                    Row(
                        // Room inside the scroll for the pill's shadow on
                        // every side (a scroller clips at its edge), most
                        // at the foot where a shadow reaches furthest.
                        Modifier
                            .horizontalScroll(rememberScrollState())
                            .padding(start = 10.dp, end = 16.dp, top = 12.dp, bottom = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                    val chipHarness = harness.ifBlank { null }
                    if (profile?.let { hasTurnChips(it, chipHarness) } == true) {
                    Surface(
                        shape = RoundedCornerShape(50),
                        color = MaterialTheme.colorScheme.surface,
                        border = BorderStroke(1.dp, pal.hairline),
                        shadowElevation = 6.dp,
                    ) {
                    profile?.let {
                        TurnChips(
                            profile = it,
                            harness = harness.ifBlank { null },
                            // Read from the collected session state, not the view
                            // model, so the chips repaint when the pin changes.
                            model = sessionModelOf(session, profile, harness),
                            effort = sessionEffortOf(session, profile, harness),
                            perSession = it.caps.sessionOptions && ref.sessionId.isNotEmpty(),
                            onModel = vm::setModel,
                            onEffort = vm::setEffort,
                            onMenu = { chipMenuOpen = it },
                        )
                    }
                    }
                    }
                    }
                }
            }
            }
        }
    }

    if (showQueue) {
        ModalBottomSheet(
            onDismissRequest = { showQueue = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        ) {
            QueueSheet(
                queued = jobState.queued,
                onCancel = vm::cancelQueued,
                onClose = { showQueue = false },
            )
        }
    }

    if (changes.open) {
        ModalBottomSheet(
            onDismissRequest = vm::closeChanges,
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        ) {
            ChangesSheet(
                ui = changes,
                onRefresh = vm::loadChanges,
                onCommit = vm::commitChanges,
                onPush = vm::pushChanges,
                canScope = profile?.caps?.changesTurn == true,
                onScope = vm::setChangesTurnScope,
                onProject = vm::setChangesRoot,
            )
        }
    }
    // The turn is over: what it changed is worth a fresh look.
    LaunchedEffect(jobState.running) {
        if (!jobState.running && changes.open) vm.loadChanges()
    }

    if (showOptions) {
        ModalBottomSheet(onDismissRequest = { showOptions = false }) {
            profile?.let { SessionOptionsSheet(vm, it) }
        }
    }

    actionItem?.let { item ->
        ModalBottomSheet(onDismissRequest = { actionItem = null }) {
            MessageActionsSheet(
                item = item,
                accent = accent.tint,
                rewindSteps = if (item.role == "user") vm.rewindSteps(item.id) else 0,
                rewindBlockedReason = vm.rewindBlockedReason(),
                onCopy = {
                    clipboard.copy(item.text)
                    actionItem = null
                },
                onRewind = {
                    actionItem = null
                    confirmRewind = item
                },
            )
        }
    }

    confirmRewind?.let { item ->
        val steps = vm.rewindSteps(item.id)
        AlertDialog(
            onDismissRequest = { confirmRewind = null },
            title = { Text("Rewind the session?") },
            text = {
                Column {
                    Text(
                        "The conversation goes back to just before this message, dropping " +
                            if (steps == 1) "your last message and the reply to it."
                            else "the last $steps of your messages and everything after them.",
                    )
                    Spacer(Modifier.height(10.dp))
                    Text(
                        text = "This cannot be undone. Conversation only — file changes " +
                            "on the host are not reverted.",
                        style = MaterialTheme.typography.bodySmall,
                        color = pal.danger,
                    )
                    Spacer(Modifier.height(10.dp))
                    Text(
                        text = item.text.lineSequence().first().take(120),
                        style = MonoStyle,
                        color = pal.dim,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmRewind = null
                    vm.rewindTo(item.id)
                }) { Text("Rewind", color = pal.danger) }
            },
            dismissButton = {
                TextButton(onClick = { confirmRewind = null }) { Text("Cancel") }
            },
        )
    }

    if (hostFile.open) {
        HostFileDialog(
            file = hostFile,
            onCopy = { clipboard.copy(it) },
            onDismiss = { vm.dismissHostFile() },
            onHostLink = vm::openHostFile,
        )
    }

    if (share.busy || share.url.isNotEmpty() || share.error != null) {
        AlertDialog(
            onDismissRequest = { vm.dismissShare() },
            title = { Text("Share session") },
            text = {
                Column {
                    when {
                        share.busy -> Text("Creating a read-only link…")
                        share.error != null -> Text(share.error!!)
                        else -> {
                            Text(
                                "Anyone with this link can read the transcript for 7 days. " +
                                    "They cannot send messages or see other sessions.",
                                style = MaterialTheme.typography.bodyMedium,
                            )
                            Spacer(Modifier.height(12.dp))
                            Text(
                                share.url,
                                style = MonoStyle,
                                color = pal.dim,
                            )
                        }
                    }
                }
            },
            confirmButton = {
                if (share.url.isNotEmpty()) {
                    TextButton(onClick = {
                        clipboard.copy(share.url)
                    }) { Text("Copy") }
                }
            },
            dismissButton = {
                Row {
                    if (share.url.isNotEmpty()) {
                        TextButton(onClick = {
                            val send = Intent(Intent.ACTION_SEND).apply {
                                type = "text/plain"
                                putExtra(Intent.EXTRA_TEXT, share.url)
                                putExtra(
                                    Intent.EXTRA_SUBJECT,
                                    session?.title?.ifBlank { null } ?: "Shared session",
                                )
                            }
                            context.startActivity(Intent.createChooser(send, "Share session"))
                        }) { Text("Share") }
                    }
                    TextButton(onClick = { vm.dismissShare() }) { Text("Close") }
                }
            },
        )
    }


    // Auto-open the sheet once per request_id; swipe/back only hides it.
    val pendingQ = jobState.pendingQuestion
    LaunchedEffect(pendingQ?.requestId) {
        val id = pendingQ?.requestId
        if (id.isNullOrBlank()) {
            questionSheetOpen = false
            lastQuestionRequestId = null
            return@LaunchedEffect
        }
        if (id != lastQuestionRequestId) {
            lastQuestionRequestId = id
            questionSheetOpen = true
        }
    }
    if (questionSheetOpen) {
        pendingQ?.let { pending ->
            QuestionSheet(
                questions = pending.questions,
                accent = accent.tint,
                onAnswer = { answers, notes ->
                    questionSheetOpen = false
                    vm.answerQuestion(answers, notes)
                },
                onCancel = {
                    // Explicit Cancel button → Esc host panel.
                    questionSheetOpen = false
                    vm.cancelQuestion()
                },
                onDismiss = {
                    // Backdrop / swipe / system back — keep pending on daemon.
                    questionSheetOpen = false
                },
            )
        }
    }
}

private fun buildSubtitle(profileName: String, cwd: String, harnessLabel: String): String {
    val folder = cwd.trimEnd('/').substringAfterLast('/')
    return listOf(profileName, harnessLabel.takeIf { it.isNotBlank() && it != "Agent" }, folder)
        .filter { !it.isNullOrBlank() }
        .joinToString(" · ")
}

private fun plainTranscript(items: List<TranscriptItem>): String =
    items.joinToString("\n\n") { item ->
        val who = when (item.role) {
            "user" -> "You"
            "assistant" -> "Agent"
            else -> item.role
        }
        "$who:\n${item.text}"
    }

private fun liveHeadline(phase: String, phaseDetail: String, tool: String, agent: String) =
    when {
        phase.isNotEmpty() && phaseDetail.isNotEmpty() -> {
            val verb = phase.replaceFirstChar { it.uppercase() }
            "$verb · $phaseDetail"
        }
        phase.isNotEmpty() -> phase.replaceFirstChar { it.uppercase() }
        tool.isNotEmpty() -> tool
        else -> "$agent is working"
    }

/**
 * The transcript's last row while a turn runs. The banner sits above the
 * list, so a reader at the bottom of a long turn saw nothing moving where
 * they were looking. Same words as the banner; the warning colour when a
 * question or permission waits on the reader.
 */
@Composable
private fun WorkingTail(text: String, elapsed: Int, color: Color) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .height(IntrinsicSize.Min),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .width(2.dp)
                .fillMaxHeight()
                .background(color.copy(alpha = 0.7f)),
        )
        Spacer(Modifier.width(10.dp))
        WorkingDots(color)
        Spacer(Modifier.width(10.dp))
        Text(
            text = if (elapsed > 0) "$text · ${Time.elapsed(elapsed)}" else text,
            style = MaterialTheme.typography.labelMedium,
            color = palette.dim,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

private fun queryFileName(context: android.content.Context, uri: android.net.Uri): String {
    val fallback = uri.lastPathSegment?.substringAfterLast('/') ?: "file"
    val cursor = runCatching {
        context.contentResolver.query(uri, null, null, null, null)
    }.getOrNull() ?: return fallback
    cursor.use {
        val index = it.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
        if (index >= 0 && it.moveToFirst()) {
            val name = it.getString(index)
            if (!name.isNullOrBlank()) return name
        }
    }
    return fallback
}
