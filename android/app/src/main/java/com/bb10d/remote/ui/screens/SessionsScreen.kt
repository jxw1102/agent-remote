package com.bb10d.remote.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.CloudDownload
import androidx.compose.material.icons.outlined.DataUsage
import androidx.compose.material.icons.outlined.Dns
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Settings
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
import com.bb10d.remote.data.SessionRef
import com.bb10d.remote.data.SessionRow
import com.bb10d.remote.data.Time
import com.bb10d.remote.ui.components.EmptyState
import com.bb10d.remote.ui.components.ErrorBanner
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
    onOpen: (SessionRef) -> Unit,
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

    var searching by remember { mutableStateOf(false) }
    var menuOpen by remember { mutableStateOf(false) }
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
                        } else {
                            Column {
                                Text("Sessions", fontWeight = FontWeight.SemiBold)
                                val subtitle = subtitleFor(rows.size, working.size)
                                if (subtitle.isNotEmpty()) {
                                    Text(
                                        subtitle,
                                        style = MaterialTheme.typography.labelSmall,
                                        color = pal.dim,
                                    )
                                }
                            }
                        }
                    },
                    actions = {
                        if (!searching) {
                            IconButton(onClick = { searching = true }) {
                                Icon(Icons.Outlined.Search, contentDescription = "Search")
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
                if (profileState.profiles.size > 1 || projectFilter != null) {
                    FilterRow(
                        profiles = profileState.profiles.map {
                            Triple(it.id, it.displayName, it.provider)
                        },
                        selected = profileFilter,
                        project = projectFilter,
                        onSelect = vm::setProfileFilter,
                        onClearProject = { vm.setProjectFilter(null) },
                    )
                }
                Hairline()
            }
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
            val problems = state.problems.map { (id, msg) ->
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
                            problems.isNotEmpty() -> "Nothing to show"
                            query.isBlank() -> "Nothing here yet"
                            else -> "No matches"
                        },
                        body = when {
                            problems.isNotEmpty() ->
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
                    listState = listState,
                    onOpen = onOpen,
                    onProfiles = onProfiles,
                )
            }
        }
    }

    LaunchedEffect(rows.size) {
        if (listState.firstVisibleItemIndex <= 1) listState.animateScrollToItem(0)
    }
}

private fun subtitleFor(count: Int, working: Int): String = when {
    working > 0 && count > 0 -> "$count · $working working"
    working > 0 -> "$working working"
    count > 0 -> "$count"
    else -> ""
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FilterRow(
    profiles: List<Triple<String, String, String>>,
    selected: String?,
    project: ProjectFilter?,
    onSelect: (String?) -> Unit,
    onClearProject: () -> Unit,
) {
    androidx.compose.foundation.lazy.LazyRow(
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        item {
            FilterChip(
                selected = selected == null,
                onClick = { onSelect(null) },
                label = { Text("All") },
            )
        }
        items(profiles, key = { it.first }) { (id, name, provider) ->
            val accent = Accent.forProvider(provider)
            FilterChip(
                selected = selected == id,
                onClick = { onSelect(if (selected == id) null else id) },
                label = { Text(name) },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = accent.tint.copy(alpha = 0.2f),
                    selectedLabelColor = accent.tint,
                ),
            )
        }
        if (project != null) {
            item {
                FilterChip(
                    selected = true,
                    onClick = onClearProject,
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
    }
}

@Composable
private fun SessionList(
    rows: List<SessionRow>,
    working: Set<String>,
    blocked: Set<String>,
    problems: List<Pair<String, String>>,
    searching: Boolean,
    listState: androidx.compose.foundation.lazy.LazyListState,
    onOpen: (SessionRef) -> Unit,
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
                    onClick = { onOpen(row.ref) },
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
    onClick: () -> Unit,
) {
    val pal = palette
    val accent = Accent.forProvider(row.provider)
    Column(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 12.dp),
    ) {
        Row(verticalAlignment = Alignment.Top) {
            if (working) {
                Box(Modifier.padding(top = 6.dp, end = 8.dp)) {
                    WorkingPulse(accent.tint)
                }
            }
            Text(
                text = row.session.title.ifBlank { "Untitled session" },
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 2,
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

        Spacer(Modifier.height(6.dp))

        if (blocked) {
            Spacer(Modifier.height(6.dp))
            Row(
                Modifier
                    .clip(RoundedCornerShape(6.dp))
                    .background(pal.warn.copy(alpha = 0.16f))
                    .padding(horizontal = 8.dp, vertical = 3.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                WorkingPulse(pal.warn, size = 6)
                Spacer(Modifier.width(6.dp))
                Text(
                    text = "Waiting for your answer",
                    style = MaterialTheme.typography.labelSmall,
                    color = pal.warn,
                )
            }
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            ProviderChip(row.provider, row.profileName)
            val project = row.session.cwd.trimEnd('/').substringAfterLast('/')
            if (project.isNotEmpty()) {
                Spacer(Modifier.width(6.dp))
                MetaPill(project)
            }
            if (row.session.gitBranch.isNotEmpty()) {
                Spacer(Modifier.width(6.dp))
                MetaPill("⑂ ${row.session.gitBranch}")
            }
        }

        val preview = if (searching) row.session.snippet else row.session.lastText
        if (preview.isNotBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(
                text = preview.replace('\n', ' ').trim(),
                style = MaterialTheme.typography.bodySmall,
                color = pal.dim,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

