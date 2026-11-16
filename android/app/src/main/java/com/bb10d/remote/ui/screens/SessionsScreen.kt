package com.bb10d.remote.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.material.icons.outlined.ArrowDropDown
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.FilterList
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import com.bb10d.remote.data.ConnState
import com.bb10d.remote.ui.components.connLabel
import com.bb10d.remote.ui.components.connTint
import com.bb10d.remote.ui.components.rememberClip
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.List
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.CloudDownload
import androidx.compose.material.icons.outlined.DataUsage
import androidx.compose.material.icons.outlined.Dns
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.data.SessionDto
import com.bb10d.remote.data.SessionRef
import com.bb10d.remote.data.SessionRow
import com.bb10d.remote.data.Time
import com.bb10d.remote.ui.components.EmptyState
import com.bb10d.remote.ui.components.ErrorBanner
import com.bb10d.remote.ui.components.FocusPill
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.MetaPill
import com.bb10d.remote.ui.components.ProviderChip
import com.bb10d.remote.ui.components.WorkingPulse
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.palette
import java.time.Instant
import java.time.ZoneId

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionsScreen(
    vm: SessionsViewModel,
    onOpen: (SessionRef, provider: String) -> Unit,
    onNewSession: () -> Unit,
    onProfiles: () -> Unit,
    onSettings: () -> Unit,
    onDrop: () -> Unit,
    onUsage: () -> Unit,
) {
    val profileState by vm.profiles.collectAsStateWithLifecycle()
    val rows by vm.rows.collectAsStateWithLifecycle()
    val state by vm.sessionsState.collectAsStateWithLifecycle()
    val working by vm.workingKeys.collectAsStateWithLifecycle()
    val blocked by vm.blockedKeys.collectAsStateWithLifecycle()
    val query by vm.query.collectAsStateWithLifecycle()
    val profileFilter by vm.profileFilter.collectAsStateWithLifecycle()
    val projectFilter by vm.projectFilter.collectAsStateWithLifecycle()
    val settings by vm.settings.collectAsStateWithLifecycle()
    val conn by vm.conn.collectAsStateWithLifecycle()

    var searching by remember { mutableStateOf(false) }
    var menuOpen by remember { mutableStateOf(false) }
    // Long-pressed row awaiting a rename / focus action.
    var actionRow by remember { mutableStateOf<SessionRow?>(null) }
    val renaming by vm.renaming.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()
    val pal = palette

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        if (searching) {
                            SearchField(
                                value = query,
                                onValueChange = vm::setQuery,
                                onClose = {
                                    searching = false
                                    vm.clearQuery()
                                },
                            )
                        } else if (profileState.profiles.isNotEmpty()) {
                            // Which sessions: two views of one list, so one
                            // switch that names both. It is the title: this
                            // screen is the list, and the switch says which.
                            FocusSwitch(
                                focusMode = settings.focusMode,
                                // Only once the rows came from a Focus fetch,
                                // or the count flashes the All list's length.
                                count = if (settings.focusMode && state.focusRows &&
                                    !state.loading
                                ) rows.size else 0,
                                onChange = vm::setFocusMode,
                            )
                        } else {
                            Text("Sessions", fontWeight = FontWeight.SemiBold)
                        }
                    },
                    actions = {
                        if (!searching) {
                            IconButton(onClick = { searching = true }) {
                                Icon(Icons.Outlined.Search, contentDescription = "Search")
                            }
                            // On which daemon: one button and a menu, however
                            // many daemons there are.
                            // Only daemons that are switched on: one that is
                            // off has no connection to speak of, and was
                            // listed here as "connecting…".
                            if (profileState.enabled.size >= 2) {
                                val on = profileState.enabled.map { it.id }.toSet()
                                DaemonFilter(
                                    profiles = profileState.enabled.map {
                                        DaemonChoice(
                                            it.id, it.displayName, it.provider,
                                            it.hostLabel, it.caps.version,
                                        )
                                    },
                                    selected = profileFilter,
                                    conn = conn,
                                    problems = state.problems.toMap().filterKeys { it in on },
                                    onSelect = vm::setProfileFilter,
                                )
                            }
                        }
                        Box {
                            IconButton(onClick = { menuOpen = true }) {
                                Icon(Icons.Outlined.MoreVert, contentDescription = "More")
                            }
                            DropdownMenu(menuOpen, onDismissRequest = { menuOpen = false }) {
                                DropdownMenuItem(
                                    text = { Text("Profiles") },
                                    leadingIcon = { Icon(Icons.Outlined.Dns, null) },
                                    onClick = { menuOpen = false; onProfiles() },
                                )
                                DropdownMenuItem(
                                    text = { Text("Usage") },
                                    leadingIcon = { Icon(Icons.Outlined.DataUsage, null) },
                                    onClick = { menuOpen = false; onUsage() },
                                )
                                DropdownMenuItem(
                                    text = { Text("Files from host") },
                                    leadingIcon = { Icon(Icons.Outlined.CloudDownload, null) },
                                    onClick = { menuOpen = false; onDrop() },
                                )
                                DropdownMenuItem(
                                    text = { Text("Settings") },
                                    leadingIcon = { Icon(Icons.Outlined.Settings, null) },
                                    onClick = { menuOpen = false; onSettings() },
                                )
                            }
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.background,
                    ),
                )
                // Only when a project filter is on: it is the one filter that
                // has no home in the bar above.
                projectFilter?.let { project ->
                    ProjectFilterRow(project) { vm.setProjectFilter(null) }
                }
                Hairline()
            }
        },
        bottomBar = {
            ConnLine(
                daemons = profileState.enabled.map { it.displayName to conn[it.id] },
                onRetry = vm::retryConnections,
            )
        },
        floatingActionButton = {
            if (profileState.enabled.isNotEmpty()) {
                ExtendedFloatingActionButton(
                    onClick = onNewSession,
                    icon = { Icon(Icons.Outlined.Add, null) },
                    text = { Text("New session") },
                    containerColor = pal.accent,
                    contentColor = pal.onAccent,
                )
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        PullToRefreshBox(
            isRefreshing = state.loading,
            onRefresh = vm::refreshEverything,
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            // A dead daemon must never read as "it has no sessions". With
            // several daemons the daemon button (its red dot, its menu) and
            // the line under the list say which and why; with one there is no
            // such button, so it is said in a banner here.
            val problems = if (profileState.enabled.size >= 2) emptyList() else
                state.problems.map { (id, msg) ->
                    (profileState.byId(id)?.displayName ?: "Daemon") to msg
                }
            when {
                profileState.profiles.isEmpty() -> EmptyState(
                    title = "No daemons yet",
                    body = "Add a profile pointing at agentremoted — multi on your " +
                        "Mac, one for Grok on your server. Both land in this one list.",
                    action = {
                        TextButton(onClick = onProfiles) { Text("Add a profile") }
                    },
                )

                // An empty list is ambiguous — it could mean "no sessions" or
                // "the daemon refused us". Never show the friendly empty state
                // while a profile is actually broken.
                rows.isEmpty() && !state.loading -> Column {
                    ProblemList(problems, onProfiles)
                    EmptyState(
                        title = when {
                            state.problems.isNotEmpty() -> "Nothing to show"
                            query.isBlank() -> "Nothing here yet"
                            else -> "No matches"
                        },
                        body = when {
                            state.problems.isNotEmpty() ->
                                "No daemon in this view returned any sessions."
                            query.isBlank() ->
                                "Start a session and it will show up here, whichever daemon " +
                                    "runs it."
                            else -> "No session on any daemon mentions “$query”."
                        },
                    )
                }

                else -> SessionList(
                    rows = rows,
                    working = working,
                    blocked = blocked,
                    problems = problems,
                    searching = query.isNotBlank(),
                    showFocusState = settings.focusMode,
                    listState = listState,
                    onOpen = { ref, provider ->
                        // Opening it dims that row's finished tag.
                        vm.markSeen(ref)
                        onOpen(ref, provider)
                    },
                    onLongPress = { actionRow = it },
                    onProfiles = onProfiles,
                )
            }
        }
    }

    actionRow?.let { target ->
        SessionActionsDialog(
            row = target,
            busy = renaming,
            onRename = { title, done -> vm.rename(target.ref, title, done) },
            onRegenerate = { done -> vm.regenerateTitle(target.ref, done) },
            onBoard = { member -> vm.setFocusMember(target.ref, member) },
            onDismiss = { actionRow = null },
        )
    }

    LaunchedEffect(rows.size) {
        if (listState.firstVisibleItemIndex <= 1) listState.animateScrollToItem(0)
    }
    // The daemon being shown was removed or switched off: show all again,
    // rather than an empty list with no way to see why.
    LaunchedEffect(profileState.enabled, profileFilter) {
        val picked = profileFilter
        if (picked != null && profileState.enabled.none { it.id == picked }) {
            vm.setProfileFilter(null)
        }
    }
}

/**
 * Is what the list shows live? One line under the list: "Live" when every
 * daemon is connected, otherwise each one that is not, by name, with Retry.
 * Words as well as colour.
 */
@Composable
private fun ConnLine(daemons: List<Pair<String, ConnState?>>, onRetry: () -> Unit) {
    if (daemons.isEmpty()) return
    val pal = palette
    val notLive = daemons.filter { it.second != ConnState.LIVE }
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.background)
            .navigationBarsPadding(),
    ) {
        Hairline()
        Row(
            Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 4.dp)
                .semantics { liveRegion = LiveRegionMode.Polite },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val items: List<Pair<String, ConnState?>> = if (notLive.isEmpty()) {
                listOf(
                    (if (daemons.size > 1) "Live · ${daemons.size} daemons" else "Live") to
                        ConnState.LIVE,
                )
            } else {
                notLive.map { (name, state) ->
                    val words = connLabel(state)
                    (if (daemons.size > 1) "$name $words"
                    else words.replaceFirstChar { it.uppercase() }) to state
                }
            }
            Row(
                Modifier
                    .weight(1f)
                    .padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                items.take(3).forEachIndexed { index, (text, state) ->
                    if (index > 0) Spacer(Modifier.width(12.dp))
                    val tint = connTint(state)
                    Box(
                        Modifier
                            .size(7.dp)
                            .clip(CircleShape)
                            .background(tint),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text,
                        style = MaterialTheme.typography.labelSmall,
                        color = if (state == ConnState.LIVE) pal.dim else tint,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
            }
            if (notLive.any { it.second == ConnState.OFFLINE || it.second == ConnState.RECONNECTING }) {
                TextButton(onClick = onRetry) { Text("Retry") }
            }
        }
    }
}

@Composable
private fun SearchField(value: String, onValueChange: (String) -> Unit, onClose: () -> Unit) {
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    LaunchedEffect(Unit) { focus.requestFocus() }
    TextField(
        value = value,
        onValueChange = onValueChange,
        placeholder = { Text("Search titles and messages") },
        singleLine = true,
        trailingIcon = {
            IconButton(onClick = onClose) {
                Icon(Icons.Outlined.Close, contentDescription = "Close search")
            }
        },
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
        keyboardActions = KeyboardActions(onSearch = { keyboard?.hide() }),
        colors = TextFieldDefaults.colors(
            focusedContainerColor = Color.Transparent,
            unfocusedContainerColor = Color.Transparent,
            focusedIndicatorColor = Color.Transparent,
            unfocusedIndicatorColor = Color.Transparent,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .focusRequester(focus),
    )
}

/**
 * Focus | All. A two-way switch rather than a "Focus" chip beside an "All"
 * chip: the chip never said what the other choice was, and the "All" next to
 * it meant all daemons.
 */
@Composable
private fun FocusSwitch(focusMode: Boolean, count: Int, onChange: (Boolean) -> Unit) {
    val pal = palette
    val shape = RoundedCornerShape(50)
    Row(
        Modifier
            .clip(shape)
            .border(1.dp, pal.hairline, shape)
            .background(MaterialTheme.colorScheme.surface)
            .padding(2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SwitchSide("Focus", focusMode, count) { if (!focusMode) onChange(true) }
        SwitchSide("All", !focusMode, 0) { if (focusMode) onChange(false) }
    }
}

@Composable
private fun SwitchSide(label: String, selected: Boolean, count: Int, onClick: () -> Unit) {
    val pal = palette
    val shape = RoundedCornerShape(50)
    Row(
        Modifier
            .clip(shape)
            .background(if (selected) pal.accent.copy(alpha = 0.18f) else Color.Transparent)
            .selectable(selected = selected, role = Role.Tab, onClick = onClick)
            .heightIn(min = 36.dp)
            .padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelLarge,
            color = if (selected) pal.accent else pal.dim,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
        )
        if (count > 0) {
            Spacer(Modifier.width(6.dp))
            Text(
                count.toString(),
                style = MaterialTheme.typography.labelSmall,
                color = pal.accent,
                modifier = Modifier
                    .clip(shape)
                    .background(pal.accent.copy(alpha = 0.22f))
                    .padding(horizontal = 6.dp, vertical = 1.dp),
            )
        }
    }
}

/**
 * Which daemon's sessions to show. A funnel while it is every daemon, the
 * picked daemon's name in its colour otherwise; the menu says how each one is
 * doing. It replaces a chip per daemon, which scrolled out of sight from the
 * third on.
 */
private data class DaemonChoice(
    val id: String,
    val name: String,
    val provider: String,
    val host: String,
    val version: String,
)

/** A daemon's standing, in a word. Never colour alone. */
private fun connWord(state: ConnState?): String = when (state) {
    ConnState.LIVE -> "Connected"
    ConnState.RECONNECTING -> "Reconnecting…"
    ConnState.OFFLINE -> "Offline"
    ConnState.CONNECTING, null -> "Connecting…"
}

@Composable
private fun DaemonFilter(
    profiles: List<DaemonChoice>,
    selected: String?,
    conn: Map<String, ConnState>,
    problems: Map<String, String>,
    onSelect: (String?) -> Unit,
) {
    val pal = palette
    var open by remember { mutableStateOf(false) }
    val picked = profiles.firstOrNull { it.id == selected }
    // A daemon whose list could not be fetched is offline whatever its
    // stream last said.
    fun stateOf(id: String): ConnState? = if (problems[id] != null) ConnState.OFFLINE else conn[id]
    Box {
        if (picked == null) {
            IconButton(onClick = { open = true }) {
                Box {
                    Icon(
                        Icons.Outlined.FilterList,
                        contentDescription = "Showing every daemon. Choose one",
                    )
                    // One of them cannot be reached: say so before the menu is open.
                    if (problems.isNotEmpty()) {
                        Box(
                            Modifier
                                .align(Alignment.TopEnd)
                                .size(7.dp)
                                .clip(CircleShape)
                                .background(pal.danger),
                        )
                    }
                }
            }
        } else {
            val tint = Accent.forProvider(picked.provider).tint
            val shape = RoundedCornerShape(8.dp)
            Row(
                Modifier
                    .padding(horizontal = 4.dp)
                    .clip(shape)
                    .background(tint.copy(alpha = 0.16f))
                    .border(1.dp, tint.copy(alpha = 0.45f), shape)
                    .clickable(onClickLabel = "choose a daemon") { open = true }
                    .heightIn(min = 36.dp)
                    .padding(start = 10.dp, end = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    picked.name,
                    style = MaterialTheme.typography.labelLarge,
                    color = tint,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.widthIn(max = 96.dp),
                )
                Icon(
                    Icons.Outlined.ArrowDropDown,
                    contentDescription = null,
                    tint = tint,
                    modifier = Modifier.size(18.dp),
                )
            }
        }
        DropdownMenu(
            open,
            onDismissRequest = { open = false },
            modifier = Modifier.widthIn(min = 272.dp, max = 340.dp),
        ) {
            // One entry: a dot in the colour of its state, the name, and
            // under it the state in words, then what identifies the daemon.
            @Composable
            fun entry(
                id: String?,
                name: String,
                state: ConnState?,
                word: String,
                detail: String,
            ) {
                val tint = connTint(state)
                val chosen = selected == id
                DropdownMenuItem(
                    text = {
                        Column(Modifier.padding(vertical = 4.dp)) {
                            Text(
                                name,
                                fontWeight = if (chosen) FontWeight.SemiBold else FontWeight.Normal,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                buildAnnotatedString {
                                    withStyle(SpanStyle(color = if (state == ConnState.LIVE ||
                                        state == ConnState.OFFLINE || state == ConnState.RECONNECTING
                                    ) tint else pal.dim)) { append(word) }
                                    if (detail.isNotEmpty()) append(" · $detail")
                                },
                                style = MaterialTheme.typography.labelSmall,
                                color = if (state == ConnState.OFFLINE) pal.danger else pal.dim,
                                maxLines = 3,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    },
                    leadingIcon = {
                        // Offline is a ring, not a filled dot: shape as well as colour.
                        Box(
                            Modifier
                                .size(9.dp)
                                .clip(CircleShape)
                                .then(
                                    if (state == ConnState.OFFLINE) {
                                        Modifier.border(1.5.dp, tint, CircleShape)
                                    } else Modifier.background(tint),
                                ),
                        )
                    },
                    // No empty slot on the others: the line under the name
                    // gets that width.
                    trailingIcon = if (chosen) {
                        { Icon(Icons.Outlined.Check, contentDescription = "selected") }
                    } else null,
                    onClick = { open = false; onSelect(id) },
                )
            }
            Text(
                "Show sessions from",
                style = MaterialTheme.typography.labelSmall,
                color = pal.dim,
                modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 2.dp),
            )
            val up = profiles.count { stateOf(it.id) == ConnState.LIVE }
            entry(
                id = null,
                name = "All daemons",
                state = when (up) {
                    profiles.size -> ConnState.LIVE
                    0 -> ConnState.OFFLINE
                    else -> ConnState.RECONNECTING
                },
                word = if (up == profiles.size) "$up connected"
                else "$up of ${profiles.size} connected",
                detail = "",
            )
            Hairline()
            profiles.forEach { p ->
                val state = stateOf(p.id)
                entry(
                    id = p.id,
                    name = p.name,
                    state = state,
                    word = connWord(state),
                    detail = problems[p.id]
                        ?: listOf(p.host, if (p.version.isNotEmpty()) "v${p.version}" else "")
                            .filter { it.isNotEmpty() }.joinToString(" · "),
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProjectFilterRow(project: ProjectFilter, onClear: () -> Unit) {
    Row(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) {
        FilterChip(
            selected = true,
            onClick = onClear,
            label = { Text(project.name) },
            trailingIcon = {
                Icon(
                    Icons.Outlined.Close,
                    contentDescription = "Clear project filter",
                    modifier = Modifier.size(16.dp),
                )
            },
            leadingIcon = {
                Icon(
                    Icons.AutoMirrored.Outlined.List,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                )
            },
        )
    }
}

/**
 * Rename a session, ask the daemon to derive a title, or take the card off the
 * Focus. Reached by long-pressing a row, so the row itself keeps its layout.
 *
 * Renaming exists because the derived names are often unrecognisable when a
 * dozen projects run in parallel — which is the whole point of Focus.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SessionActionsDialog(
    row: SessionRow,
    busy: String?,
    onRename: (String, (String?) -> Unit) -> Unit,
    onRegenerate: ((String?, String?) -> Unit) -> Unit,
    onBoard: (Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    val pal = palette
    val clip = rememberClip()
    var title by remember(row.ref.key) { mutableStateOf(row.session.title) }
    var note by remember(row.ref.key) { mutableStateOf<String?>(null) }
    val member = row.session.focus

    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Rename session") },
        text = {
            Column {
                TextField(
                    value = title,
                    onValueChange = { title = it.take(120) },
                    singleLine = true,
                    placeholder = { Text("e.g. BB10 pager chime") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    busy ?: note
                        ?: "Leave it empty to go back to the name the agent derived.",
                    style = MaterialTheme.typography.labelSmall,
                    color = pal.dim,
                )
                Spacer(Modifier.height(12.dp))
                TextButton(
                    onClick = {
                        onRegenerate { fresh, err ->
                            if (fresh != null) title = fresh
                            note = err ?: "Generated from the transcript. Save to keep it."
                        }
                    },
                    enabled = busy == null,
                ) {
                    Icon(Icons.Outlined.AutoAwesome, contentDescription = null,
                        modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Regenerate from transcript")
                }
                if (row.ref.sessionId.isNotEmpty()) {
                    TextButton(onClick = { clip.copy(row.ref.sessionId); onDismiss() }) {
                        Icon(Icons.Outlined.ContentCopy, contentDescription = null,
                            modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Copy session id")
                    }
                }
                TextButton(onClick = { onBoard(!member); onDismiss() }) {
                    Icon(
                        if (member) Icons.Outlined.Check else Icons.Outlined.Star,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp),
                        tint = if (member) pal.dim else pal.ok,
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        if (member) "Done — take it off Focus"
                        else "Track in Focus",
                        color = if (member) pal.dim else pal.ok,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    onRename(title) { err ->
                        if (err == null) onDismiss() else note = err
                    }
                },
                enabled = busy == null,
            ) {
                Icon(Icons.Outlined.Edit, contentDescription = null,
                    modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Save")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun SessionList(
    rows: List<SessionRow>,
    working: Set<String>,
    blocked: Set<String>,
    problems: List<Pair<String, String>>,
    searching: Boolean,
    showFocusState: Boolean,
    listState: androidx.compose.foundation.lazy.LazyListState,
    onOpen: (SessionRef, provider: String) -> Unit,
    onLongPress: (SessionRow) -> Unit,
    onProfiles: () -> Unit,
) {
    LazyColumn(
        state = listState,
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(bottom = 96.dp),
    ) {
        if (problems.isNotEmpty()) {
            item(key = "problems") { ProblemList(problems, onProfiles) }
        }

        var lastDay = ""
        rows.forEach { row ->
            val day = dayLabel(row.sortKey)
            if (day != lastDay && !searching) {
                lastDay = day
                item(key = "day-$day-${row.ref.key}") { DayHeader(day) }
            }
            item(key = row.ref.key) {
                SessionCard(
                    row = row,
                    working = working.contains(row.ref.key),
                    blocked = blocked.contains(row.ref.key),
                    searching = searching,
                    showFocusState = showFocusState,
                    onClick = { onOpen(row.ref, row.provider) },
                    onLongClick = { onLongPress(row) },
                )
                Hairline(inset = 16)
            }
        }
    }
}

@Composable
private fun ProblemList(problems: List<Pair<String, String>>, onProfiles: () -> Unit) {
    if (problems.isEmpty()) return
    Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
        problems.forEach { (name, message) ->
            ErrorBanner(
                text = "$name: $message",
                action = { TextButton(onClick = onProfiles) { Text("Fix") } },
                modifier = Modifier.padding(bottom = 6.dp),
            )
        }
    }
}

@Composable
private fun DayHeader(label: String) {
    Text(
        text = label,
        style = MaterialTheme.typography.labelSmall,
        color = palette.dim,
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.background)
            .padding(start = 16.dp, top = 14.dp, bottom = 6.dp),
    )
}

private fun dayLabel(epochMs: Long): String {
    if (epochMs <= 0) return "Older"
    val zone = ZoneId.systemDefault()
    val day = Instant.ofEpochMilli(epochMs).atZone(zone).toLocalDate()
    val today = Instant.now().atZone(zone).toLocalDate()
    return when (day) {
        today -> "Today"
        today.minusDays(1) -> "Yesterday"
        else -> Time.relativeStamp(epochMs)
    }
}

@Composable
private fun SessionCard(
    row: SessionRow,
    working: Boolean,
    blocked: Boolean,
    searching: Boolean,
    showFocusState: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
) {
    val pal = palette
    val accent = Accent.forProvider(row.provider)
    Column(
        Modifier
            .fillMaxWidth()
            // Long press: rename / retitle / focus, without adding controls to
            // the row itself.
            .combinedClickable(
                onClickLabel = "open",
                onLongClickLabel = "rename or change focus",
                onClick = onClick,
                onLongClick = onLongClick,
            )
            // The breathing dot says this to the eye; say it to TalkBack too.
            .semantics {
                when {
                    blocked -> stateDescription = "waiting for your answer"
                    working -> stateDescription = "working"
                }
            }
            .padding(horizontal = 16.dp, vertical = 10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (working) {
                WorkingPulse(accent.tint, Modifier.padding(end = 8.dp))
            }
            Text(
                // Don't echo attachment filenames — the transcript chip already shows them.
                text = listTitle(row.session),
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text = Time.relativeStamp(row.sortKey),
                style = MaterialTheme.typography.labelSmall,
                color = pal.dim,
            )
        }

        Spacer(Modifier.height(5.dp))

        // One line: which daemon, where, what state it is in, and a short id
        // at the far end. Folder and branch are words, not pills, and give
        // way (they share what is left) so the state and the id are never cut.
        Row(verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
                ProviderChip(row.provider, row.profileName, Modifier.widthIn(max = 132.dp))
                val project = row.session.cwd.trimEnd('/').substringAfterLast('/')
                if (project.isNotEmpty()) {
                    Spacer(Modifier.width(7.dp))
                    Text(
                        project,
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.8f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        // The folder keeps more of its name than the branch.
                        modifier = Modifier.weight(2f, fill = false),
                    )
                }
                if (row.session.gitBranch.isNotEmpty()) {
                    Spacer(Modifier.width(7.dp))
                    Text(
                        "⑂ ${row.session.gitBranch}",
                        style = MaterialTheme.typography.labelMedium,
                        color = pal.dim,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
                // The state tag, in the daemon's words. "Needs answer" shows in
                // either view; the rest only in Focus, where the row was
                // enrolled (in All it is noise). Derived from the live status
                // stream, so a turn ending is visible without a refresh.
                val focusState = liveFocusState(row.session.focusState, working, blocked)
                if (blocked) {
                    Spacer(Modifier.width(7.dp))
                    FocusPill("needs_answer")
                } else if (showFocusState && focusState.isNotEmpty()) {
                    Spacer(Modifier.width(7.dp))
                    // A turn that ended while we watched the stream is unread
                    // by definition: you were looking at the list.
                    FocusPill(
                        focusState,
                        unread = row.session.focusUnread ||
                            row.session.focusState == "working",
                    )
                }
            }
            // Enough of the id to tell two sessions apart. Long-press the row
            // to copy all of it.
            if (row.ref.sessionId.isNotEmpty()) {
                Spacer(Modifier.width(8.dp))
                Text(
                    row.ref.sessionId.take(8),
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = pal.dim.copy(alpha = 0.75f),
                    maxLines = 1,
                )
            }
        }

        val preview = listPreview(row.session, searching)
        if (preview.isNotBlank()) {
            Spacer(Modifier.height(5.dp))
            Text(
                // One line of what was last said, as prose: the markdown marks
                // of a reply ("**Progress:**") are noise at this size.
                text = preview.replace(MARKDOWN_MARKS, "").replace('\n', ' ').trim(),
                style = MaterialTheme.typography.bodySmall,
                color = pal.dim,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

private val MARKDOWN_MARKS = Regex("""\*\*|__|`""")

/**
 * The focus tag as of *now*, not as of the last list fetch.
 *
 * The status stream carries only in-flight jobs, so it proves `needs_answer`
 * and `working` outright and refutes them once the job leaves the stream. It
 * cannot tell `failed` from `turn_finished` — both are absent — so those keep
 * the daemon's value, which the next refresh corrects.
 */
private fun liveFocusState(said: String, working: Boolean, blocked: Boolean): String {
    if (blocked) return "needs_answer"
    if (working) return "working"
    if (said == "working" || said == "needs_answer") return "turn_finished"
    return said
}

/** Session list title: never a bare attachment filename (chip lives in chat). */
private fun listTitle(session: SessionDto): String {
    val t = session.title.trim()
    if (t.isEmpty()) return "Untitled session"
    if (looksLikeFilenameTitle(t) || isAttachmentOnly(t)) {
        val folder = session.cwd.trimEnd('/').substringAfterLast('/')
        val label = attachmentLabel(t)
        return if (folder.isNotEmpty()) "$label · $folder" else label
    }
    return t
}

private fun listPreview(session: SessionDto, searching: Boolean): String {
    val raw = if (searching) session.snippet else session.lastText
    val text = raw.replace('\n', ' ').trim()
    if (text.isEmpty() || isAttachmentOnly(text)) return ""
    val title = listTitle(session)
    if (text.equals(title, ignoreCase = true)) return ""
    if (looksLikeFilenameTitle(text) && looksLikeFilenameTitle(title)) return ""
    return text
}

private fun isAttachmentOnly(text: String): Boolean {
    val lines = text.lines().map { it.trim() }.filter { it.isNotEmpty() }
    if (lines.isEmpty()) return false
    return lines.all { ATTACHED_LINE.matches(it) }
}

private fun looksLikeFilenameTitle(text: String): Boolean {
    val t = text.trim()
    if (t.isEmpty()) return false
    if (FILENAME_EXT.containsMatchIn(t)) return true
    val low = t.lowercase()
    return low.startsWith("screenshot") && FILENAME_EXT.containsMatchIn(low)
}

private fun attachmentLabel(text: String): String {
    val fromAttach = ATTACHED_ANY.find(text)?.groupValues?.getOrNull(1)
    val name = (fromAttach ?: text).substringAfterLast('/').substringAfterLast('\\')
    val ext = name.substringAfterLast('.', "").lowercase()
    return when (ext) {
        "png", "jpg", "jpeg", "gif", "webp", "heic", "bmp" -> "Image"
        "pdf" -> "PDF"
        "mp4", "mov", "webm" -> "Video"
        "mp3", "wav", "m4a", "aac" -> "Audio"
        else -> "Attachment"
    }
}

private val ATTACHED_LINE = Regex("""^\[attached:\s*[^\]]+\]$""", RegexOption.IGNORE_CASE)
private val ATTACHED_ANY = Regex("""\[attached:\s*([^\]]+)\]""", RegexOption.IGNORE_CASE)
private val FILENAME_EXT = Regex(
    """\.(png|jpe?g|gif|webp|heic|bmp|pdf|mov|mp4|m4a|wav|zip)$""",
    RegexOption.IGNORE_CASE,
)

