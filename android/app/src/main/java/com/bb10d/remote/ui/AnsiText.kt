package com.bb10d.remote.ui

import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.TextUnit

/**
 * Render a tmux pane capture that may contain ANSI SGR sequences
 * (`capture-pane -e`). Used by Live TUI on Android; BB strips SGR instead.
 */
@Composable
fun AnsiText(
    text: String,
    modifier: Modifier = Modifier,
    defaultColor: Color = Color(0xFFD0D4DC),
    fontSize: TextUnit = TextUnit.Unspecified,
    lineHeight: TextUnit = TextUnit.Unspecified,
    fontFamily: FontFamily = FontFamily.Monospace,
    selectable: Boolean = true,
) {
    val annotated = remember(text, defaultColor) { ansiToAnnotated(text, defaultColor) }
    if (selectable) {
        SelectionContainer {
            Text(
                text = annotated,
                modifier = modifier,
                fontSize = fontSize,
                lineHeight = lineHeight,
                fontFamily = fontFamily,
            )
        }
    } else {
        Text(
            text = annotated,
            modifier = modifier,
            fontSize = fontSize,
            lineHeight = lineHeight,
            fontFamily = fontFamily,
        )
    }
}

private val ANSI_FG = listOf(
    Color(0xFF0C0C0C), Color(0xFFCD3131), Color(0xFF0DBC79), Color(0xFFE5E510),
    Color(0xFF2472C8), Color(0xFFBC3FBC), Color(0xFF11A8CD), Color(0xFFE5E5E5),
)
private val ANSI_BRIGHT = listOf(
    Color(0xFF666666), Color(0xFFF14C4C), Color(0xFF23D18B), Color(0xFFF5F543),
    Color(0xFF3B8EEA), Color(0xFFD670D6), Color(0xFF29B8DB), Color(0xFFE5E5E5),
)

private fun ansi256(n: Int): Color {
    if (n < 0) return Color.Unspecified
    if (n < 8) return ANSI_FG[n]
    if (n < 16) return ANSI_BRIGHT[n - 8]
    if (n < 232) {
        val v = n - 16
        fun c(x: Int) = if (x == 0) 0 else 55 + x * 40
        val r = c(v / 36)
        val g = c((v % 36) / 6)
        val b = c(v % 6)
        return Color(r, g, b)
    }
    val gray = 8 + (n - 232) * 10
    return Color(gray, gray, gray)
}

fun ansiToAnnotated(raw: String, defaultColor: Color): AnnotatedString {
    if (raw.isEmpty()) return AnnotatedString("")
    // Strip OSC sequences (title etc.)
    val s = raw.replace(Regex("\u001B\\][^\u0007\u001B]*(?:\u0007|\u001B\\\\)"), "")
    if (!s.contains('\u001B')) {
        return AnnotatedString(s)
    }
    return buildAnnotatedString {
        var bold = false
        var dim = false
        var italic = false
        var underline = false
        var fg: Color? = null
        var bg: Color? = null
        fun style(): SpanStyle = SpanStyle(
            color = when {
                fg != null -> fg!!
                dim -> defaultColor.copy(alpha = 0.7f)
                else -> defaultColor
            },
            background = bg ?: Color.Unspecified,
            fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal,
            fontStyle = if (italic) FontStyle.Italic else FontStyle.Normal,
            textDecoration = if (underline) TextDecoration.Underline else TextDecoration.None,
        )
        val re = Regex("\u001B\\[([0-9;]*)m")
        var last = 0
        for (m in re.findAll(s)) {
            val chunk = s.substring(last, m.range.first)
            if (chunk.isNotEmpty()) {
                pushStyle(style())
                append(chunk)
                pop()
            }
            last = m.range.last + 1
            val parts = (m.groupValues.getOrNull(1) ?: "0")
                .split(';')
                .map { it.toIntOrNull() ?: 0 }
            var p = 0
            while (p < parts.size) {
                when (val code = parts[p]) {
                    0 -> {
                        bold = false; dim = false; italic = false; underline = false
                        fg = null; bg = null
                    }
                    1 -> bold = true
                    2 -> dim = true
                    3 -> italic = true
                    4 -> underline = true
                    22 -> { bold = false; dim = false }
                    23 -> italic = false
                    24 -> underline = false
                    39 -> fg = null
                    49 -> bg = null
                    in 30..37 -> fg = ANSI_FG[code - 30]
                    in 90..97 -> fg = ANSI_BRIGHT[code - 90]
                    in 40..47 -> bg = ANSI_FG[code - 40]
                    in 100..107 -> bg = ANSI_BRIGHT[code - 100]
                    38, 48 -> {
                        val isFg = code == 38
                        val mode = parts.getOrNull(p + 1)
                        if (mode == 5 && parts.getOrNull(p + 2) != null) {
                            val c = ansi256(parts[p + 2])
                            if (isFg) fg = c else bg = c
                            p += 2
                        } else if (mode == 2 && parts.getOrNull(p + 4) != null) {
                            val c = Color(
                                parts[p + 2].coerceIn(0, 255),
                                parts[p + 3].coerceIn(0, 255),
                                parts[p + 4].coerceIn(0, 255),
                            )
                            if (isFg) fg = c else bg = c
                            p += 4
                        }
                    }
                }
                p++
            }
        }
        if (last < s.length) {
            // Drop residual CSI (cursor motion)
            val tail = s.substring(last).replace(Regex("\u001B\\[[0-9;?]*[A-Za-z]"), "")
            if (tail.isNotEmpty()) {
                pushStyle(style())
                append(tail)
                pop()
            }
        }
    }
}

/** Plain text for clients that cannot render SGR (e.g. BB10 Label). */
fun stripAnsi(raw: String): String {
    if (raw.isEmpty() || !raw.contains('\u001B')) return raw
    return raw
        .replace(Regex("\u001B\\][^\u0007\u001B]*(?:\u0007|\u001B\\\\)"), "")
        .replace(Regex("\u001B\\[[0-9;?]*[A-Za-z]"), "")
}
