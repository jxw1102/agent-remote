package com.bb10d.remote.ui.screens

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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.draw.clip
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.data.Profile
import com.bb10d.remote.ui.components.EmptyState
import com.bb10d.remote.ui.components.ErrorBanner
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.MetaPill
import com.bb10d.remote.ui.components.ProviderChip
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.palette

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProfilesScreen(
    vm: ProfilesViewModel,
    onBack: () -> Unit,
    onEdit: (String?) -> Unit,
) {
    val state by vm.profiles.collectAsStateWithLifecycle()
    val streams by vm.streamsUp.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Profiles", fontWeight = FontWeight.SemiBold) },
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
        floatingActionButton = {
            FloatingActionButton(
                onClick = { onEdit(null) },
                containerColor = palette.accent,
                contentColor = palette.onAccent,
            ) {
                Icon(Icons.Outlined.Add, contentDescription = "Add profile")
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        if (state.profiles.isEmpty()) {
            EmptyState(
                title = "No profiles",
                body = "Each profile is one agentremoted host. Add the multi daemon on your Mac and " +
                    "the Grok daemon on your server — sessions from both merge into one list.",
                modifier = Modifier.padding(padding),
                action = { Button(onClick = { onEdit(null) }) { Text("Add profile") } },
            )
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentPadding = PaddingValues(bottom = 96.dp),
            ) {
                items(state.profiles, key = { it.id }) { profile ->
                    ProfileRow(
                        profile = profile,
                        live = streams.contains(profile.id),
                        onClick = { onEdit(profile.id) },
                        onToggle = { vm.setEnabled(profile.id, it) },
                    )
                    Hairline(inset = 16)
                }
            }
        }
    }
}

@Composable
private fun ProfileRow(
    profile: Profile,
    live: Boolean,
    onClick: () -> Unit,
    onToggle: (Boolean) -> Unit,
) {
    val pal = palette
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = profile.displayName,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.height(4.dp))
            // Multi host: Claude · Grok · Codex (not only the default ping provider).
            val harnesses = profile.caps.harnesses()
            Text(
                text = buildString {
                    if (harnesses.size > 1) {
                        append(harnesses.joinToString(" · ") {
                            Accent.forProvider(it).label
                        })
                        append(" · ")
                    } else if (harnesses.size == 1) {
                        append(Accent.forProvider(harnesses[0]).label)
                        append(" · ")
                    } else if (profile.provider.isNotEmpty()) {
                        append(Accent.forProvider(profile.provider).label)
                        append(" · ")
                    }
                    append(profile.hostLabel)
                },
                style = MaterialTheme.typography.bodySmall,
                color = pal.dim,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                if (harnesses.size > 1) {
                    harnesses.forEach { h ->
                        ProviderChip(h, Accent.forProvider(h).label, compact = true)
                    }
                } else if (profile.provider.isNotEmpty()) {
                    ProviderChip(profile.provider, profile.provider, compact = true)
                }
                if (profile.caps.version.isNotEmpty()) {
                    MetaPill("agentremoted ${profile.caps.version}")
                }
                if (profile.caps.host.isNotEmpty()) MetaPill(profile.caps.host)
                if (live) MetaPill("live", tint = pal.ok)
                if (!profile.configured) MetaPill("needs a token", tint = pal.warn)
            }
        }
        Switch(checked = profile.enabled, onCheckedChange = onToggle)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProfileEditorScreen(
    vm: ProfilesViewModel,
    profileId: String?,
    onDone: () -> Unit,
) {
    val state by vm.profiles.collectAsStateWithLifecycle()
    val existing = remember(profileId, state) { profileId?.let { state.byId(it) } }

    var name by remember(existing) { mutableStateOf(existing?.name.orEmpty()) }
    var url by remember(existing) { mutableStateOf(existing?.baseUrl.orEmpty()) }
    var token by remember(existing) { mutableStateOf(existing?.token.orEmpty()) }
    var showToken by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }

    val test by vm.testResult.collectAsStateWithLifecycle()
    val pal = palette

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        if (existing == null) "New profile" else "Edit profile",
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = { vm.clearTest(); onDone() }) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    if (existing != null) {
                        IconButton(onClick = { confirmDelete = true }) {
                            Icon(
                                Icons.Outlined.Delete,
                                contentDescription = "Delete",
                                tint = pal.danger,
                            )
                        }
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
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp),
        ) {
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text("Name") },
                placeholder = { Text("Mac · Claude") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.Words,
                    imeAction = ImeAction.Next,
                ),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = url,
                onValueChange = { url = it },
                label = { Text("Server address") },
                placeholder = { Text("192.168.1.20:8473") },
                supportingText = {
                    Text("http:// is assumed; add https:// for a TLS daemon")
                },
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = token,
                onValueChange = { token = it },
                label = { Text("Token") },
                supportingText = { Text("The contents of ~/.agentremoted/token on that host") },
                singleLine = true,
                visualTransformation = if (showToken) VisualTransformation.None
                else PasswordVisualTransformation(),
                trailingIcon = {
                    IconButton(onClick = { showToken = !showToken }) {
                        Icon(
                            if (showToken) Icons.Outlined.VisibilityOff
                            else Icons.Outlined.Visibility,
                            contentDescription = if (showToken) "Hide token" else "Show token",
                        )
                    }
                },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                modifier = Modifier.fillMaxWidth(),
            )

            Spacer(Modifier.height(16.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(
                    onClick = { vm.test(url, token) },
                    enabled = url.isNotBlank() && test !is ProfilesViewModel.TestState.Running,
                ) {
                    if (test is ProfilesViewModel.TestState.Running) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp),
                            strokeWidth = 2.dp,
                        )
                        Spacer(Modifier.width(8.dp))
                    }
                    Text("Test connection")
                }
                Spacer(Modifier.width(12.dp))
                Button(
                    onClick = {
                        vm.save(existing, name, url, token)
                        vm.clearTest()
                        onDone()
                    },
                    enabled = url.isNotBlank() && token.isNotBlank(),
                ) { Text("Save") }
            }

            Spacer(Modifier.height(16.dp))

            when (val result = test) {
                is ProfilesViewModel.TestState.Ok -> Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(
                        Icons.Outlined.CheckCircle,
                        contentDescription = null,
                        tint = pal.ok,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Column {
                        Text(
                            text = "${Accent.forProvider(result.caps.provider).label} on " +
                                result.caps.host.ifBlank { "the daemon" },
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                        Text(
                            text = "agentremoted ${result.caps.version} · " + capsSummary(result),
                            style = MaterialTheme.typography.bodySmall,
                            color = pal.dim,
                        )
                    }
                }

                is ProfilesViewModel.TestState.Failed -> ErrorBanner(result.message)
                else -> Unit
            }

            Spacer(Modifier.height(24.dp))
            Text(
                text = "A profile is just a daemon address. The provider badge comes from the " +
                    "daemon's own /api/ping, so re-pointing a profile at another host re-badges " +
                    "it automatically.",
                style = MaterialTheme.typography.bodySmall,
                color = pal.dim,
            )
            Spacer(Modifier.height(32.dp))
        }
    }

    if (confirmDelete && existing != null) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("Delete ${existing.displayName}?") },
            text = {
                Text(
                    "This only removes the connection from this phone. Sessions on the daemon " +
                        "are untouched.",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmDelete = false
                    vm.delete(existing.id)
                    onDone()
                }) { Text("Delete", color = pal.danger) }
            },
            dismissButton = {
                TextButton(onClick = { confirmDelete = false }) { Text("Cancel") }
            },
        )
    }
}

private fun capsSummary(ok: ProfilesViewModel.TestState.Ok): String {
    val bits = buildList {
        if (ok.caps.interactive) add("interactive")
        if (ok.caps.permissions) add("permissions")
        if (ok.caps.canSetModel) add("models")
        if (ok.caps.canSetEffort) add("effort")
        if (ok.caps.rewind) add("rewind")
        if (ok.caps.canShowUsage) add("usage")
    }
    return if (bits.isEmpty()) "basic" else bits.joinToString(", ")
}

@Composable
fun ProfilePickerRow(profile: Profile, selected: Boolean, onClick: () -> Unit) {
    val accent = Accent.forProvider(profile.provider)
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 12.dp),
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
                profile.hostLabel,
                style = MaterialTheme.typography.bodySmall,
                color = palette.dim,
            )
        }
        if (selected) {
            Icon(Icons.Outlined.CheckCircle, contentDescription = null, tint = accent.tint)
        }
    }
}
