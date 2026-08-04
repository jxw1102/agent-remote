package com.bb10d.remote.ui.screens

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.OpenInBrowser
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.ui.components.ErrorBanner
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.ProviderChip
import com.bb10d.remote.ui.theme.Accent
import com.bb10d.remote.ui.theme.palette

/**
 * Subscription headroom, per daemon.
 *
 * Only some daemons can answer: Claude reads Anthropic's OAuth usage endpoint,
 * and Grok can only scrape its TUI's `/usage` (slow, and impossible without
 * tmux on the host). Profiles that say they cannot are listed with the reason
 * rather than being hidden, so an empty screen never looks like a bug.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun UsageScreen(vm: UsageViewModel, onBack: () -> Unit, onOpenWeb: (String) -> Unit) {
    val profiles by vm.profiles.collectAsStateWithLifecycle()
    val results by vm.results.collectAsStateWithLifecycle()
    val pal = palette

    LaunchedEffect(Unit) { vm.refresh() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Usage", fontWeight = FontWeight.SemiBold) },
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
                .verticalScroll(rememberScrollState()),
        ) {
            profiles.enabled.forEach { profile ->
                val result = results[profile.id]
                Column(Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        ProviderChip(profile.provider, profile.displayName)
                        Spacer(Modifier.width(8.dp))
                        if (result is UsageViewModel.Result.Loading) {
                            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                        }
                    }
                    Spacer(Modifier.height(12.dp))
                    when {
                        !profile.caps.canShowUsage -> Column {
                            Text(
                                "This daemon does not report usage.",
                                style = MaterialTheme.typography.bodySmall,
                                color = pal.dim,
                            )
                            if (profile.provider == "grok") {
                                TextButton(onClick = { onOpenWeb(GROK_USAGE_URL) }) {
                                    Icon(Icons.Outlined.OpenInBrowser, null)
                                    Spacer(Modifier.width(6.dp))
                                    Text("Open grok.com usage")
                                }
                            }
                        }

                        result is UsageViewModel.Result.Failed -> ErrorBanner(result.message)

                        result is UsageViewModel.Result.Ok &&
                            result.sections.isEmpty() && result.buckets.isEmpty() -> Text(
                            "No usage data returned.",
                            style = MaterialTheme.typography.bodySmall,
                            color = pal.dim,
                        )

                        result is UsageViewModel.Result.Ok && result.sections.isNotEmpty() -> Column {
                            // Multi-harness host: Claude / Grok / … each get a block.
                            result.sections.forEach { section ->
                                val harness = section.provider.ifBlank { profile.provider }
                                ProviderChip(harness, harness.replaceFirstChar {
                                    if (it.isLowerCase()) it.titlecase() else it.toString()
                                })
                                Spacer(Modifier.height(8.dp))
                                when {
                                    section.error.isNotBlank() && section.buckets.isEmpty() -> {
                                        Text(
                                            section.error,
                                            style = MaterialTheme.typography.bodySmall,
                                            color = pal.dim,
                                        )
                                    }
                                    section.buckets.isEmpty() -> Text(
                                        "No usage data returned.",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = pal.dim,
                                    )
                                    else -> section.buckets.forEach { bucket ->
                                        val title = stripHarnessPrefix(
                                            bucket.title, harness,
                                        )
                                        UsageBar(
                                            title = title,
                                            percent = bucket.percent,
                                            resets = bucket.resetsText,
                                            severity = bucket.severity,
                                            accent = Accent.forProvider(harness).tint,
                                        )
                                        Spacer(Modifier.height(14.dp))
                                    }
                                }
                                Spacer(Modifier.height(10.dp))
                            }
                        }

                        result is UsageViewModel.Result.Ok -> Column {
                            result.buckets.forEach { bucket ->
                                val harness = bucket.provider.ifBlank { profile.provider }
                                UsageBar(
                                    title = bucket.title,
                                    percent = bucket.percent,
                                    resets = bucket.resetsText,
                                    severity = bucket.severity,
                                    accent = Accent.forProvider(harness).tint,
                                )
                                Spacer(Modifier.height(14.dp))
                            }
                        }

                        else -> Text(
                            "Reading…",
                            style = MaterialTheme.typography.bodySmall,
                            color = pal.dim,
                        )
                    }
                }
                Hairline(inset = 16)
            }
            if (profiles.enabled.isEmpty()) {
                Text(
                    "No enabled profiles.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = pal.dim,
                    modifier = Modifier.padding(16.dp),
                )
            }
            Spacer(Modifier.height(32.dp))
        }
    }
}

private const val GROK_USAGE_URL = "https://grok.com/?_s=usage"

/** Multi /api/usage prefixes "Claude · …" for flat-list clients; strip under a section header. */
private fun stripHarnessPrefix(title: String, harness: String): String {
    if (harness.isBlank() || title.isBlank()) return title
    val label = harness.replaceFirstChar {
        if (it.isLowerCase()) it.titlecase() else it.toString()
    }
    val prefix = "$label · "
    return if (title.startsWith(prefix, ignoreCase = true)) title.drop(prefix.length) else title
}

@Composable
private fun UsageBar(
    title: String,
    percent: Int,
    resets: String,
    severity: String,
    accent: androidx.compose.ui.graphics.Color,
) {
    val pal = palette
    val color = when (severity) {
        "critical" -> pal.danger
        "warning" -> pal.warn
        else -> accent
    }
    val fraction by animateFloatAsState(
        targetValue = (percent.coerceIn(0, 100)) / 100f,
        label = "usage",
    )
    Column {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                title,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.weight(1f),
            )
            Text("$percent%", style = MaterialTheme.typography.labelLarge, color = color)
        }
        Spacer(Modifier.height(6.dp))
        Box(
            Modifier
                .fillMaxWidth()
                .height(8.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(pal.hairline),
        ) {
            Box(
                Modifier
                    .fillMaxWidth(fraction)
                    .height(8.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(color),
            )
        }
        if (resets.isNotBlank()) {
            Spacer(Modifier.height(4.dp))
            Text(resets, style = MaterialTheme.typography.labelSmall, color = pal.dim)
        }
    }
}
