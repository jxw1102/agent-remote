package com.bb10d.remote.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.automirrored.outlined.InsertDriveFile
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.data.Time
import com.bb10d.remote.ui.components.ErrorBanner
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.MetaPill
import com.bb10d.remote.ui.components.ProviderChip
import com.bb10d.remote.ui.theme.palette

/**
 * The host→phone direction.
 *
 * The agent copies a file into its daemon's drop folder ("put it in
 * ~/Public"), and it appears here for every profile at once — which is the
 * point of the merged app: you do not have to remember which machine produced
 * the artefact.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DropScreen(vm: DropViewModel, onBack: () -> Unit) {
    val profiles by vm.profiles.collectAsStateWithLifecycle()
    val state by vm.state.collectAsStateWithLifecycle()
    val rows by vm.rows.collectAsStateWithLifecycle()
    val message by vm.message.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val pal = palette

    LaunchedEffect(Unit) { vm.refresh() }
    LaunchedEffect(message) {
        message?.let {
            snackbar.showSnackbar(it)
            vm.clearMessage()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Files from host", fontWeight = FontWeight.SemiBold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = vm::refresh) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "Refresh")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState()),
        ) {
            // Per-daemon header: where each drop folder lives, plus any
            // daemon that could not answer. The files themselves are ONE
            // merged list below — matching the sessions screen.
            profiles.enabled.forEach { profile ->
                val feed = state[profile.id]
                Row(
                    Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    ProviderChip(profile.provider, profile.displayName)
                    Spacer(Modifier.width(8.dp))
                    if (feed?.loading == true) {
                        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                    } else if (!feed?.path.isNullOrBlank()) {
                        Text(
                            feed.path,
                            style = MaterialTheme.typography.labelSmall,
                            color = pal.dim,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                feed?.error?.let {
                    ErrorBanner(it, modifier = Modifier.padding(horizontal = 16.dp))
                    Spacer(Modifier.height(6.dp))
                }
            }
            if (profiles.enabled.isEmpty()) {
                Text(
                    "No enabled profiles.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = pal.dim,
                    modifier = Modifier.padding(16.dp),
                )
            }

            Spacer(Modifier.height(6.dp))
            Hairline()

            rows.forEach { row ->
                DropFileRow(
                    row = row,
                    downloading = vm.isDownloading(row.profileId, row.file.name),
                    onDownload = { vm.download(context, row.profileId, row.file.name) },
                    onDelete = { vm.delete(row.profileId, row.file.name) },
                )
                Hairline(inset = 16)
            }
            if (rows.isEmpty() && state.values.none { it.loading }) {
                Text(
                    "Nothing staged. Ask the agent to copy a file into a drop folder.",
                    style = MaterialTheme.typography.bodySmall,
                    color = pal.dim,
                    modifier = Modifier.padding(16.dp),
                )
            }
            Spacer(Modifier.height(32.dp))
        }
    }
}

@Composable
private fun DropFileRow(
    row: DropRow,
    downloading: Boolean,
    onDownload: () -> Unit,
    onDelete: () -> Unit,
) {
    val pal = palette
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onDownload)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.AutoMirrored.Outlined.InsertDriveFile,
            contentDescription = null,
            tint = pal.dim,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                row.file.name,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                ProviderChip(row.provider, row.profileName, compact = false)
                Spacer(Modifier.width(6.dp))
                MetaPill(Time.humanSize(row.file.size))
                Spacer(Modifier.width(6.dp))
                MetaPill(Time.relativeStamp(row.file.mtime * 1000))
            }
            if (row.alsoOn.isNotEmpty()) {
                Text(
                    text = "Identical copy on ${row.alsoOn.joinToString(", ")}",
                    style = MaterialTheme.typography.labelSmall,
                    color = pal.dim,
                )
            }
        }
        if (downloading) {
            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
        } else {
            IconButton(onClick = onDownload) {
                Icon(Icons.Outlined.Download, contentDescription = "Download")
            }
        }
        IconButton(onClick = onDelete) {
            Icon(
                Icons.Outlined.Delete,
                contentDescription = "Delete on host",
                tint = pal.dim,
            )
        }
    }
}
