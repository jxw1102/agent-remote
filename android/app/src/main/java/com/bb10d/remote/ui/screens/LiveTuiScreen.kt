package com.bb10d.remote.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.SessionRef
import com.bb10d.remote.ui.AnsiText
import com.bb10d.remote.ui.theme.palette
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Live TUI — shows the host tmux pane for an Interactive session and
 * injects keys / line text via agentremoted.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LiveTuiScreen(
    repo: AgentRepository,
    ref: SessionRef,
    onBack: () -> Unit,
) {
    val pal = palette
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("Connecting to host TUI…") }
    var status by remember { mutableStateOf("Host TUI") }
    var live by remember { mutableStateOf(false) }
    var line by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var seq by remember { mutableStateOf(0L) }
    val scroll = rememberScrollState()

    fun sendKeys(keys: List<String> = emptyList(), body: String = "") {
        val client = repo.client(ref.profileId) ?: return
        scope.launch {
            runCatching { client.tuiKeys(ref.sessionId, keys, body) }
                .onFailure { error = repo.reason(it) }
                .onSuccess { error = null }
        }
    }

    LaunchedEffect(ref.key) {
        val client = repo.client(ref.profileId) ?: return@LaunchedEffect
        while (isActive) {
            val frame = runCatching { client.tui(ref.sessionId) }.getOrElse {
                error = repo.reason(it)
                live = false
                status = "Error"
                delay(1200)
                continue
            }
            error = null
            if (!frame.attached) {
                live = false
                status = frame.error.ifBlank { "No host TUI attached" }
                if (seq == 0L) {
                    text = frame.error.ifBlank {
                        "No interactive TUI for this session. Start a turn in Interactive mode."
                    }
                }
            } else if (frame.seq != seq) {
                seq = frame.seq
                live = true
                status = if (frame.jobId.isNotBlank()) {
                    "Live · job ${frame.jobId.take(8)}"
                } else {
                    "Live"
                }
                text = frame.text.ifBlank { "(empty pane)" }
            } else {
                live = true
            }
            delay(400)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Live TUI", fontWeight = FontWeight.SemiBold)
                        Text(
                            text = buildString {
                                append(if (live) "● " else "")
                                append(status)
                            },
                            style = MaterialTheme.typography.labelSmall,
                            color = if (live) pal.ok else pal.dim,
                        )
                    }
                },
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
        containerColor = Color(0xFF0A0C10),
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding()
                .navigationBarsPadding(),
        ) {
            // Soft keys as real chrome buttons (surface + border) so they stay
            // readable on the near-black TUI pane — flat TextButtons vanish in dark.
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surface)
                    .padding(horizontal = 8.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                listOf(
                    "Esc" to listOf("Escape"),
                    "Tab" to listOf("Tab"),
                    "\u2191" to listOf("Up"),
                    "\u2193" to listOf("Down"),
                    "\u2190" to listOf("Left"),
                    "\u2192" to listOf("Right"),
                    "^C" to listOf("Ctrl+C"),
                    "\u21B5" to listOf("Enter"),
                ).forEach { (label, keys) ->
                    LiveTuiKeyButton(
                        label = label,
                        onClick = { sendKeys(keys) },
                        modifier = Modifier.weight(1f),
                    )
                }
            }
            // Coloured SGR from daemon tmux capture-pane -e.
            AnsiText(
                text = text,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(scroll)
                    .horizontalScroll(rememberScrollState())
                    .background(Color(0xFF0A0C10))
                    .padding(12.dp),
                defaultColor = Color(0xFFD0D4DC),
                fontFamily = FontFamily.Monospace,
                fontSize = 12.sp,
                lineHeight = 16.sp,
            )
            if (error != null) {
                Text(
                    error!!,
                    color = pal.danger,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
                )
            }
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surface)
                    .padding(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = line,
                    onValueChange = { line = it },
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    placeholder = { Text("Line into TUI") },
                    textStyle = MaterialTheme.typography.bodyMedium.copy(
                        fontFamily = FontFamily.Monospace,
                    ),
                )
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = {
                        val t = line.trim()
                        if (t.isEmpty()) return@Button
                        sendKeys(listOf("Enter"), t)
                        line = ""
                    },
                ) { Text("Send") }
            }
        }
    }
}

/** Compact mono keycap for the Live TUI toolbar. */
@Composable
private fun LiveTuiKeyButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val pal = palette
    val shape = RoundedCornerShape(8.dp)
    Box(
        modifier = modifier
            .defaultMinSize(minHeight = 40.dp)
            .clip(shape)
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .border(1.dp, pal.hairline, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 4.dp, vertical = 8.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            color = MaterialTheme.colorScheme.onSurface,
            fontSize = 13.sp,
            fontWeight = FontWeight.Medium,
            fontFamily = FontFamily.Monospace,
            maxLines = 1,
            textAlign = TextAlign.Center,
        )
    }
}
