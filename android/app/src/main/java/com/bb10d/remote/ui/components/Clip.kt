package com.bb10d.remote.ui.components

import android.content.ClipData
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.platform.Clipboard
import androidx.compose.ui.platform.ClipEntry
import androidx.compose.ui.platform.LocalClipboard
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * Copy-to-clipboard for callers that are not suspend functions.
 *
 * Compose's clipboard became suspend-based (the platform call can block), so
 * every click handler would otherwise need its own scope. This wraps that once.
 */
class Clip(private val clipboard: Clipboard, private val scope: CoroutineScope) {
    fun copy(text: String, label: String = "Agent Remote") {
        if (text.isEmpty()) return
        scope.launch {
            clipboard.setClipEntry(ClipEntry(ClipData.newPlainText(label, text)))
        }
    }
}

@Composable
fun rememberClip(): Clip {
    val clipboard = LocalClipboard.current
    val scope = rememberCoroutineScope()
    return remember(clipboard, scope) { Clip(clipboard, scope) }
}
