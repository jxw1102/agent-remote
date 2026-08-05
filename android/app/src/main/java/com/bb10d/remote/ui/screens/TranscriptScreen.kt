package com.bb10d.remote.ui.screens

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.automirrored.outlined.PlaylistPlay
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material.icons.outlined.Tag
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.data.ExecMode
import com.bb10d.remote.data.Time
import com.bb10d.remote.ui.components.ErrorBanner
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.MetaPill
import com.bb10d.remote.ui.components.rememberClip
import com.bb10d.remote.ui.components.WorkingPulse
import com.bb10d.remote.ui.markdown.MarkdownText
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.AgentRemoteTheme
import com.bb10d.remote.ui.theme.MonoStyle
import com.bb10d.remote.ui.theme.palette
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

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
) {
    val pal = palette
    val ref by vm.ref.collectAsStateWithLifecycle()
    val showLiveTui = ref.sessionId.isNotEmpty()
        && (profile?.caps?.liveTuiFor(harness.ifBlank { null }) == true)
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val clipboard = rememberClip()
    val context = LocalContext.current

    var composerText by remember { mutableStateOf("") }
    var menuOpen by remember { mutableStateOf(false) }
    var showQueue by remember { mutableStateOf(false) }
    var showOptions by remember { mutableStateOf(false) }
    // Long-press opens a menu; nothing destructive happens on the gesture
    // itself. Rewind then asks again, because it cannot be undone.
    var actionItem by remember { mutableStateOf<TranscriptItem?>(null) }
    var confirmRewind by remember { mutableStateOf<TranscriptItem?>(null) }

    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        scope.launch {
            val name = queryFileName(context, uri)
            val bytes = runCatching {
                context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
            }.getOrNull()
            if (bytes == null) return@launch
            // Same marker the BB10 / web clients insert so the agent (and
            // host-side tooling) can spot uploaded paths in the prompt.
            vm.attachUpload(name, bytes) { path ->
                val sep = if (composerText.isBlank()) "" else " "
                composerText = composerText.trimEnd() + sep + "[attached: $path]"
            }
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
    LaunchedEffect(ui.items.lastOrNull()?.id) {
        if (ui.items.isEmpty()) return@LaunchedEffect
        val atBottom = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index
            ?.let { it >= ui.items.size - 3 } ?: true
        if (atBottom) listState.animateScrollToItem(ui.items.lastIndex)
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
                                )
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
                        IconButton(onClick = { showOptions = true }) {
                            Icon(Icons.Outlined.Tune, contentDescription = "Turn options")
                        }
                        Box {
                            IconButton(onClick = { menuOpen = true }) {
                                Icon(Icons.Outlined.MoreVert, contentDescription = "More")
                            }
                            DropdownMenu(menuOpen, onDismissRequest = { menuOpen = false }) {
                                DropdownMenuItem(
                                    text = { Text("Refresh") },
                                    leadingIcon = { Icon(Icons.Outlined.Refresh, null) },
                                    onClick = { menuOpen = false; vm.refresh() },
                                )
                                if (showLiveTui) {
                                    DropdownMenuItem(
                                        text = { Text("Live TUI") },
                                        leadingIcon = { Icon(Icons.Outlined.Terminal, null) },
                                        onClick = {
                                            menuOpen = false
                                            onLiveTui()
                                        },
                                    )
                                }
                                DropdownMenuItem(
                                    text = { Text("Copy transcript") },
                                    leadingIcon = { Icon(Icons.Outlined.ContentCopy, null) },
                                    onClick = {
                                        menuOpen = false
                                        clipboard.copy(plainTranscript(ui.items))
                                    },
                                )
                                DropdownMenuItem(
                                    text = { Text("Session id") },
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

                    else -> LazyColumn(
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
                            )
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

            if (jobState.pendingQuestion != null) {
                QuestionBanner(accent.tint)
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

            Composer(
                text = composerText,
                onText = { composerText = it },
                running = jobState.running,
                interactive = profile?.effectiveExecMode() == ExecMode.INTERACTIVE,
                accent = accent.tint,
                onAccent = accent.onTint,
                onSend = {
                    val text = composerText
                    composerText = ""
                    vm.send(text)
                },
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

    if (showOptions) {
        ModalBottomSheet(onDismissRequest = { showOptions = false }) {
            profile?.let { TurnOptionsSheet(vm, it) }
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
                        text = "This cannot be undone. On Grok it also reverts file changes " +
                            "made since then, and anything uncommitted is lost.",
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


    jobState.pendingQuestion?.let { pending ->
        QuestionSheet(
            questions = pending.questions,
            accent = accent.tint,
            onAnswer = { answers, notes -> vm.answerQuestion(answers, notes) },
            onCancel = vm::cancelQuestion,
        )
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

@Composable
private fun LoadOlderRow(loading: Boolean, onClick: () -> Unit) {
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
private fun MessageRow(
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

/** The live strip: what the agent is doing right now, and for how long. */
@Composable
private fun StatusBanner(
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
        val headline = when {
            phase.isNotEmpty() && phaseDetail.isNotEmpty() -> "$phase · $phaseDetail"
            phase.isNotEmpty() -> phase
            tool.isNotEmpty() -> tool
            else -> "$agent is working"
        }
        val second = listOf(tool, toolDetail).filter { it.isNotBlank() }.joinToString("  ")
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

@Composable
private fun PermissionPrompt(
    tool: String,
    detail: String,
    accent: Color,
    onAllow: () -> Unit,
    onDeny: () -> Unit,
) {
    val pal = palette
    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .border(1.dp, accent.copy(alpha = 0.4f), RoundedCornerShape(12.dp))
            .padding(14.dp),
    ) {
        Text(
            "Allow $tool?",
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.onSurface,
        )
        if (detail.isNotBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(
                text = detail,
                style = MonoStyle,
                color = pal.dim,
                maxLines = 6,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
            TextButton(onClick = onDeny) { Text("Deny", color = pal.danger) }
            Spacer(Modifier.width(8.dp))
            TextButton(onClick = onAllow) { Text("Allow", color = accent) }
        }
    }
}

@Composable
private fun QuestionBanner(accent: Color) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(accent.copy(alpha = 0.14f))
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        WorkingPulse(accent, size = 7)
        Spacer(Modifier.width(10.dp))
        Text(
            "A question is waiting for you",
            style = MaterialTheme.typography.labelMedium,
            color = accent,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Composer(
    text: String,
    onText: (String) -> Unit,
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
            if (running) {
                IconButton(onClick = onStop) {
                    Icon(Icons.Outlined.Stop, contentDescription = "Stop", tint = pal.danger)
                }
            }
            IconButton(
                onClick = onSend,
                enabled = text.isNotBlank(),
                modifier = Modifier
                    .padding(bottom = 4.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (text.isNotBlank()) accent else Color.Transparent),
            ) {
                Icon(
                    Icons.AutoMirrored.Outlined.Send,
                    contentDescription = "Send",
                    tint = if (text.isNotBlank()) onAccent else pal.dim,
                )
            }
        }
    }
}

/**
 * What a long press offers. Copy is always here; rewind only appears on your
 * own messages, and only when the daemon and the current execution mode can
 * actually do it — an option that would fail is worse than no option.
 */
@Composable
private fun MessageActionsSheet(
    item: TranscriptItem,
    accent: Color,
    rewindSteps: Int,
    rewindBlockedReason: String?,
    onCopy: () -> Unit,
    onRewind: () -> Unit,
) {
    val pal = palette
    Column(Modifier.padding(bottom = 28.dp)) {
        Text(
            text = item.text.lineSequence().first().take(120),
            style = MaterialTheme.typography.bodyMedium,
            color = pal.dim,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 12.dp),
        )
        Hairline(inset = 16)

        ActionRow(Icons.Outlined.ContentCopy, "Copy message", accent, onCopy)

        if (item.role == "user" && rewindSteps > 0) {
            if (rewindBlockedReason == null) {
                ActionRow(
                    icon = Icons.Outlined.History,
                    label = "Rewind to here",
                    tint = pal.danger,
                    onClick = onRewind,
                    subtitle = if (rewindSteps == 1) {
                        "Undo your last message"
                    } else {
                        "Undo the last $rewindSteps of your messages"
                    },
                )
            } else {
                Text(
                    text = "Rewind unavailable — $rewindBlockedReason",
                    style = MaterialTheme.typography.bodySmall,
                    color = pal.dim,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 14.dp),
                )
            }
        }
    }
}

@Composable
private fun ActionRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    tint: Color,
    onClick: () -> Unit,
    subtitle: String = "",
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, tint = tint)
        Spacer(Modifier.width(16.dp))
        Column {
            Text(label, style = MaterialTheme.typography.bodyLarge)
            if (subtitle.isNotEmpty()) {
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = palette.dim,
                )
            }
        }
    }
}

@Composable
private fun QueueSheet(
    queued: List<com.bb10d.remote.data.QueuedDto>,
    onCancel: (String) -> Unit,
    onClose: () -> Unit,
) {
    Column(Modifier.padding(bottom = 24.dp)) {
        Text(
            "Queued behind this turn",
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(16.dp),
        )
        Text(
            "The daemon owns this queue, so it survives losing the app or the network.",
            style = MaterialTheme.typography.bodySmall,
            color = palette.dim,
            modifier = Modifier.padding(horizontal = 16.dp),
        )
        Spacer(Modifier.height(12.dp))
        queued.forEach { entry ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = entry.prompt,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { onCancel(entry.id) }) { Text("Cancel") }
            }
            Hairline(inset = 16)
        }
        if (queued.isEmpty()) {
            Text(
                "Nothing queued.",
                style = MaterialTheme.typography.bodyMedium,
                color = palette.dim,
                modifier = Modifier.padding(16.dp),
            )
            TextButton(onClick = onClose, modifier = Modifier.padding(start = 8.dp)) {
                Text("Close")
            }
        }
    }
}


@Composable
private fun TurnOptionsSheet(vm: TranscriptViewModel, profile: com.bb10d.remote.data.Profile) {
    val caps = profile.caps
    val harness by vm.harnessProvider.collectAsStateWithLifecycle()
    val h = harness.ifBlank { null }
    val models = caps.modelsFor(h)
    val efforts = caps.effortsFor(h)
    val interactive = caps.interactiveFor(h)
    val canSetModel = caps.canSetModelFor(h)
    val canSetEffort = caps.canSetEffortFor(h)
    val selectedModel = profile.model.takeIf { it in models } ?: models.firstOrNull().orEmpty()
    val selectedEffort = profile.effort.takeIf { it in efforts } ?: efforts.firstOrNull().orEmpty()
    Column(Modifier.padding(horizontal = 16.dp).padding(bottom = 28.dp)) {
        Text("Turn options", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(4.dp))
        Text(
            buildString {
                append("Applies to ${profile.displayName}")
                if (harness.isNotBlank()) append(" · $harness")
                append(" — later turns on this profile.")
            },
            style = MaterialTheme.typography.bodySmall,
            color = palette.dim,
        )
        Spacer(Modifier.height(16.dp))
        OptionRow(
            label = "Execution",
            options = ExecMode.options(interactive),
            selected = profile.effectiveExecMode(),
            display = { ExecMode.short(it) },
            onSelect = { vm.setExecMode(it) },
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = "Both modes auto-run tools (no permission prompts).",
            style = MaterialTheme.typography.bodySmall,
            color = palette.dim,
        )
        if (canSetModel && models.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            OptionRow(
                label = "Model",
                options = models,
                selected = selectedModel,
                display = { it },
                onSelect = { vm.setModel(it) },
            )
        }
        if (canSetEffort && efforts.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            OptionRow(
                label = "Reasoning effort",
                options = efforts,
                selected = selectedEffort,
                display = { it },
                onSelect = { vm.setEffort(it) },
            )
        }
        if (caps.rewindFor(h)) {
            Spacer(Modifier.height(16.dp))
            Text(
                "Long-press one of your messages to rewind the session back to it.",
                style = MaterialTheme.typography.bodySmall,
                color = palette.dim,
            )
        }
        Spacer(Modifier.height(12.dp))
        Row {
            MetaPill("agentremoted ${caps.version}")
            Spacer(Modifier.width(6.dp))
            if (caps.host.isNotEmpty()) MetaPill(caps.host)
        }
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
