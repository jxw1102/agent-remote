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
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import com.bb10d.remote.ui.components.connLabel
import com.bb10d.remote.ui.components.connTint
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
import com.bb10d.remote.ui.components.ErrorBanner
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
    onLiveTui: () -> Unit = {},
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
        TranscriptScreenBody(
            vm = vm,
            onBack = onBack,
            onLiveTui = onLiveTui,
            ui = ui,
            jobState = jobState,
            live = live,
            session = session,
            profile = profile,
            settings = settings,
            accent = accent,
            harness = harness,
            conn = conn,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun TranscriptScreenBody(
    vm: TranscriptViewModel,
    onBack: () -> Unit,
    onLiveTui: () -> Unit,
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
    LaunchedEffect(ui.scrollTick) {
        if (ui.items.isNotEmpty()) listState.scrollToItem(ui.items.lastIndex)
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
            listState.animateScrollToItem(ui.items.lastIndex)
        } finally {
            following[0] = false
        }
    }
    LaunchedEffect(awayFromLatest, behindLatest, lastId) {
        if (following[0]) return@LaunchedEffect
        wasReadingBack[0] = awayFromLatest || behindLatest
        if (!wasReadingBack[0]) unseen = false
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

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Column {
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
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(
                                Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "Back",
                            )
                        }
                    },
                    actions = {
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
                        // In the bar at every width, as on the web: how the
                        // next turn runs, and the host terminal. The rest is
                        // what you do now and then, under More.
                        IconButton(onClick = { showOptions = true }) {
                            Icon(Icons.Outlined.Tune, contentDescription = "Session options")
                        }
                        if (showLiveTui) {
                            IconButton(onClick = onLiveTui) {
                                Icon(Icons.Outlined.Terminal, contentDescription = "Live TUI")
                            }
                        }
                        Box {
                            IconButton(onClick = { menuOpen = true }) {
                                Icon(Icons.Outlined.MoreVert, contentDescription = "More")
                            }
                            DropdownMenu(menuOpen, onDismissRequest = { menuOpen = false }) {
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
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.background,
                    ),
                )
                StatusBanner(
                    running = jobState.running,
                    phase = live?.phase.orEmpty(),
                    phaseDetail = live?.phaseDetail.orEmpty(),
                    tool = live?.tool.orEmpty().ifEmpty { jobState.toolLine },
                    toolDetail = live?.toolDetail.orEmpty(),
                    elapsed = live?.elapsedS ?: 0,
                    accent = accent.tint,
                    agent = accent.label,
                )
                Hairline()
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {
            Box(Modifier.weight(1f)) {
                when {
                    ui.loading && ui.items.isEmpty() -> Box(
                        Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center,
                    ) { CircularProgressIndicator(color = accent.tint) }

                    ui.error != null && ui.items.isEmpty() -> Box(
                        Modifier
                            .fillMaxSize()
                            .padding(16.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        ErrorBanner(ui.error!!, action = {
                            TextButton(onClick = { vm.loadTail() }) { Text("Retry") }
                        })
                    }

                    else -> CompositionLocalProvider(
                        LocalHostLinkHandler provides vm::openHostFile,
                    ) { LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(
                            start = 14.dp,
                            end = 14.dp,
                            top = 10.dp,
                            bottom = 16.dp,
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
                    } }
                }
                if ((awayFromLatest || unseen) && ui.items.isNotEmpty()) {
                    JumpToLatest(
                        unseen = unseen,
                        accent = accent.tint,
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .padding(bottom = 10.dp),
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
            }

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
            val interactiveNow = profile?.effectiveExecMode() == ExecMode.INTERACTIVE
            SlashSuggestions(
                matches = slashMatches(composerText, profile?.caps?.slashFor(h).orEmpty()),
                headless = !interactiveNow,
                onPick = { vm.updateComposer("$it ") },
            )
            profile?.let {
                TurnChips(
                    profile = it,
                    harness = h,
                    onExecMode = vm::setExecMode,
                    onModel = vm::setModel,
                    onEffort = vm::setEffort,
                )
            }
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
