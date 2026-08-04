package com.bb10d.remote.ui.markdown

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.bb10d.remote.ui.theme.MonoStyle
import com.bb10d.remote.ui.theme.palette

/**
 * Renders parsed markdown as Compose.
 *
 * Two rules drive the layout choices here, both learned from reading agent
 * output on a phone: code must never reflow (a wrapped shell command is
 * unreadable and un-copyable, so it scrolls sideways instead), and tables must
 * stay tables (collapsing them to key/value loses the comparison that made the
 * agent draw one).
 */
@Composable
fun MarkdownText(
    source: String,
    modifier: Modifier = Modifier,
    baseStyle: TextStyle = MaterialTheme.typography.bodyMedium,
    color: Color = LocalContentColor.current,
) {
    val blocks = remember(source) { Markdown.parse(source) }
    MarkdownBlocks(blocks, modifier, baseStyle, color)
}

@Composable
fun MarkdownBlocks(
    blocks: List<MdBlock>,
    modifier: Modifier = Modifier,
    baseStyle: TextStyle = MaterialTheme.typography.bodyMedium,
    color: Color = LocalContentColor.current,
) {
    Column(modifier = modifier.fillMaxWidth()) {
        blocks.forEachIndexed { index, block ->
            if (index > 0) Spacer(Modifier.height(gapBefore(block)))
            RenderBlock(block, baseStyle, color)
        }
    }
}

private fun gapBefore(block: MdBlock) = when (block) {
    is MdBlock.Bullet -> 3.dp
    is MdBlock.Heading -> 12.dp
    is MdBlock.Code -> 8.dp
    is MdBlock.Table -> 8.dp
    MdBlock.Rule -> 10.dp
    else -> 7.dp
}

@Composable
private fun RenderBlock(block: MdBlock, baseStyle: TextStyle, color: Color) {
    val pal = palette
    when (block) {
        is MdBlock.Heading -> Text(
            text = annotate(block.spans, pal.inlineCode, pal.accent),
            style = baseStyle.copy(
                fontSize = when (block.level) {
                    1 -> 19.sp
                    2 -> 17.sp
                    3 -> 15.5.sp
                    else -> 14.5.sp
                },
                fontWeight = FontWeight.SemiBold,
                lineHeight = 24.sp,
            ),
            color = pal.heading,
        )

        is MdBlock.Paragraph -> Text(
            text = annotate(block.spans, pal.inlineCode, pal.accent),
            style = baseStyle,
            color = color,
        )

        is MdBlock.Bullet -> Row(
            modifier = Modifier.padding(start = (block.indent * 14).dp),
            verticalAlignment = Alignment.Top,
        ) {
            Text(
                text = block.marker,
                style = baseStyle,
                color = pal.accent,
                modifier = Modifier.width(if (block.marker.length > 2) 26.dp else 16.dp),
            )
            Text(
                text = annotate(block.spans, pal.inlineCode, pal.accent),
                style = baseStyle,
                color = color,
            )
        }

        is MdBlock.Code -> CodeBlock(block)

        is MdBlock.Quote -> Row(modifier = Modifier.height(IntrinsicSize.Min)) {
            Box(
                Modifier
                    .width(3.dp)
                    .fillMaxHeight()
                    .clip(RoundedCornerShape(2.dp))
                    .background(pal.accent.copy(alpha = 0.55f)),
            )
            Column(Modifier.padding(start = 10.dp)) {
                MarkdownBlocks(block.blocks, baseStyle = baseStyle, color = pal.thought)
            }
        }

        is MdBlock.Table -> TableBlock(block, baseStyle)

        MdBlock.Rule -> Box(
            Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(pal.hairline),
        )
    }
}

@Composable
private fun CodeBlock(block: MdBlock.Code) {
    val pal = palette
    val scroll = rememberScrollState()
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(pal.codeBg)
            .border(1.dp, pal.codeBorder, RoundedCornerShape(8.dp)),
    ) {
        if (block.lang.isNotEmpty()) {
            Text(
                text = block.lang,
                style = MaterialTheme.typography.labelSmall,
                color = pal.dim,
                modifier = Modifier.padding(start = 10.dp, top = 6.dp),
            )
        }
        Text(
            text = highlight(block.code, block.lang, pal.inlineCode, pal.dim, pal.toolTint),
            style = MonoStyle,
            color = MaterialTheme.colorScheme.onSurface,
            softWrap = false,
            modifier = Modifier
                .horizontalScroll(scroll)
                .padding(horizontal = 10.dp, vertical = 8.dp),
        )
    }
}

@Composable
private fun TableBlock(block: MdBlock.Table, baseStyle: TextStyle) {
    val pal = palette
    val scroll = rememberScrollState()
    val cellStyle = baseStyle.copy(fontSize = 12.5.sp, lineHeight = 17.sp)
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .border(1.dp, pal.codeBorder, RoundedCornerShape(8.dp))
            .horizontalScroll(scroll),
    ) {
        Row(Modifier.background(pal.codeBg)) {
            block.header.forEach { cell ->
                Text(
                    text = annotate(cell, pal.inlineCode, pal.accent),
                    style = cellStyle.copy(fontWeight = FontWeight.SemiBold),
                    color = pal.heading,
                    modifier = Modifier
                        .width(TABLE_CELL_WIDTH)
                        .padding(horizontal = 8.dp, vertical = 6.dp),
                )
            }
        }
        block.rows.forEachIndexed { index, row ->
            Box(Modifier.fillMaxWidth().height(1.dp).background(pal.hairline))
            Row(
                Modifier.background(
                    if (index % 2 == 1) pal.codeBg.copy(alpha = 0.45f) else Color.Transparent,
                ),
            ) {
                row.forEach { cell ->
                    Text(
                        text = annotate(cell, pal.inlineCode, pal.accent),
                        style = cellStyle,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier
                            .width(TABLE_CELL_WIDTH)
                            .padding(horizontal = 8.dp, vertical = 6.dp),
                    )
                }
            }
        }
    }
}

private val TABLE_CELL_WIDTH = 150.dp

/** Spans -> AnnotatedString, with links carrying a real [LinkAnnotation.Url]. */
fun annotate(spans: List<MdSpan>, codeColor: Color, linkColor: Color): AnnotatedString =
    buildAnnotatedString {
        spans.forEach { span ->
            when (span) {
                is MdSpan.Text -> withSpan(span.style) { append(span.text) }
                is MdSpan.Code -> withStyle(
                    SpanStyle(
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.5.sp,
                        color = codeColor,
                    ),
                ) { append(span.text) }

                is MdSpan.Link -> withLink(
                    LinkAnnotation.Url(
                        url = span.href,
                        styles = TextLinkStyles(
                            style = SpanStyle(
                                color = linkColor,
                                textDecoration = TextDecoration.Underline,
                            ),
                        ),
                    ),
                ) { append(span.text) }
            }
        }
    }

private inline fun AnnotatedString.Builder.withSpan(
    style: MdStyle,
    block: AnnotatedString.Builder.() -> Unit,
) {
    if (!style.bold && !style.italic && !style.strike) {
        block()
        return
    }
    withStyle(
        SpanStyle(
            fontWeight = if (style.bold) FontWeight.Bold else null,
            fontStyle = if (style.italic) FontStyle.Italic else null,
            textDecoration = if (style.strike) TextDecoration.LineThrough else null,
        ),
    ) { block() }
}

/**
 * Deliberately shallow syntax tint — comments, strings and numbers only.
 *
 * A full tokenizer per language would be a lot of code for a phone-sized code
 * view; these three carry most of the readability win and cannot mis-colour a
 * language they were not written for.
 */
private fun highlight(
    code: String,
    lang: String,
    stringColor: Color,
    commentColor: Color,
    numberColor: Color,
): AnnotatedString = buildAnnotatedString {
    val commentPrefixes = when (lang) {
        "python", "py", "sh", "bash", "zsh", "shell", "yaml", "yml", "toml", "ruby", "rb" ->
            listOf("#")

        "sql" -> listOf("--")
        "" -> emptyList()
        else -> listOf("//")
    }
    code.split('\n').forEachIndexed { index, line ->
        if (index > 0) append('\n')
        val trimmed = line.trimStart()
        val comment = commentPrefixes.firstOrNull { trimmed.startsWith(it) }
        if (comment != null) {
            withStyle(SpanStyle(color = commentColor, fontStyle = FontStyle.Italic)) {
                append(line)
            }
            return@forEachIndexed
        }
        var i = 0
        val buffer = StringBuilder()
        fun flush() {
            if (buffer.isNotEmpty()) {
                append(buffer.toString()); buffer.clear()
            }
        }
        while (i < line.length) {
            val c = line[i]
            if (c == '"' || c == '\'' || c == '`') {
                val close = findStringEnd(line, i, c)
                if (close > i) {
                    flush()
                    withStyle(SpanStyle(color = stringColor)) {
                        append(line.substring(i, close + 1))
                    }
                    i = close + 1
                    continue
                }
            }
            if (c.isDigit() && (i == 0 || !line[i - 1].isLetterOrDigit())) {
                var j = i
                while (j < line.length && (line[j].isDigit() || line[j] == '.' ||
                        line[j] == 'x' || (line[j] in 'a'..'f') || (line[j] in 'A'..'F'))
                ) {
                    j++
                }
                if (j > i && (j >= line.length || !line[j].isLetter())) {
                    flush()
                    withStyle(SpanStyle(color = numberColor)) { append(line.substring(i, j)) }
                    i = j
                    continue
                }
            }
            buffer.append(c)
            i++
        }
        flush()
    }
}

private fun findStringEnd(line: String, start: Int, quote: Char): Int {
    var i = start + 1
    while (i < line.length) {
        if (line[i] == '\\') {
            i += 2; continue
        }
        if (line[i] == quote) return i
        i++
    }
    return -1
}
