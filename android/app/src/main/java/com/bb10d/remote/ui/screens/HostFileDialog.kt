package com.bb10d.remote.ui.screens

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import com.bb10d.remote.ui.markdown.LocalHostLinkHandler
import com.bb10d.remote.ui.markdown.MarkdownText
import com.bb10d.remote.ui.theme.MonoStyle
import com.bb10d.remote.ui.theme.palette

/** Lines past this are cut on a phone; the full file stays a Copy away. */
private const val MAX_LINES = 5000

private val MARKDOWN = Regex("""\.(md|markdown|mdown|mkd|mdx)$""", RegexOption.IGNORE_CASE)

/**
 * A host file the agent linked, as the daemon served it: text in a
 * monospace list with the linked line marked, or an image.
 */
@Composable
fun HostFileDialog(
    file: HostFileUi,
    onCopy: (String) -> Unit,
    onDismiss: () -> Unit,
    onHostLink: ((String) -> Unit)? = null,
) {
    val pal = palette
    // Markdown reads formatted, like the transcript it came from; a link to a
    // specific line opens the source so that line can be marked.
    val isMarkdown = MARKDOWN.containsMatchIn(file.path)
    var formatted by rememberSaveable(file.path) { mutableStateOf(isMarkdown && file.line <= 0) }
    AlertDialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
        modifier = Modifier.padding(horizontal = 12.dp),
        title = {
            Column {
                Text(file.name, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    file.path,
                    style = MonoStyle,
                    color = pal.dim,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        },
        text = {
            when {
                file.busy -> Box(Modifier.fillMaxWidth().padding(24.dp)) {
                    CircularProgressIndicator()
                }

                file.error != null -> Text(file.error)

                file.image != null -> {
                    val bitmap = remember(file.image) {
                        BitmapFactory.decodeByteArray(file.image, 0, file.image.size)
                    }
                    if (bitmap == null) {
                        Text("${file.name} is not an image the phone can decode.")
                    } else {
                        Image(
                            bitmap = bitmap.asImageBitmap(),
                            contentDescription = file.name,
                            contentScale = ContentScale.Fit,
                            modifier = Modifier.fillMaxWidth().heightIn(max = 520.dp),
                        )
                    }
                }

                file.text != null && isMarkdown && formatted ->
                    CompositionLocalProvider(LocalHostLinkHandler provides onHostLink) {
                        MarkdownText(
                            file.text,
                            modifier = Modifier.heightIn(max = 520.dp)
                                .verticalScroll(rememberScrollState()),
                        )
                    }

                file.text != null -> TextLines(file.text, file.line)
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        },
        dismissButton = {
            if (file.text != null) {
                Row {
                    if (isMarkdown) {
                        TextButton(onClick = { formatted = !formatted }) {
                            Text(if (formatted) "Source" else "Formatted")
                        }
                    }
                    TextButton(onClick = { onCopy(file.text) }) { Text("Copy") }
                }
            }
        },
    )
}

@Composable
private fun TextLines(text: String, line: Int) {
    val pal = palette
    val lines = remember(text) { text.split('\n') }
    val shown = remember(lines) { lines.take(MAX_LINES) }
    val state = rememberLazyListState()
    LaunchedEffect(line) {
        if (line in 1..shown.size) state.scrollToItem((line - 4).coerceAtLeast(0))
    }
    val scroll = rememberScrollState()
    SelectionContainer {
        LazyColumn(
            state = state,
            modifier = Modifier.heightIn(max = 520.dp).horizontalScroll(scroll),
        ) {
            itemsIndexed(shown) { i, row ->
                val marked = i + 1 == line
                Row(
                    Modifier.let {
                        if (marked) it.background(pal.accent.copy(alpha = 0.22f)) else it
                    },
                ) {
                    Text(
                        "%4d  ".format(i + 1),
                        style = MonoStyle,
                        color = pal.dim,
                    )
                    Text(row.ifEmpty { " " }, style = MonoStyle,
                        color = MaterialTheme.colorScheme.onSurface)
                }
            }
            if (lines.size > shown.size) {
                item {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "… ${lines.size - shown.size} more lines. Copy takes the whole file.",
                        color = pal.dim,
                    )
                }
            }
        }
    }
}
