package com.bb10d.remote.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bb10d.remote.BuildConfig
import com.bb10d.remote.ui.components.Hairline
import com.bb10d.remote.ui.components.SectionLabel
import com.bb10d.remote.ui.theme.palette

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(vm: SettingsViewModel, onBack: () -> Unit, onProfiles: () -> Unit) {
    val settings by vm.settings.collectAsStateWithLifecycle()
    val profiles by vm.profiles.collectAsStateWithLifecycle()
    val pal = palette

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings", fontWeight = FontWeight.SemiBold) },
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
            SectionLabel("Connections")
            NavRow(
                title = "Profiles",
                subtitle = profileSummary(profiles.profiles.size, profiles.enabled.size),
                onClick = onProfiles,
            )
            Hairline(inset = 16)

            SectionLabel("Appearance")
            ChoiceRow(
                title = "Theme",
                options = listOf("system" to "Follow system", "dark" to "Dark", "light" to "Light"),
                selected = settings.theme,
                onSelect = vm::setTheme,
            )
            SwitchRow(
                title = "Rich text",
                subtitle = "Render agent replies as markdown. Off shows the raw text.",
                checked = settings.richText,
                onChange = vm::setRichText,
            )
            Hairline(inset = 16)

            SectionLabel("Cues")
            SwitchRow(
                title = "Sound cues",
                subtitle = "A blip on each new phase or tool, do-re-mi when the turn finishes, " +
                    "a low double-tap when it fails. Plays at media volume.",
                checked = settings.soundCues,
                onChange = vm::setSoundCues,
            )
            SwitchRow(
                title = "Haptic cues",
                subtitle = "The same signals as a short vibration — what the status LED did on " +
                    "BlackBerry. Also controls whether alerts buzz.",
                checked = settings.hapticCues,
                onChange = vm::setHapticCues,
            )
            Hairline(inset = 16)

            SectionLabel("While turns run")
            SwitchRow(
                title = "Keep watching in the background",
                subtitle = "Holds the status streams open so alerts arrive with the app closed.",
                checked = settings.backgroundWatch,
                onChange = vm::setBackgroundWatch,
            )
            SwitchRow(
                title = "Notify when a turn finishes",
                checked = settings.notifyTurnDone,
                onChange = vm::setNotifyTurnDone,
            )
            Hairline(inset = 16)

            SectionLabel("About")
            Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
                Text(
                    "Agent Remote ${BuildConfig.VERSION_NAME}",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    "Talks to agentremoted hosts over their HTTP API. One profile per host; the " +
                        "provider badge and every feature toggle come from that daemon's " +
                        "/api/ping, so Claude and Grok hosts can share this list.",
                    style = MaterialTheme.typography.bodySmall,
                    color = pal.dim,
                )
            }
            Spacer(Modifier.height(40.dp))
        }
    }
}

private fun profileSummary(total: Int, enabled: Int) = when {
    total == 0 -> "None yet"
    total == enabled -> "$total connected"
    else -> "$enabled of $total in the list"
}

@Composable
private fun NavRow(title: String, subtitle: String, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = palette.dim)
        }
        Icon(
            Icons.AutoMirrored.Outlined.KeyboardArrowRight,
            contentDescription = null,
            tint = palette.dim,
        )
    }
}

@Composable
private fun SwitchRow(
    title: String,
    checked: Boolean,
    onChange: (Boolean) -> Unit,
    subtitle: String = "",
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable { onChange(!checked) }
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            if (subtitle.isNotEmpty()) {
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = palette.dim)
            }
        }
        Spacer(Modifier.width(12.dp))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
private fun ChoiceRow(
    title: String,
    options: List<Pair<String, String>>,
    selected: String,
    onSelect: (String) -> Unit,
) {
    val labels = options.associate { it.first to it.second }
    Column(Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) {
        OptionRow(
            label = title,
            options = options.map { it.first },
            selected = selected,
            display = { labels[it] ?: it },
            onSelect = onSelect,
        )
    }
}
