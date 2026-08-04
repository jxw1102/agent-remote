package com.bb10d.remote.ui.markdown

/**
 * A small, deliberately incomplete markdown parser.
 *
 * The daemon already ships Cascades-flavoured HTML in each message's `blocks`,
 * but that palette and those tags exist for BB10's text engine. Android gets
 * the raw `text` instead and parses it here, so code blocks can scroll, tables
 * can be real columns, and links can be tappable — none of which survives a
 * pre-rendered HTML string.
 *
 * Scope is what agent output actually contains: ATX headings, fenced code,
 * bullet/ordered lists (nested), block quotes, pipe tables, thematic breaks,
 * and inline bold/italic/strike/code/links/autolinks. Anything unrecognised
 * degrades to a paragraph rather than being dropped.
 */

sealed interface MdBlock {
    data class Heading(val level: Int, val spans: List<MdSpan>) : MdBlock
    data class Paragraph(val spans: List<MdSpan>) : MdBlock
    data class Bullet(val indent: Int, val marker: String, val spans: List<MdSpan>) : MdBlock
    data class Code(val lang: String, val code: String) : MdBlock
    data class Quote(val blocks: List<MdBlock>) : MdBlock
    data class Table(val header: List<List<MdSpan>>, val rows: List<List<List<MdSpan>>>) : MdBlock
    data object Rule : MdBlock
}

sealed interface MdSpan {
    data class Text(val text: String, val style: MdStyle = MdStyle()) : MdSpan
    data class Code(val text: String) : MdSpan
    data class Link(val text: String, val href: String) : MdSpan
}

data class MdStyle(
    val bold: Boolean = false,
    val italic: Boolean = false,
    val strike: Boolean = false,
)

private val FENCE = Regex("^\\s{0,3}(`{3,}|~{3,})\\s*([A-Za-z0-9_+\\-.#]*)\\s*$")
private val ATX = Regex("^\\s{0,3}(#{1,6})\\s+(.*?)\\s*#*\\s*$")
private val RULE = Regex("^\\s{0,3}([-*_])\\s*(\\1\\s*){2,}$")
private val BULLET = Regex("^(\\s*)([-*+])\\s+(.*)$")
private val ORDERED = Regex("^(\\s*)(\\d{1,3})[.)]\\s+(.*)$")
private val QUOTE = Regex("^\\s{0,3}>\\s?(.*)$")
private val TABLE_SEP = Regex("^\\s*\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)+\\|?\\s*$")

object Markdown {

    fun parse(source: String): List<MdBlock> {
        if (source.isBlank()) return emptyList()
        val lines = source.replace("\r\n", "\n").replace('\r', '\n').split('\n')
        return parseLines(lines)
    }

    private fun parseLines(lines: List<String>): List<MdBlock> {
        val out = mutableListOf<MdBlock>()
        val paragraph = mutableListOf<String>()
        var i = 0

        fun flushParagraph() {
            if (paragraph.isEmpty()) return
            val text = paragraph.joinToString("\n").trim()
            paragraph.clear()
            if (text.isNotEmpty()) out += MdBlock.Paragraph(inline(text))
        }

        while (i < lines.size) {
            val line = lines[i]

            val fence = FENCE.find(line)
            if (fence != null) {
                flushParagraph()
                val marker = fence.groupValues[1]
                val lang = fence.groupValues[2]
                val body = mutableListOf<String>()
                i++
                while (i < lines.size) {
                    val candidate = lines[i]
                    val close = FENCE.find(candidate)
                    // A closing fence must use the same character and be at
                    // least as long; a shorter run inside stays literal.
                    if (close != null && close.groupValues[1].firstOrNull() == marker.first() &&
                        close.groupValues[1].length >= marker.length &&
                        close.groupValues[2].isEmpty()
                    ) {
                        i++
                        break
                    }
                    body += candidate
                    i++
                }
                out += MdBlock.Code(lang.lowercase(), body.joinToString("\n").trimEnd())
                continue
            }

            if (line.isBlank()) {
                flushParagraph()
                i++
                continue
            }

            if (RULE.matches(line)) {
                flushParagraph()
                out += MdBlock.Rule
                i++
                continue
            }

            val heading = ATX.find(line)
            if (heading != null) {
                flushParagraph()
                out += MdBlock.Heading(
                    heading.groupValues[1].length,
                    inline(heading.groupValues[2]),
                )
                i++
                continue
            }

            if (QUOTE.matches(line)) {
                flushParagraph()
                val body = mutableListOf<String>()
                while (i < lines.size) {
                    val m = QUOTE.find(lines[i]) ?: break
                    body += m.groupValues[1]
                    i++
                }
                out += MdBlock.Quote(parseLines(body))
                continue
            }

            // Pipe table: a header row followed by a |---|---| separator.
            if (line.contains('|') && i + 1 < lines.size && TABLE_SEP.matches(lines[i + 1])) {
                flushParagraph()
                val header = splitRow(line)
                i += 2
                val rows = mutableListOf<List<String>>()
                while (i < lines.size && lines[i].contains('|') && lines[i].isNotBlank()) {
                    rows += splitRow(lines[i])
                    i++
                }
                val width = maxOf(header.size, rows.maxOfOrNull { it.size } ?: 0)
                out += MdBlock.Table(
                    header = pad(header, width).map { inline(it) },
                    rows = rows.map { r -> pad(r, width).map { inline(it) } },
                )
                continue
            }

            val bullet = BULLET.find(line)
            val ordered = if (bullet == null) ORDERED.find(line) else null
            if (bullet != null || ordered != null) {
                flushParagraph()
                val indentText = (bullet ?: ordered)!!.groupValues[1]
                val indent = (indentText.replace("\t", "    ").length / 2).coerceIn(0, 4)
                val marker = if (bullet != null) "•" else ordered!!.groupValues[2] + "."
                val body = StringBuilder((bullet ?: ordered)!!.groupValues[3])
                i++
                // Continuation lines: indented, not a new list item or block.
                while (i < lines.size) {
                    val next = lines[i]
                    if (next.isBlank() || BULLET.matches(next) || ORDERED.matches(next) ||
                        ATX.matches(next) || FENCE.matches(next) || QUOTE.matches(next)
                    ) {
                        break
                    }
                    if (!next.startsWith(" ") && !next.startsWith("\t")) break
                    body.append('\n').append(next.trim())
                    i++
                }
                out += MdBlock.Bullet(indent, marker, inline(body.toString()))
                continue
            }

            paragraph += line
            i++
        }
        flushParagraph()
        return out
    }

    private fun pad(cells: List<String>, width: Int): List<String> =
        if (cells.size >= width) cells.take(width) else cells + List(width - cells.size) { "" }

    private fun splitRow(line: String): List<String> {
        var s = line.trim()
        if (s.startsWith("|")) s = s.substring(1)
        if (s.endsWith("|") && !s.endsWith("\\|")) s = s.dropLast(1)
        val cells = mutableListOf<String>()
        val cur = StringBuilder()
        var escaped = false
        for (ch in s) {
            when {
                escaped -> {
                    cur.append(ch); escaped = false
                }

                ch == '\\' -> escaped = true
                ch == '|' -> {
                    cells += cur.toString().trim(); cur.clear()
                }

                else -> cur.append(ch)
            }
        }
        cells += cur.toString().trim()
        return cells
    }

    // -- inline ------------------------------------------------------------

    /**
     * Inline pass. Code spans win over everything (their content is literal),
     * then links, then emphasis. Underscore emphasis is only honoured at word
     * boundaries so `snake_case_names` survive intact — agent output is full
     * of them.
     */
    fun inline(source: String): List<MdSpan> {
        val out = mutableListOf<MdSpan>()
        emit(source, MdStyle(), out)
        return out.ifEmpty { listOf(MdSpan.Text(source)) }
    }

    private fun emit(text: String, style: MdStyle, out: MutableList<MdSpan>) {
        if (text.isEmpty()) return
        var i = 0
        val buffer = StringBuilder()

        fun flush() {
            if (buffer.isNotEmpty()) {
                out += MdSpan.Text(buffer.toString(), style)
                buffer.clear()
            }
        }

        while (i < text.length) {
            val c = text[i]

            if (c == '\\' && i + 1 < text.length && !text[i + 1].isLetterOrDigit()) {
                buffer.append(text[i + 1]); i += 2; continue
            }

            if (c == '`') {
                val ticks = countRun(text, i, '`')
                val closeAt = findRun(text, i + ticks, '`', ticks)
                if (closeAt > 0) {
                    flush()
                    out += MdSpan.Code(text.substring(i + ticks, closeAt).trim())
                    i = closeAt + ticks
                    continue
                }
            }

            if (c == '[') {
                val link = matchLink(text, i)
                if (link != null) {
                    flush()
                    // Link labels can be styled; keep it simple and honest —
                    // the whole label becomes one tappable run.
                    out += MdSpan.Link(stripInline(link.label), link.href)
                    i = link.end
                    continue
                }
            }

            if (c == '!' && i + 1 < text.length && text[i + 1] == '[') {
                val link = matchLink(text, i + 1)
                if (link != null) {
                    flush()
                    val label = stripInline(link.label).ifBlank { "image" }
                    out += MdSpan.Link("🖼 $label", link.href)
                    i = link.end
                    continue
                }
            }

            if (c == '*' || c == '_') {
                val run = countRun(text, i, c)
                if (run in 1..3 && canOpen(text, i, c)) {
                    val closeAt = findEmphasisClose(text, i + run, c, run)
                    if (closeAt > 0) {
                        val inner = text.substring(i + run, closeAt)
                        val next = when (run) {
                            1 -> style.copy(italic = true)
                            2 -> style.copy(bold = true)
                            else -> style.copy(bold = true, italic = true)
                        }
                        flush()
                        emit(inner, next, out)
                        i = closeAt + run
                        continue
                    }
                }
            }

            if (c == '~' && countRun(text, i, '~') == 2) {
                val closeAt = findRun(text, i + 2, '~', 2)
                if (closeAt > 0) {
                    flush()
                    emit(text.substring(i + 2, closeAt), style.copy(strike = true), out)
                    i = closeAt + 2
                    continue
                }
            }

            if ((c == 'h' || c == 'w') && isBareUrlStart(text, i)) {
                val end = bareUrlEnd(text, i)
                if (end > i) {
                    flush()
                    val raw = text.substring(i, end)
                    out += MdSpan.Link(raw, if (raw.startsWith("www.")) "https://$raw" else raw)
                    i = end
                    continue
                }
            }

            buffer.append(c)
            i++
        }
        flush()
    }

    private data class LinkMatch(val label: String, val href: String, val end: Int)

    private fun matchLink(text: String, start: Int): LinkMatch? {
        var depth = 0
        var i = start
        while (i < text.length) {
            when (text[i]) {
                '\\' -> i++
                '[' -> depth++
                ']' -> {
                    depth--
                    if (depth == 0) break
                }
            }
            i++
        }
        if (i >= text.length || depth != 0) return null
        val label = text.substring(start + 1, i)
        if (i + 1 >= text.length || text[i + 1] != '(') return null
        var j = i + 2
        var paren = 1
        while (j < text.length) {
            when (text[j]) {
                '\\' -> j++
                '(' -> paren++
                ')' -> {
                    paren--
                    if (paren == 0) break
                }
            }
            j++
        }
        if (j >= text.length) return null
        val target = text.substring(i + 2, j).trim()
        // Drop a title: [x](url "title")
        val href = target.substringBefore(' ').trim().trim('<', '>')
        if (href.isEmpty()) return null
        return LinkMatch(label, href, j + 1)
    }

    private fun stripInline(label: String): String =
        label.replace(Regex("[*_`~]"), "").trim()

    private fun countRun(text: String, at: Int, ch: Char): Int {
        var n = 0
        while (at + n < text.length && text[at + n] == ch) n++
        return n
    }

    private fun findRun(text: String, from: Int, ch: Char, length: Int): Int {
        var i = from
        while (i < text.length) {
            if (text[i] == ch && countRun(text, i, ch) == length) return i
            if (text[i] == ch) i += countRun(text, i, ch) else i++
        }
        return -1
    }

    /** `_` inside a word is part of the word, not emphasis. */
    private fun canOpen(text: String, at: Int, ch: Char): Boolean {
        if (ch == '*') return at + 1 < text.length && !text[at + 1].isWhitespace()
        val before = text.getOrNull(at - 1)
        return (before == null || !before.isLetterOrDigit()) &&
            at + 1 < text.length && !text[at + 1].isWhitespace()
    }

    private fun findEmphasisClose(text: String, from: Int, ch: Char, length: Int): Int {
        var i = from
        while (i < text.length) {
            if (text[i] == '\\') {
                i += 2; continue
            }
            if (text[i] == ch) {
                val run = countRun(text, i, ch)
                val prev = text.getOrNull(i - 1)
                val closes = run >= length && prev != null && !prev.isWhitespace() &&
                    (ch == '*' || text.getOrNull(i + run)?.isLetterOrDigit() != true)
                if (closes) return i
                i += run
                continue
            }
            i++
        }
        return -1
    }

    private fun isBareUrlStart(text: String, at: Int): Boolean {
        if (at > 0 && (text[at - 1].isLetterOrDigit() || text[at - 1] == '/')) return false
        return text.startsWith("http://", at) || text.startsWith("https://", at) ||
            text.startsWith("www.", at)
    }

    private fun bareUrlEnd(text: String, at: Int): Int {
        var end = at
        while (end < text.length && !text[end].isWhitespace() && text[end] != '`' &&
            text[end] != '<' && text[end] != '>' && text[end] != '|'
        ) {
            end++
        }
        // Trailing sentence punctuation is not part of the URL.
        while (end > at && text[end - 1] in ".,;:!?)]}\"'") end--
        return if (end - at > 8) end else -1
    }
}
