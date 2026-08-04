package com.bb10d.remote.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.data.ExecMode
import com.bb10d.remote.data.Profile
import com.bb10d.remote.data.ProjectDto
import com.bb10d.remote.ui.components.ErrorBanner
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.MetaPill
import com.bb10d.remote.ui.components.SectionLabel
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.palette

/**
 * Starting a session in a merged app has one extra step the single-provider
 * BB10 apps did not: the daemon is a choice, not a given. It is asked first
 * and everything below it — projects, models, whether a cwd is even required —
 * re-reads from that daemon's capabilities.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NewSessionScreen(
    vm: NewSessionViewModel,
    onBack: () -> Unit,
    onStarted: (profileId: String, jobId: String) -> Unit,
) {
    val profileState by vm.profiles.collectAsStateWithLifecycle()
    val selectedId by vm.profileId.collectAsStateWithLifecycle()
    val harness by vm.harness.collectAsStateWithLifecycle()
    val projects by vm.projects.collectAsStateWithLifecycle()
    val loadingProjects by vm.loadingProjects.collectAsStateWithLifecycle()
    val error by vm.error.collectAsStateWithLifecycle()
    val starting by vm.starting.collectAsStateWithLifecycle()

    val candidates = profileState.enabled
    val profile = candidates.firstOrNull { it.id == selectedId } ?: candidates.firstOrNull()
    val harnesses = profile?.caps?.harnesses().orEmpty()
    val activeHarness = harness.ifBlank { harnesses.firstOrNull() ?: profile?.provider.orEmpty() }
    val multiHost = profile?.caps?.isMulti == true || harnesses.size > 1
    val accent = Accent.forProvider(activeHarness.ifBlank { profile?.provider })
    val pal = palette

    var cwd by remember(profile?.id) { mutableStateOf("") }
    var prompt by remember { mutableStateOf("") }
    var showAdvanced by remember { mutableStateOf(false) }

    LaunchedEffect(profile?.id) {
        profile?.let { vm.selectProfile(it.id) }
    }

    val caps = profile?.caps
    val cwdRequired = caps?.requiresCwd(activeHarness) ?: true
    val canStart = profile != null && prompt.isNotBlank() && !starting &&
        (!cwdRequired || cwd.isNotBlank())

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("New session", fontWeight = FontWeight.SemiBold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {
            Column(
                Modifier
                    .weight(1f)
                    .verticalScroll(rememberScrollState()),
            ) {
                if (candidates.size > 1) {
                    SectionLabel("Daemon")
                    candidates.forEach { candidate ->
                        ProfileChoice(
                            profile = candidate,
                            selected = candidate.id == profile?.id,
                            onClick = {
                                vm.selectProfile(candidate.id)
                                cwd = ""
                            },
                        )
                    }
                } else if (candidates.isEmpty()) {
                    Text(
                        "No enabled profiles. Add one in Profiles first.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = pal.dim,
                        modifier = Modifier.padding(16.dp),
                    )
                } else {
                    // Single multi-harness host: still show which daemon it is.
                    val only = candidates.first()
                    Text(
                        only.displayName + " · " + only.hostLabel,
                        style = MaterialTheme.typography.bodySmall,
                        color = pal.dim,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    )
                }

                if (multiHost || harnesses.size > 1) {
                    SectionLabel(if (candidates.size > 1) "Harness" else "Which harness?")
                    FlowRow(
                        Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        harnesses.ifEmpty { listOf("claude", "grok") }.forEach { h ->
                            val hAccent = Accent.forProvider(h)
                            val selected = h == activeHarness
                            FilterChip(
                                selected = selected,
                                onClick = {
                                    vm.selectHarness(h)
                                    cwd = ""
                                },
                                label = { Text(hAccent.label) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = hAccent.tint.copy(alpha = 0.22f),
                                    selectedLabelColor = hAccent.tint,
                                ),
                            )
                        }
                    }
                }

                Hairline()

                SectionLabel(
                    if (cwdRequired) "Project folder (required)" else "Project folder (optional)",
                )
                OutlinedTextField(
                    value = cwd,
                    onValueChange = { cwd = it },
                    placeholder = {
                        Text(
                            if (cwdRequired) "/Users/you/code/project"
                            else "leave empty for the daemon's workspace",
                        )
                    },
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium.copy(
                        fontFamily = FontFamily.Monospace,
                    ),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp),
                )

                if (loadingProjects) {
                    Row(
                        Modifier.padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(10.dp))
                        Text(
                            "Reading projects…",
                            style = MaterialTheme.typography.bodySmall,
                            color = pal.dim,
                        )
                    }
                } else if (projects.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    projects.take(if (showAdvanced) projects.size else 6).forEach { project ->
                        ProjectRow(project, cwd == project.cwd) { cwd = project.cwd }
                    }
                    if (projects.size > 6 && !showAdvanced) {
                        TextButton(
                            onClick = { showAdvanced = true },
                            modifier = Modifier.padding(start = 8.dp),
                        ) { Text("Show all ${projects.size} projects") }
                    }
                }

                Hairline(modifier = Modifier.padding(top = 12.dp))

                SectionLabel("First message")
                OutlinedTextField(
                    value = prompt,
                    onValueChange = { prompt = it },
                    placeholder = { Text("What should ${accent.label} do?") },
                    minLines = 4,
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 120.dp)
                        .padding(horizontal = 16.dp),
                )

                if (profile != null) {
                    Spacer(Modifier.height(16.dp))
                    ComposerDefaults(vm = vm, profile = profile, harness = activeHarness)
                }

                if (error != null) {
                    Spacer(Modifier.height(12.dp))
                    ErrorBanner(error!!, modifier = Modifier.padding(horizontal = 16.dp))
                }
                Spacer(Modifier.height(24.dp))
            }

            Hairline()
            Row(
                Modifier
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .padding(16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = profile?.let {
                        val h = Accent.forProvider(activeHarness).label
                        "${it.displayName} · $h · ${ExecMode.short(vm.execModeFor(it, activeHarness))}"
                    }.orEmpty(),
                    style = MaterialTheme.typography.bodySmall,
                    color = pal.dim,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(12.dp))
                Button(
                    onClick = {
                        val target = profile ?: return@Button
                        vm.start(target, cwd.trim(), prompt.trim()) { jobId ->
                            onStarted(target.id, jobId)
                        }
                    },
                    enabled = canStart,
                ) {
                    if (starting) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    } else {
                        Icon(Icons.AutoMirrored.Outlined.Send, contentDescription = null)
                    }
                    Spacer(Modifier.width(8.dp))
                    Text("Start")
                }
            }
        }
    }
}

@Composable
private fun ProfileChoice(profile: Profile, selected: Boolean, onClick: () -> Unit) {
    val hs = profile.caps.harnesses()
    // Multi host uses neutral chrome; single harness keeps brand accent.
    val accent = if (hs.size > 1) Accent.forProvider("") else Accent.forProvider(
        hs.firstOrNull() ?: profile.provider,
    )
    val pal = palette
    val harnessLine = if (hs.size > 1) {
        hs.joinToString(" · ") { Accent.forProvider(it).label }
    } else {
        accent.label.takeIf { profile.provider.isNotEmpty() || hs.isNotEmpty() }
            ?: hs.firstOrNull()?.let { Accent.forProvider(it).label }
    }
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 3.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(if (selected) accent.tint.copy(alpha = 0.12f) else Color.Transparent)
            .border(
                1.dp,
                if (selected) accent.tint.copy(alpha = 0.5f) else pal.hairline,
                RoundedCornerShape(10.dp),
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .size(10.dp)
                .clip(CircleShape)
                .background(accent.tint),
        )
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(profile.displayName, style = MaterialTheme.typography.bodyLarge)
            Text(
                text = listOfNotNull(harnessLine, profile.hostLabel).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = pal.dim,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        if (selected) {
            Icon(Icons.Outlined.CheckCircle, contentDescription = null, tint = accent.tint)
        }
    }
}

@Composable
private fun ProjectRow(project: ProjectDto, selected: Boolean, onClick: () -> Unit) {
    val pal = palette
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Outlined.FolderOpen,
            contentDescription = null,
            tint = if (selected) pal.accent else pal.dim,
            modifier = Modifier.size(18.dp),
        )
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                project.name.ifBlank { project.id },
                style = MaterialTheme.typography.bodyMedium,
                color = if (selected) pal.accent else MaterialTheme.colorScheme.onSurface,
            )
            Text(
                project.cwd,
                style = MaterialTheme.typography.bodySmall,
                color = pal.dim,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        MetaPill("${project.sessionCount}")
    }
}

/** Model / effort / execution mode, all gated on what the daemon reported. */
@Composable
private fun ComposerDefaults(
    vm: NewSessionViewModel,
    profile: Profile,
    harness: String = "",
) {
    var expanded by remember { mutableStateOf(false) }
    val pal = palette
    val models = profile.caps.modelsFor(harness.ifBlank { null })
    val efforts = profile.caps.effortsFor(harness.ifBlank { null })
    val detailCaps = harness.takeIf { it.isNotBlank() }
        ?.let { profile.caps.providerDetails[it]?.caps }
    val interactive = profile.caps.interactiveFor(harness.ifBlank { null })
    val canSetModel = detailCaps?.get("can_set_model") ?: profile.caps.canSetModel
    val canSetEffort = detailCaps?.get("can_set_effort") ?: profile.caps.canSetEffort
    Column {
        TextButton(onClick = { expanded = !expanded }, modifier = Modifier.padding(start = 8.dp)) {
            Text(if (expanded) "Hide options" else "Model, effort, execution mode")
        }
        AnimatedVisibility(expanded) {
            Column(Modifier.padding(horizontal = 16.dp)) {
                OptionRow(
                    label = "Execution",
                    options = ExecMode.options(interactive),
                    selected = vm.execModeFor(profile, harness),
                    display = { ExecMode.short(it) },
                    onSelect = { vm.setExecMode(profile.id, it) },
                )
                Text(
                    "Both modes auto-run tools (no permission prompts).",
                    style = MaterialTheme.typography.bodySmall,
                    color = pal.dim,
                )
                if (canSetModel && models.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    OptionRow(
                        label = "Model",
                        options = models,
                        selected = profile.model.ifBlank { models.first() },
                        display = { it },
                        onSelect = { vm.setModel(profile.id, it) },
                    )
                }
                if (canSetEffort && efforts.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    OptionRow(
                        label = "Effort",
                        options = efforts,
                        selected = profile.effort.ifBlank { efforts.first() },
                        display = { it },
                        onSelect = { vm.setEffort(profile.id, it) },
                    )
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "These stick to this profile and are reused for every turn you send " +
                        "to it.",
                    style = MaterialTheme.typography.bodySmall,
                    color = pal.dim,
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun OptionRow(
    label: String,
    options: List<String>,
    selected: String,
    display: (String) -> String,
    onSelect: (String) -> Unit,
) {
    val pal = palette
    Column {
        Text(label, style = MaterialTheme.typography.labelSmall, color = pal.dim)
        Spacer(Modifier.height(4.dp))
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            options.forEach { option ->
                val active = option == selected
                Text(
                    text = display(option),
                    style = MaterialTheme.typography.labelMedium,
                    color = if (active) pal.onAccent else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(
                            if (active) pal.accent
                            else MaterialTheme.colorScheme.surfaceContainerHigh,
                        )
                        .clickable { onSelect(option) }
                        .padding(horizontal = 10.dp, vertical = 6.dp),
                )
            }
        }
    }
}
