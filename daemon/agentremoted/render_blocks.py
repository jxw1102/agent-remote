"""
Native render blocks for BB10 Cascades ListView (format: tui-v2).

Block kinds: gap, hr, user, h, p, li, code, th, tr, img.

Textual blocks carry:
  text  — plain (markers stripped)
  rich  — minimal Qt rich-text for Label TextFormat.Html
           bold, inline code color, links (cyan, no underline)

Tables: real multi-column cells c0..c5 / c0r..c5r. Widths w0..w5 always
fill the phone row (content-proportional, no wasted empty strip). Never
collapse wide tables to a single key:value column.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Tuple

# GrokNight-ish palette (TUI) — cyan for both inline code and links
COLOR_INLINE_CODE = "#67e8f9"  # cyan
COLOR_LINK = "#67e8f9"  # cyan
COLOR_HEADING = "#c678dd"  # purple (client also hardcodes)
# Grok Build thinking-block header: dim/muted (header_bright=false)
COLOR_META_THOUGHT = "#9a8fb0"  # muted purple — thinking accent family
COLOR_META_WORKED = "#6b7280"  # dim gray — worked line (text color stays)
COLOR_META = COLOR_META_WORKED
COLOR_THOUGHT_ACCENT = "#6b5b7a"  # left quote bar (thought)
COLOR_WORKED_ACCENT = "#4b5563"  # left quote bar (worked) — same format, gray
COLOR_BODY = "#d0d0d0"  # default body when wrapping rich HTML

# Cascades Html Labels:
#  - must NOT set textStyle (theme white overrides <font color>)
#  - must NOT nest <font> (outer size-only wrapper kills inner colors)
#  - BB10 is most reliable with COLOR ONLY (no size= on <font>)
HTML_SIZE_BODY = 2  # unused for Cascades tags; kept for docs
HTML_SIZE_HEADING = 4


def _esc(s: str) -> str:
    """Escape HTML specials but keep apostrophes (Cascades shows &#x27; literally)."""
    return html.escape(s or "", quote=False)


def _wrap_color(inner_html: str, color: str) -> str:
    """Dual-emit color: <span style> outside, <font color> inside.

    Different Cascades text engines honor different subsets — some OS
    builds render <font color>, others only CSS color on <span> (observed
    on-device: <b> works but <font color> paints theme-white). Emitting
    both is harmless: whichever tag the engine knows wins, the other is
    ignored.
    """
    if not inner_html:
        return ""
    return '<span style="color:%s"><font color="%s">%s</font></span>' % (
        color, color, inner_html)


def _font(
    text: str,
    color: str,
    size: int = HTML_SIZE_BODY,
    bold: bool = False,
    underline: bool = False,
) -> str:
    """Flat colored run (span+font dual emit) — Cascades-safe (no size,
    no color nesting).

    `size` is ignored: on BB10 Cascades, size+color on the same tag (or nested
    size wrappers) often renders monochrome theme text.
    """
    if not text:
        return ""
    body = _esc(text)
    if bold:
        body = "<b>%s</b>" % body
    if underline:
        body = "<u>%s</u>" % body
    return _wrap_color(body, color)


def cascades_safe_rich(rich: str) -> str:
    """Normalize any rich HTML to Cascades-safe flat colored fonts.

    - Unwrap outer <font size="N">…</font> wrappers
    - Strip size= from all font tags
    - Leave color= intact
    """
    if not rich:
        return rich
    s = rich
    # Repeatedly unwrap size-only outer wrappers
    for _ in range(4):
        m = re.match(
            r'^<font(\s+size="?\d+"?)?\s*>(.*)</font>\s*$',
            s,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not m:
            break
        # only unwrap if this outer tag has no color
        open_tag = m.group(0)[: m.group(0).find(">") + 1]
        if re.search(r'color\s*=', open_tag, re.I):
            break
        s = m.group(2)

    # Strip size attributes everywhere
    s = re.sub(r'\s+size="?\d+"?', "", s, flags=re.I)
    # Normalize color="#x" spacing
    s = re.sub(
        r'<font\s+color\s*=\s*"?(#[0-9A-Fa-f]{3,8})"?\s*>',
        r'<font color="\1">',
        s,
    )
    return s


def _size_wrap(rich: str, size: int = HTML_SIZE_BODY) -> str:
    """Legacy no-op path — return Cascades-safe rich (never nest size wrappers)."""
    return cascades_safe_rich(rich or "")


# Phone UI supports up to 6 columns.
# Classic / Q20 is 720px wide; list paddings (6+6 outer, 4+4 inner) leave ~700.
MAX_TABLE_COLS = 6
CHAR_PX = 7  # ~XXSmall glyph width estimate
CELL_PAD_PX = 12
MIN_COL_W = 40
# Soft weight cap (chars): long cells share remaining space instead of dominating
WEIGHT_CHAR_CAP = 28
# Fixed full row width every table must fill (px of cell strip, excluding gaps)
PHONE_ROW_WIDTH = 692
COL_GAP_PX = 6
FORMAT_ID = "tui-v2"

# Syntax highlight palette (dark / GrokNight-ish) — used if Pygments style
# inline colors need a fallback for unknown langs.
COLOR_CODE_DEFAULT = "#a8d4a8"


def _span_styles_to_font(html: str) -> str:
    """Convert Pygments <span style="color:…"> into dual span+font runs.

    Tokenize first so the generic style=/class= cleanup can't eat the
    colored spans we emit ourselves.
    """
    if not html:
        return html

    def open_repl(m: re.Match) -> str:
        style = m.group(1) or ""
        cm = re.search(r"color:\s*(#[0-9A-Fa-f]{3,8})", style, re.I)
        color = cm.group(1) if cm else COLOR_CODE_DEFAULT
        return "\x00O%s\x00" % color

    s = re.sub(
        r'<span\s+[^>]*style="([^"]*)"[^>]*>',
        open_repl,
        html,
        flags=re.I,
    )
    # bare spans without style: still open a run so </span> counts match
    s = re.sub(r"<span[^>]*>", "\x00O%s\x00" % COLOR_CODE_DEFAULT, s, flags=re.I)
    s = re.sub(r"</span>", "\x00C\x00", s, flags=re.I)
    # Drop leftover style= / class= attributes on other tags if any
    s = re.sub(r'\s+style="[^"]*"', "", s, flags=re.I)
    s = re.sub(r'\s+class="[^"]*"', "", s, flags=re.I)
    # Expand tokens into dual span+font runs
    s = re.sub(
        r"\x00O(#[0-9A-Fa-f]{3,8})\x00",
        r'<span style="color:\1"><font color="\1">',
        s,
    )
    s = s.replace("\x00C\x00", "</font></span>")
    return cascades_safe_rich(s)


def highlight_code_html(code: str, lang: str = "") -> str:
    """Language-aware syntax highlighting → Cascades-safe <font color> HTML.

    Pygments emits span+CSS; we convert to flat font tags so phone Labels
    (and QTextDocument) both show multi-color code without CustomControl.
    """
    if not code:
        return ""
    text = code
    # Hard cap for phone paint memory
    if len(text) > 12000:
        text = text[:12000] + "\n…"

    try:
        from pygments import highlight
        from pygments.lexers import get_lexer_by_name, guess_lexer_for_filename
        from pygments.lexers import TextLexer
        from pygments.formatters import HtmlFormatter
        from pygments.util import ClassNotFound
    except ImportError:
        return _font(text, COLOR_CODE_DEFAULT)

    lexer = None
    name = (lang or "").strip().lower()
    # Common aliases → pygments names
    aliases = {
        "js": "javascript",
        "ts": "typescript",
        "tsx": "tsx",
        "jsx": "jsx",
        "py": "python",
        "rb": "ruby",
        "sh": "bash",
        "shell": "bash",
        "zsh": "bash",
        "yml": "yaml",
        "c++": "cpp",
        "h": "c",
        "hpp": "cpp",
        "kt": "kotlin",
        "rs": "rust",
        "cs": "csharp",
        "objc": "objective-c",
        "qml": "qml",
        "json": "json",
        "md": "markdown",
        "dockerfile": "docker",
        "text": "text",
        "txt": "text",
        "plain": "text",
    }
    if name in aliases:
        name = aliases[name]

    if name and name not in ("text", "plain", "txt"):
        try:
            lexer = get_lexer_by_name(name, stripnl=False, stripall=False)
        except ClassNotFound:
            lexer = None
    if lexer is None and name:
        # try as filename extension
        try:
            lexer = guess_lexer_for_filename("file." + name, text)
        except (ClassNotFound, ValueError):
            lexer = None
    if lexer is None:
        # unknown / empty lang: still lightly color as plain mono text
        try:
            lexer = get_lexer_by_name("text", stripnl=False)
        except ClassNotFound:
            lexer = TextLexer(stripnl=False)

    # monokai: good contrast on dark #1a1a1a panel
    formatter = HtmlFormatter(
        style="monokai",
        noclasses=True,
        nobackground=True,
        nowrap=True,
        lineseparator="<br/>\n",
    )
    try:
        html = highlight(text, lexer, formatter)
    except Exception:
        return _font(text, COLOR_CODE_DEFAULT)

    # Pygments wraps in optional spans; ensure no outer pre/div with backgrounds
    html = html.strip()
    html = re.sub(r"^<div[^>]*>", "", html)
    html = re.sub(r"</div>$", "", html)
    html = re.sub(r"^<pre[^>]*>", "", html)
    html = re.sub(r"</pre>$", "", html)
    html = _span_styles_to_font(html)
    # Do NOT wrap the whole block in another <font> — Cascades often paints
    # nested <font color> as monochrome. Token fonts are already colored.
    if "color" not in html.lower():
        return _wrap_color(html, COLOR_CODE_DEFAULT)
    return html


def strip_inline_md(s: str) -> str:
    """Plain text fallback (markers removed)."""
    if not s:
        return ""
    t = s
    t = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", t)
    t = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"~~(.+?)~~", r"\1", t)
    return t.strip()


def inline_to_rich(md: str) -> Tuple[str, str]:
    """Convert inline markdown → (plain, qt_rich_html).

    Cascades Label TextFormat.Html only reliably honors:
      <b> <i> <u> <font color="#rrggbb"> <br/>
    Avoid <a>/<span style=…> — theme overrides / CSS ignored.
    """
    if not md:
        return "", ""

    s = md
    # Token buckets
    codes: List[str] = []
    links: List[Tuple[str, str]] = []  # (label, url)
    bolds: List[str] = []

    def take_code(m: re.Match) -> str:
        codes.append(m.group(1))
        return "\x00C%d\x00" % (len(codes) - 1)

    def take_link(m: re.Match) -> str:
        links.append((m.group(1), m.group(2)))
        return "\x00L%d\x00" % (len(links) - 1)

    def take_bare_url(m: re.Match) -> str:
        u = m.group(0)
        # trim trailing punctuation common in prose
        while u and u[-1] in ".,;:)]}>\"'":
            u = u[:-1]
        if not u:
            return m.group(0)
        links.append((u, u))
        return "\x00L%d\x00" % (len(links) - 1)

    def take_bold(m: re.Match) -> str:
        bolds.append(m.group(1))
        return "\x00B%d\x00" % (len(bolds) - 1)

    # Normalize awkward markdown-in-markdown: `` `code` `` → `code`
    s = re.sub(r"``\s*`([^`]+)`\s*``", r"`\1`", s)
    s = re.sub(r"``([^`]+)``", r"`\1`", s)

    # Order: code first (so * inside code stays), then links, bare urls, bold.
    s = re.sub(r"`([^`]+)`", take_code, s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", s)  # images handled elsewhere
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", take_link, s)
    s = re.sub(r"https?://[^\s<]+", take_bare_url, s)
    s = re.sub(r"\*\*(.+?)\*\*", take_bold, s)
    s = re.sub(r"__(.+?)__", take_bold, s)

    plain = strip_inline_md(md)

    # No markdown spans → keep plain text (avoid &#x27; etc. on Plain labels)
    if not codes and not links and not bolds:
        return plain, plain

    def expand_inline_tokens(fragment: str, bold: bool = False) -> str:
        """Expand code/link tokens into flat <font size color> spans (no nesting)."""
        bits = re.split(r"(\x00[CL]\d+\x00)", fragment)
        acc: List[str] = []
        for bit in bits:
            if not bit:
                continue
            cm = re.match(r"^\x00C(\d+)\x00$", bit)
            if cm:
                # Cyan inline code
                acc.append(
                    _font(codes[int(cm.group(1))], COLOR_INLINE_CODE, bold=bold)
                )
                continue
            lm = re.match(r"^\x00L(\d+)\x00$", bit)
            if lm:
                lab, _url = links[int(lm.group(1))]
                # Cascades-safe link: cyan only, no <a>, no underline.
                acc.append(_font(lab, COLOR_LINK, bold=bold))
                continue
            # Body text: explicit gray so Html Label needs no textStyle.color
            acc.append(_font(bit, COLOR_BODY, bold=bold))
        return "".join(acc)

    # Bold first as a flag on nested expand — never wrap fonts inside fonts
    parts = re.split(r"(\x00B\d+\x00)", s)
    out: List[str] = []
    for part in parts:
        if not part:
            continue
        bm = re.match(r"^\x00B(\d+)\x00$", part)
        if bm:
            inner = bolds[int(bm.group(1))]
            out.append(expand_inline_tokens(inner, bold=True))
            continue
        out.append(expand_inline_tokens(part, bold=False))

    rich = cascades_safe_rich("".join(out))
    return plain, rich


def _text_block(kind: str, role: str, md: str, **extra: Any) -> Dict[str, Any]:
    plain, rich = inline_to_rich(md)
    if rich and rich != plain:
        rich = cascades_safe_rich(rich)
    b: Dict[str, Any] = {
        "k": kind,
        "role": role,
        "text": plain,
        "rich": rich,
        "fmt": "rich",
    }
    b.update(extra)
    return b


def markdown_to_blocks(md: str, role: str = "assistant") -> List[Dict[str, Any]]:
    if not md or not md.strip():
        return []

    text = md.replace("\r\n", "\n").replace("\r", "\n")
    fences: List[Tuple[str, str]] = []
    images: List[Dict[str, str]] = []

    def save_fence(m: re.Match) -> str:
        fences.append(((m.group(1) or "").strip(), (m.group(2) or "").rstrip("\n")))
        return "\x00FENCE%d\x00" % (len(fences) - 1)

    def save_image(m: re.Match) -> str:
        alt = (m.group(1) or "").strip()
        url = (m.group(2) or "").strip()
        if url:
            images.append(
                {
                    "k": "img",
                    "role": role,
                    "url": url,
                    "alt": alt or "image",
                    "text": alt or url,
                    "rich": "",
                    "fmt": "plain",
                }
            )
            return "\x00IMG%d\x00" % (len(images) - 1)
        return " "

    text = re.sub(r"```([^\n`]*)\n(.*?)```", save_fence, text, flags=re.DOTALL)
    text = re.sub(r"~~~([^\n]*)\n(.*?)~~~", save_fence, text, flags=re.DOTALL)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", save_image, text)

    lines = text.split("\n")
    blocks: List[Dict[str, Any]] = []
    para: List[str] = []
    i = 0

    def flush_para() -> None:
        nonlocal para
        if not para:
            return
        chunk = " ".join(para).strip()
        para = []
        if not chunk:
            return
        parts = re.split(r"(\x00IMG\d+\x00)", chunk)
        for part in parts:
            im = re.match(r"^\x00IMG(\d+)\x00$", part)
            if im:
                blocks.append(dict(images[int(im.group(1))]))
                continue
            if part.strip():
                blocks.append(_text_block("p", role, part.strip()))

    def is_table_sep(line: str) -> bool:
        s = line.strip()
        if "|" not in s:
            return False
        cells = split_table_row(s)
        return bool(cells) and all(re.match(r"^:?-+:?$", c or "-") for c in cells)

    def is_table_row(line: str) -> bool:
        s = line.strip()
        # Leading | is common; also allow "a | b | c" style
        if s.startswith("|") and s.count("|") >= 2:
            return True
        return s.count("|") >= 2 and not s.startswith("#")

    def emit_table(header: List[str], body_rows: List[List[str]]) -> None:
        # Always real multi-column rows (never collapse to 1-col KV).
        # Cap at MAX_TABLE_COLS; column widths scale to phone width.
        all_src = [list(header or [])] + [list(r) for r in body_rows]
        # Drop trailing all-empty source rows
        while all_src and all(not (c or "").strip() for c in all_src[-1]):
            all_src.pop()
        if not all_src:
            return

        # Column count = max over rows of (last non-empty cell index + 1),
        # or at least the declared header length when cells are empty (KV tables).
        ncols = max(1, min(len(header or []), MAX_TABLE_COLS))
        for r in all_src:
            last = -1
            for i, c in enumerate(r):
                if (c or "").strip():
                    last = i
            if last >= 0:
                ncols = max(ncols, last + 1)
        ncols = min(ncols, MAX_TABLE_COLS)

        def norm_row(r: List[str]) -> List[str]:
            row = list(r[:ncols]) + [""] * max(0, ncols - len(r))
            return row[:ncols]

        # GFM often uses empty header: | | | / |--|--| then key|value rows
        hdr = norm_row(header or [])
        header_empty = all(not (c or "").strip() for c in hdr)
        norm_body = [
            r
            for r in (norm_row(br) for br in body_rows)
            if any((c or "").strip() for c in r)
        ]
        if header_empty and not norm_body:
            return
        if not header_empty and not norm_body and not any(hdr):
            return

        width_header = hdr if not header_empty else (
            norm_body[0] if norm_body else hdr
        )
        width_body = (
            norm_body if not header_empty else norm_body[1:]
        )
        widths = _compute_full_width_cols(width_header, width_body, ncols)

        if not header_empty:
            blocks.append(
                _row_block("th", hdr, role, header=True, widths=widths)
            )
        for row in norm_body:
            blocks.append(
                _row_block("tr", row, role, header=False, widths=widths)
            )

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        fm = re.match(r"^\x00FENCE(\d+)\x00$", stripped)
        if fm:
            flush_para()
            lang, body = fences[int(fm.group(1))]
            if len(body) > 12000:
                body = body[:12000] + "\n…"
            rich = highlight_code_html(body, lang or "")
            blocks.append(
                {
                    "k": "code",
                    "role": role,
                    "text": body,
                    "rich": rich,
                    "fmt": "rich",
                    "lang": (lang or "").strip(),
                }
            )
            i += 1
            continue

        im_line = re.match(r"^\x00IMG(\d+)\x00$", stripped)
        if im_line:
            flush_para()
            blocks.append(dict(images[int(im_line.group(1))]))
            i += 1
            continue

        if is_table_row(line) and i + 1 < len(lines) and is_table_sep(lines[i + 1]):
            flush_para()
            rows = []
            j = i
            while j < len(lines) and is_table_row(lines[j]):
                rows.append(split_table_row(lines[j]))
                j += 1
            if len(rows) >= 2:
                emit_table(rows[0], rows[2:])
                i = j
                continue

        h = re.match(r"^(#{1,4})\s+(.+)$", line)
        if h:
            flush_para()
            level = len(h.group(1))
            plain, rich = inline_to_rich(h.group(2).strip())
            # Headings: purple + bold. Never wrap colored runs in another
            # colored tag (nested colors paint monochrome on BB10) — when
            # the heading has inline markup, boldify its runs instead.
            if rich == plain or not rich:
                hrich = _font(plain, COLOR_HEADING, bold=True)
            else:
                hrich = re.sub(
                    r"(<font[^>]*>)(.*?)(</font>)",
                    r"\1<b>\2</b>\3",
                    rich,
                    flags=re.DOTALL,
                )
            blocks.append(
                {
                    "k": "h",
                    "role": role,
                    "level": level,
                    "text": plain,
                    "rich": hrich,
                    "fmt": "rich",
                }
            )
            i += 1
            continue

        if re.match(r"^---+\s*$", line) or re.match(r"^\*\*\*+\s*$", line):
            flush_para()
            blocks.append(
                {
                    "k": "hr",
                    "role": role,
                    "text": "",
                    "rich": "",
                    "fmt": "plain",
                }
            )
            i += 1
            continue

        ul = re.match(r"^[\-\+]\s+(.+)$", line) or re.match(
            r"^\*\s+(?!\*)(.+)$", line
        )
        if ul:
            flush_para()
            plain, rich = inline_to_rich(ul.group(1).strip())
            blocks.append(
                {
                    "k": "li",
                    "role": role,
                    "ord": 0,
                    "text": plain,
                    "rich": rich,
                    "fmt": "rich",
                    "prefix": "•  ",
                }
            )
            i += 1
            continue

        ol = re.match(r"^(\d+)\.\s+(.+)$", line)
        if ol:
            flush_para()
            n = int(ol.group(1))
            plain, rich = inline_to_rich(ol.group(2).strip())
            blocks.append(
                {
                    "k": "li",
                    "role": role,
                    "ord": n,
                    "text": plain,
                    "rich": rich,
                    "fmt": "rich",
                    "prefix": "%d.  " % n,
                }
            )
            i += 1
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return blocks


def split_table_row(line: str) -> List[str]:
    """Split a markdown table row on |, honoring \\| as a literal pipe."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    # Split on | not preceded by backslash
    parts = re.split(r"(?<!\\)\|", s)
    return [p.replace("\\|", "|").strip() for p in parts]


def _col_content_lens(
    header: List[str], body_rows: List[List[str]], ncols: int
) -> List[int]:
    """Longest plain-text line length per column (for weight)."""
    maxlens = [1] * ncols
    for row in [header] + list(body_rows):
        for i in range(ncols):
            raw = row[i] if i < len(row) else ""
            plain = strip_inline_md(raw)
            ln = 0
            for line in plain.split("\n"):
                ln = max(ln, len(line))
            maxlens[i] = max(maxlens[i], max(1, ln))
    return maxlens


def _compute_full_width_cols(
    header: List[str], body_rows: List[List[str]], ncols: int
) -> List[int]:
    """Always fill PHONE_ROW_WIDTH; share by content, no wasted empty strip.

    Logic:
      1. Weight each column by its longest cell (chars), soft-capped so one
         huge cell cannot steal almost everything.
      2. Partition the full available pixel budget proportionally to weights.
      3. Enforce MIN_COL_W, then re-fix rounding so widths sum exactly to
         the full row (every table is full-bleed; leftover goes to heaviest
         columns — the ones that need wrap room).
    """
    if ncols <= 0:
        return []
    maxlens = _col_content_lens(header, body_rows, ncols)

    # Soft-capped weights: short labels stay narrow; long prose gets room.
    weights = [float(max(2, min(ml, WEIGHT_CHAR_CAP))) for ml in maxlens]
    # Tiny boost for columns that look like headers/keys (short labels)
    # is unnecessary — proportional weight already keeps them smaller.

    gaps = COL_GAP_PX * max(0, ncols - 1)
    avail = max(ncols * MIN_COL_W, PHONE_ROW_WIDTH - gaps)
    wsum = sum(weights) or float(ncols)

    # Proportional assignment
    raw = [avail * (wt / wsum) for wt in weights]
    widths = [max(MIN_COL_W, int(round(r))) for r in raw]

    # If MIN floors overshoot, shave heaviest columns first
    def total() -> int:
        return sum(widths)

    while total() > avail:
        order = sorted(range(ncols), key=lambda i: widths[i], reverse=True)
        shaved = False
        for i in order:
            if widths[i] > MIN_COL_W:
                widths[i] -= 1
                shaved = True
                break
        if not shaved:
            break

    # Grow to exact full width — give leftover to heaviest (longest content)
    leftover = avail - total()
    if leftover > 0:
        order = sorted(range(ncols), key=lambda i: (maxlens[i], widths[i]), reverse=True)
        i = 0
        while leftover > 0 and ncols:
            widths[order[i % ncols]] += 1
            leftover -= 1
            i += 1

    return [int(x) for x in widths]


def _cell_rich(md: str, header: bool) -> Tuple[str, str]:
    """(plain, rich) for one table cell — flat font tags only."""
    p, r = inline_to_rich(md)
    p = (p or "").strip()
    if not p and not (md or "").strip():
        return "", ""
    if header:
        # Prefer re-font plain as bold light so we never nest <b> around <font>
        if r and r != p and "<font" in r:
            # Make each font span bold by injecting <b> inside (flat structure)
            def boldify(fm: re.Match) -> str:
                return "%s<b>%s</b></font>" % (fm.group(1), fm.group(2))

            rich = re.sub(
                r"(<font[^>]*>)(.*?)</font>", boldify, r, flags=re.DOTALL
            )
            return p, rich
        return p, _font(p, "#e8e8e8", bold=True)
    if r and r != p and "<font" in r:
        return p, r
    return p, _font(p, COLOR_BODY)


def _row_block(
    kind: str,
    cells: List[str],
    role: str,
    header: bool,
    widths: List[int],
) -> Dict[str, Any]:
    plains: List[str] = []
    richs: List[str] = []
    for c in cells:
        p, r = _cell_rich(c, header=header)
        plains.append(p)
        richs.append(r)

    ncols = min(len(plains), MAX_TABLE_COLS)
    # pad to MAX_TABLE_COLS for stable QML fields
    while len(plains) < MAX_TABLE_COLS:
        plains.append("")
        richs.append("")
    w = list(widths[:MAX_TABLE_COLS])
    while len(w) < MAX_TABLE_COLS:
        w.append(MIN_COL_W)

    # Fallback single-line (if multi-col UI can't bind) — tab-ish spaces
    line = "  ".join(x for x in plains[:ncols] if x)
    line_r = "  ".join(x for x in richs[:ncols] if x)
    # Full-bleed: active columns always sum to PHONE_ROW_WIDTH (+ gaps for layout)
    row_width = sum(w[:ncols]) + COL_GAP_PX * max(0, ncols - 1)

    out: Dict[str, Any] = {
        "k": kind,
        "role": role,
        "text": line,
        "rich": line_r,
        "fmt": "rich",
        "ncols": ncols,
        "header": header,
        "rowWidth": int(row_width),
        # scroll flag kept for older clients; current QML uses multi-col no ScrollView
        "scroll": 0,
    }
    for i in range(MAX_TABLE_COLS):
        out["c%d" % i] = plains[i]
        out["c%dr" % i] = richs[i]
        out["w%d" % i] = int(w[i])
        out["hasC%d" % i] = 1 if i < ncols else 0
    return out


def messages_to_blocks(messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    first = True
    for m in messages:
        role = m.get("role") or ""
        text = (m.get("text") or "").strip()
        if not text or role in ("tool", "thought"):
            continue

        # Timing: Thought for (thinking header) / Worked for (turn footer)
        if role == "status":
            meta_kind = (m.get("metaKind") or "").strip().lower()
            if not meta_kind:
                if text.startswith("Thought for"):
                    meta_kind = "thought"
                elif text.startswith("Worked for"):
                    meta_kind = "worked"
                else:
                    meta_kind = "status"
            if meta_kind == "thought":
                color = COLOR_META_THOUGHT
            elif meta_kind == "worked":
                color = COLOR_META_WORKED
            else:
                color = COLOR_META
            # Same quote format for Thought and Worked (italic); colors differ
            rich = _wrap_color("<i>%s</i>" % _esc(text), color)
            # No full gap before Thought (sits under user); small gap before Worked
            if meta_kind == "worked" and not first:
                out.append(
                    {
                        "k": "gap",
                        "role": "status",
                        "text": "",
                        "rich": "",
                        "fmt": "plain",
                    }
                )
            out.append(
                {
                    "k": "meta",
                    "role": "status",
                    "metaKind": meta_kind,
                    "text": text,
                    "rich": rich,
                    "fmt": "rich",
                    # Quote bar for both Thought and Worked (same chrome)
                    "accent": 1 if meta_kind in ("thought", "worked") else 0,
                }
            )
            first = False
            continue

        if not first:
            # Don't insert a large gap when previous block was Thought-for
            # (thinking header should sit tight above assistant content)
            prev_was_thought = (
                out
                and out[-1].get("k") == "meta"
                and out[-1].get("metaKind") == "thought"
            )
            if not prev_was_thought:
                out.append(
                    {
                        "k": "gap",
                        "role": role,
                        "text": "",
                        "rich": "",
                        "fmt": "plain",
                    }
                )
        first = False

        if role == "user":
            body = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
            plain, rich = inline_to_rich(body.strip())
            if plain or rich:
                out.append(
                    {
                        "k": "user",
                        "role": "user",
                        "text": plain,
                        "rich": rich,
                        "fmt": "rich",
                    }
                )
        else:
            out.extend(markdown_to_blocks(text, role="assistant"))
    return out


def format_transcript_text(messages: List[Dict[str, str]]) -> str:
    blocks = []
    for m in messages:
        role = m.get("role") or ""
        text = (m.get("text") or "").strip()
        if not text or role in ("tool", "thought"):
            continue
        blocks.append(strip_inline_md(text))
    return "\n\n".join(blocks)
