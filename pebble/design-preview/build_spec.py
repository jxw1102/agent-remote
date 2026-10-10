#!/usr/bin/env python3
"""Convert pebble/DESIGN.md into spec.html for local review."""
from __future__ import annotations

import html
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "DESIGN.md"
OUT = HERE / "spec.html"

SHORT = {
    "overview": "Overview",
    "background-motivation": "Why",
    "goals-non-goals": "Goals",
    "key-decisions": "Decisions",
    "runtime-c-not-alloy": "Runtime",
    "proposed-design": "Design",
    "api-interface-changes": "API",
    "data-model-changes": "Data",
    "appmessage-dictionary": "AppMessage",
    "poll-strategy-honest": "Poll",
    "sequence-diagrams": "Sequences",
    "pkjs-module-map": "PKJS",
    "watch-module-map": "Watch",
    "error-states": "Errors",
    "security-privacy-considerations": "Security",
    "observability": "Logs",
    "battery": "Battery",
    "rollout-plan": "Rollout",
    "client-parity-agentsmd": "Parity",
    "alternatives-considered": "Alts",
    "risks": "Risks",
    "open-questions": "Open",
    "references": "Refs",
    "pr-plan": "PRs",
}


def slug(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[`*]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def inline(text: str) -> str:
    parts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("`", i):
            j = text.find("`", i + 1)
            if j < 0:
                parts.append(html.escape(text[i:]))
                break
            parts.append("<code>" + html.escape(text[i + 1 : j]) + "</code>")
            i = j + 1
            continue
        if text.startswith("**", i):
            j = text.find("**", i + 2)
            if j < 0:
                parts.append(html.escape(text[i:]))
                break
            parts.append("<strong>" + inline(text[i + 2 : j]) + "</strong>")
            i = j + 2
            continue
        if text.startswith("[", i):
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", text[i:])
            if m:
                label, href = m.group(1), m.group(2)
                if href.endswith(".md") or href.startswith("../") or href.startswith("daemon/") or href.startswith("esp32/") or href.startswith("android/") or href.startswith("web/") or "pebble/" in href or href.startswith("AGENTS"):
                    parts.append("<code>" + html.escape(label) + "</code>")
                else:
                    parts.append(
                        f'<a href="{html.escape(href, quote=True)}">{inline(label)}</a>'
                    )
                i += m.end()
                continue
        if text[i] == "*":
            j = text.find("*", i + 1)
            if j > i + 1:
                parts.append("<em>" + inline(text[i + 1 : j]) + "</em>")
                i = j + 1
                continue
        if text.startswith("~~", i):
            j = text.find("~~", i + 2)
            if j > 0:
                parts.append("<del>" + inline(text[i + 2 : j]) + "</del>")
                i = j + 2
                continue
        j = i + 1
        while j < n and text[j] not in "`*[":
            j += 1
        parts.append(html.escape(text[i:j]))
        i = j
    return "".join(parts)


def fence_html(lang: str, code: str) -> str:
    code = code.rstrip("\n")
    if lang == "mermaid":
        return f'<pre class="mermaid">{html.escape(code)}</pre>'
    cls = f' class="lang-{html.escape(lang)}"' if lang else ""
    return f"<pre><code{cls}>{html.escape(code)}</code></pre>"


def is_table_sep(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    inner = s.strip("|")
    return bool(re.match(r"^[\s:|-]+$", inner)) and "-" in inner


def table_html(rows: list[str]) -> str:
    body: list[list[str]] = []
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        body.append(cells)
    if len(body) < 2:
        return "<p>" + inline(" ".join(rows)) + "</p>"
    head, rest = body[0], body[1:]
    if rest and all(re.match(r"^:?-+:?$", c or "-") for c in rest[0]):
        rest = rest[1:]
    out = ['<div class="table-wrap"><table><thead><tr>']
    for c in head:
        out.append(f"<th>{inline(c)}</th>")
    out.append("</tr></thead><tbody>")
    for row in rest:
        out.append("<tr>")
        for i, c in enumerate(row):
            tag = "th" if i == 0 and False else "td"
            out.append(f"<{tag}>{inline(c)}</{tag}>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def convert(md: str) -> tuple[str, list[tuple[str, str]]]:
    md = md.replace("\r\n", "\n")
    fences: list[tuple[str, str]] = []

    def grab(m: re.Match) -> str:
        fences.append((m.group(1) or "", m.group(2)))
        return f"\n\n@@@FENCE{len(fences) - 1}@@@\n\n"

    md = re.sub(r"```(\w*)\n(.*?)```", grab, md, flags=re.S)

    lines = md.split("\n")
    blocks: list[str] = []
    toc: list[tuple[str, str]] = []
    i = 0
    para: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if para:
            text = " ".join(p.strip() for p in para if p.strip())
            if text:
                blocks.append("<p>" + inline(text) + "</p>")
        para = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        mfen = re.match(r"@@@FENCE(\d+)@@@$", stripped)
        if mfen:
            flush_para()
            lang, code = fences[int(mfen.group(1))]
            blocks.append(fence_html(lang, code))
            i += 1
            continue

        if stripped == "":
            flush_para()
            i += 1
            continue

        hm = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if hm:
            flush_para()
            level = len(hm.group(1))
            title = hm.group(2).strip()
            sid = slug(title)
            if level == 1:
                blocks.append(f"<h1 id='{sid}'>{inline(title)}</h1>")
            elif level == 2:
                toc.append((sid, title))
                blocks.append(f"<h2 id='{sid}'>{inline(title)}</h2>")
            elif level == 3:
                blocks.append(f"<h3 id='{sid}'>{inline(title)}</h3>")
            else:
                blocks.append(f"<h4 id='{sid}'>{inline(title)}</h4>")
            i += 1
            continue

        if stripped in ("---", "***"):
            flush_para()
            blocks.append("<hr>")
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and is_table_sep(lines[i + 1]):
            flush_para()
            rows = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            blocks.append(table_html(rows))
            continue

        ul = re.match(r"^[-*]\s+(.*)$", stripped)
        ol = re.match(r"^\d+\.\s+(.*)$", stripped)
        if ul or ol:
            flush_para()
            ordered = bool(ol)
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < len(lines):
                s = lines[i].strip()
                um = re.match(r"^[-*]\s+(.*)$", s)
                om = re.match(r"^\d+\.\s+(.*)$", s)
                if ordered and om:
                    items.append("<li>" + inline(om.group(1)) + "</li>")
                    i += 1
                elif not ordered and um:
                    items.append("<li>" + inline(um.group(1)) + "</li>")
                    i += 1
                elif s == "":
                    break
                else:
                    break
            blocks.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(blocks), toc


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Agent Remote — Pebble Time 2 spec</title>
<style>
  :root {{
    --page: #14120e;
    --panel: #1c1914;
    --ink: #f2ead8;
    --mute: #9a8f78;
    --rule: #3a3428;
    --orange: #FF5500;
    --cerulean: #00AAFF;
    --jaeger: #00AA55;
    --yellow: #FFAA00;
    --sans: "Helvetica Neue", Helvetica, Arial, sans-serif;
    --serif: "Iowan Old Style", "Palatino Linotype", Palatino, "Times New Roman", serif;
    --mono: "SF Mono", "Menlo", "Consolas", monospace;
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  @media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior: auto; }} }}
  html, body {{ margin: 0; background: var(--page); color: var(--ink); }}
  body {{ font-family: var(--serif); }}
  .rail {{
    position: fixed; top: 0; left: 0; bottom: 0; width: 148px;
    background: #1c1a16; border-right: 1px solid var(--rule);
    overflow-y: auto; padding: 16px 10px 32px; z-index: 20;
  }}
  .rail .brand {{
    font-family: var(--sans); font-size: 11px; color: var(--yellow);
    margin: 0 0 12px; padding: 0 4px; line-height: 1.3;
  }}
  .rail a {{
    display: block; text-decoration: none; color: var(--mute);
    font-family: var(--sans); font-size: 12px; line-height: 1.25;
    padding: 6px 8px; border: 1px solid transparent;
    border-radius: 2px 8px 8px 2px; margin-bottom: 4px;
  }}
  .rail a:hover, .rail a:focus-visible {{ color: var(--ink); border-color: #5a554c; outline: none; }}
  .rail a.on {{ background: var(--orange); color: #000; border-color: var(--orange); }}
  .wrap {{ margin-left: 148px; padding: 40px 48px 96px; max-width: 920px; }}
  .topnav {{
    font-family: var(--sans); font-size: 13px; color: var(--mute); margin: 0 0 28px;
  }}
  .topnav a {{ color: var(--cerulean); text-decoration: none; }}
  .topnav a:hover {{ text-decoration: underline; }}
  h1 {{
    font-size: clamp(28px, 4vw, 44px); letter-spacing: -0.03em;
    line-height: 1.1; margin: 0 0 12px; font-weight: 700;
  }}
  h2 {{
    font-size: 26px; letter-spacing: -0.02em; margin: 48px 0 12px;
    padding-top: 12px; border-top: 1px solid var(--rule); font-weight: 700;
  }}
  h2:first-of-type {{ border-top: 0; margin-top: 8px; }}
  h3 {{ font-size: 18px; margin: 28px 0 8px; font-weight: 700; }}
  h4 {{ font-size: 16px; margin: 20px 0 8px; font-weight: 700; font-family: var(--sans); }}
  p, li {{ font-size: 17px; line-height: 1.5; color: var(--ink); }}
  p {{ margin: 0 0 12px; max-width: 42em; }}
  li {{ margin: 0 0 6px; }}
  ul, ol {{ margin: 0 0 16px; padding-left: 1.3em; max-width: 44em; }}
  hr {{ border: 0; border-top: 1px solid var(--rule); margin: 28px 0; }}
  a {{ color: var(--cerulean); }}
  code {{
    font-family: var(--mono); font-size: 0.86em;
    background: #1f1c18; padding: 0.05em 0.3em; color: #f0e6c8;
  }}
  pre {{
    background: #0e0d0b; border: 1px solid var(--rule);
    padding: 14px 16px; overflow-x: auto; font-family: var(--mono);
    font-size: 13px; line-height: 1.45; color: #e6dcc4; margin: 0 0 18px;
  }}
  pre code {{ background: none; padding: 0; font-size: inherit; color: inherit; }}
  pre.mermaid {{ background: #1a1814; text-align: center; }}
  .table-wrap {{ overflow-x: auto; margin: 0 0 18px; }}
  table {{
    border-collapse: collapse; font-family: var(--sans); font-size: 14px;
    width: 100%; min-width: 480px;
  }}
  th, td {{
    text-align: left; padding: 8px 10px;
    border-bottom: 1px solid var(--rule); vertical-align: top;
  }}
  th {{ color: var(--mute); font-weight: 500; }}
  strong {{ font-weight: 700; }}
  .lede {{ color: var(--mute); font-size: 18px; max-width: 40em; margin: 0 0 24px; }}
  .mnav {{ display: none; }}
  .mnav a {{
    flex: 0 0 auto; text-decoration: none; color: var(--ink);
    font-family: var(--sans); font-size: 13px; font-weight: 700;
    line-height: 32px; padding: 0 10px; border: 1px solid #5a554c; background: #2c2924;
  }}
  @media (max-width: 860px) {{
    .rail {{ display: none; }}
    .wrap {{ margin-left: 0; padding: 20px 18px 72px; }}
    .mnav {{
      display: flex; position: sticky; top: 0; z-index: 20;
      background: #1c1a16; border-bottom: 1px solid var(--rule);
      padding: 8px 12px; gap: 6px; overflow-x: auto;
    }}
    h2 {{ scroll-margin-top: 52px; }}
  }}
</style>
</head>
<body>
<nav class="mnav" aria-label="Jump">{mnav}</nav>
<nav class="rail" aria-label="Spec sections">
  <p class="brand">D+B · Time 2<br>PKJS · voice</p>
  <a href="index.html">Mockups</a>
  {rail}
</nav>
<div class="wrap">
  <p class="topnav"><a href="index.html">Mockups</a> · spec · emery 200×228</p>
  {body}
</div>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{
    startOnLoad: true,
    theme: "dark",
    themeVariables: {{
      darkMode: true,
      background: "#1a1814",
      primaryColor: "#2c2924",
      primaryTextColor: "#f2ead8",
      primaryBorderColor: "#6a6458",
      lineColor: "#9a8f78",
      secondaryColor: "#1c1a16",
      tertiaryColor: "#14120e",
      fontFamily: "Helvetica Neue, Helvetica, Arial, sans-serif"
    }}
  }});
</script>
<script>
(function () {{
  const ids = {ids};
  const links = Array.prototype.slice.call(document.querySelectorAll(".rail a[href^='#']"));
  function mark() {{
    const y = window.scrollY + 80;
    let cur = ids[0];
    ids.forEach(function (id) {{
      const el = document.getElementById(id);
      if (el && el.offsetTop <= y) cur = id;
    }});
    links.forEach(function (a) {{
      a.classList.toggle("on", a.getAttribute("href") === "#" + cur);
    }});
  }}
  mark();
  window.addEventListener("scroll", mark, {{ passive: true }});
}})();
</script>
</body>
</html>
"""


def main() -> None:
    md = SRC.read_text(encoding="utf-8")
    body, toc = convert(md)
    rail = []
    mnav = ['<a href="index.html">Mockups</a>']
    ids = []
    for sid, title in toc:
        label = SHORT.get(sid, title)
        if len(label) > 18:
            label = label[:16] + "…"
        rail.append(f'<a href="#{html.escape(sid)}">{html.escape(label)}</a>')
        mnav.append(f'<a href="#{html.escape(sid)}">{html.escape(label)}</a>')
        ids.append(sid)
    page = TEMPLATE.format(
        body=body,
        rail="\n  ".join(rail),
        mnav="".join(mnav),
        ids=repr(ids),
    )
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(toc)} sections)")


if __name__ == "__main__":
    main()
