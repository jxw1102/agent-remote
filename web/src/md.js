// Markdown -> DOM, deliberately incomplete.
//
// The daemon also ships pre-rendered `blocks` per message, but that HTML and
// its palette exist for BlackBerry's Cascades text engine. The browser gets
// the raw `text` and renders it here instead, so code blocks scroll and can
// be copied, tables stay tables, and links are real links.
//
// Scope is what agent output actually contains: ATX headings, fenced code,
// bullet/ordered lists, block quotes, pipe tables, thematic breaks, and
// inline bold/italic/strike/code/links/autolinks. Anything unrecognised
// degrades to a paragraph rather than vanishing.

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([A-Za-z0-9_+\-.#]*)\s*$/;
const ATX = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])\s*(?:\1\s*){2,}$/;
// GFM task items before plain bullets: "- [ ] foo" / "- [x] foo".
const TASK = /^(\s*)([-*+])\s+\[([ xX])\]\s+(.*)$/;
const BULLET = /^(\s*)([-*+])\s+(.*)$/;
const ORDERED = /^(\s*)(\d{1,3})[.)]\s+(.*)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
// Separator cell: --- / :--- / ---: / :---: (2+ dashes). Leading/trailing
// pipes optional so both `|---|---|` and `---|---` parse.
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
// Status words agents put in table cells — rendered as pills.
const STATUS_CELL = /^(pending|todo|done|completed|complete|in[\s_-]?progress|active|blocked|cancelled|canceled|failed|error|ok|pass|passed|fail|wip|open|closed)$/i;

/** Escaping is done by the DOM (textContent), never by string replacement. */
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function renderMarkdown(source) {
  const host = el("div", "md");
  if (!source) return host;
  const lines = String(source).replace(/\r\n?/g, "\n").split("\n");
  renderLines(lines, host);
  return host;
}

function renderLines(lines, host) {
  let para = [];
  const flush = () => {
    if (!para.length) return;
    const text = para.join("\n").trim();
    para = [];
    if (text) host.appendChild(inlineInto(el("p"), text));
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    const fence = FENCE.exec(line);
    if (fence) {
      flush();
      const marker = fence[1];
      const lang = (fence[2] || "").toLowerCase();
      const body = [];
      i++;
      for (; i < lines.length; i++) {
        const close = FENCE.exec(lines[i]);
        // A closing fence uses the same character and is at least as long;
        // a shorter run inside the block stays literal.
        if (close && close[1][0] === marker[0]
            && close[1].length >= marker.length && !close[2]) break;
        body.push(lines[i]);
      }
      host.appendChild(codeBlock(body.join("\n").replace(/\s+$/, ""), lang));
      continue;
    }

    if (!line.trim()) { flush(); continue; }

    if (RULE.test(line)) { flush(); host.appendChild(el("hr")); continue; }

    const head = ATX.exec(line);
    if (head) {
      flush();
      const level = Math.min(head[1].length, 6);
      host.appendChild(inlineInto(el("h" + level), head[2]));
      continue;
    }

    if (QUOTE.test(line)) {
      flush();
      const body = [];
      for (; i < lines.length; i++) {
        const m = QUOTE.exec(lines[i]);
        if (!m) break;
        body.push(m[1]);
      }
      i--;
      const quote = el("blockquote");
      renderLines(body, quote);
      host.appendChild(quote);
      continue;
    }

    // Pipe table: a header row followed by a |---|---| separator.
    // Alignments come from the separator (:--- / ---: / :---:).
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      flush();
      const header = splitRow(line);
      const aligns = splitRow(lines[i + 1]).map(cellAlign);
      i += 2;
      const rows = [];
      for (; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) {
        // A second separator mid-stream ends the table (rare, but safer).
        if (TABLE_SEP.test(lines[i])) break;
        rows.push(splitRow(lines[i]));
      }
      i--;
      host.appendChild(table(header, rows, aligns));
      continue;
    }

    // Task list (GFM checkboxes) — grok's todo_write emits these.
    const task = TASK.exec(line);
    if (task) {
      flush();
      const list = el("ul", "task-list");
      for (; i < lines.length; i++) {
        const t = TASK.exec(lines[i]);
        if (!t) break;
        const checked = /x/i.test(t[3]);
        const body = t[4];
        const li = el("li", "task-item" + (checked ? " is-done" : ""));
        // Decorative only: the transcript is read-only, so no real <input>.
        const box = el("span", "task-check" + (checked ? " on" : ""), checked ? "✓" : "");
        box.setAttribute("aria-hidden", "true");
        li.appendChild(box);
        li.appendChild(inlineInto(el("span", "task-body"), body));
        list.appendChild(li);
      }
      i--;
      host.appendChild(list);
      continue;
    }

    const bullet = BULLET.exec(line);
    const ordered = bullet ? null : ORDERED.exec(line);
    if (bullet || ordered) {
      flush();
      const listTag = bullet ? "ul" : "ol";
      const list = el(listTag);
      if (ordered) list.start = parseInt(ordered[2], 10) || 1;
      for (; i < lines.length; i++) {
        // A task item starts a new list rather than nesting as a plain bullet.
        if (TASK.test(lines[i])) break;
        const b = BULLET.exec(lines[i]);
        const o = b ? null : ORDERED.exec(lines[i]);
        if (!b && !o) break;
        const body = [(b || o)[3]];
        // Continuation lines: indented, and not a new block of any kind.
        while (i + 1 < lines.length) {
          const next = lines[i + 1];
          if (!next.trim() || BULLET.test(next) || ORDERED.test(next)
              || TASK.test(next)
              || ATX.test(next) || FENCE.test(next) || QUOTE.test(next)) break;
          if (!/^[ \t]/.test(next)) break;
          body.push(next.trim());
          i++;
        }
        list.appendChild(inlineInto(el("li"), body.join("\n")));
      }
      i--;
      host.appendChild(list);
      continue;
    }

    para.push(line);
  }
  flush();
}

function cellAlign(sep) {
  const s = String(sep || "").trim();
  const left = s.startsWith(":");
  const right = s.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return "";
}

function table(header, rows, aligns = []) {
  const width = Math.max(header.length, ...rows.map((r) => r.length), 0);
  const pad = (cells) => {
    const out = cells.slice(0, width);
    while (out.length < width) out.push("");
    return out;
  };
  // First column is often "#" / a rank — keep it narrow via a class.
  const firstHead = String(header[0] || "").trim();
  const rankCol = !firstHead || /^(#|n|no\.?|id)$/i.test(firstHead);

  const wrap = el("div", "md-table-wrap");
  const t = el("table");
  if (rankCol) t.classList.add("has-rank");
  const thead = el("thead");
  const hrow = el("tr");
  pad(header).forEach((cell, i) => {
    const th = inlineInto(el("th"), cell);
    if (aligns[i]) th.style.textAlign = aligns[i];
    if (rankCol && i === 0) th.classList.add("col-rank");
    hrow.appendChild(th);
  });
  thead.appendChild(hrow);
  t.appendChild(thead);
  const tbody = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr");
    pad(row).forEach((cell, i) => {
      const td = tableCell(cell);
      if (aligns[i]) td.style.textAlign = aligns[i];
      if (rankCol && i === 0) td.classList.add("col-rank");
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  t.appendChild(tbody);
  wrap.appendChild(t);
  return wrap;
}

/** Status words become pills; everything else is normal inline markdown. */
function tableCell(text) {
  const plain = String(text ?? "").trim();
  if (STATUS_CELL.test(plain)) {
    const td = el("td");
    const kind = plain.toLowerCase().replace(/[\s_]+/g, "-");
    const pill = el("span", "status-pill status-" + kind, plain);
    td.appendChild(pill);
    return td;
  }
  return inlineInto(el("td"), text);
}

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells = [];
  let cur = "";
  let escaped = false;
  for (const ch of s) {
    if (escaped) { cur += ch; escaped = false; continue; }
    if (ch === "\\") { escaped = true; continue; }
    if (ch === "|") { cells.push(cur.trim()); cur = ""; continue; }
    cur += ch;
  }
  cells.push(cur.trim());
  return cells;
}

function codeBlock(code, lang) {
  const wrap = el("div", "code-wrap");
  const bar = el("div", "code-bar");
  const resolved = resolveLang(lang);
  bar.appendChild(el("span", "code-lang", resolved.label || lang || "text"));
  const copy = el("button", "code-copy", "Copy");
  copy.type = "button";
  copy.addEventListener("click", async () => {
    try {
      // Secure contexts get the real API; plain-http hosting falls back.
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(code);
      } else {
        const ta = document.createElement("textarea");
        ta.value = code;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand && document.execCommand("copy");
        ta.remove();
        if (!ok) throw new Error("clipboard unavailable");
      }
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Blocked";
    }
    setTimeout(() => { copy.textContent = "Copy"; }, 1400);
  });
  bar.appendChild(copy);
  wrap.appendChild(bar);
  const pre = el("pre");
  const codeEl = el("code", resolved.id ? "lang-" + resolved.id : null);
  highlightInto(codeEl, code, resolved.id);
  pre.appendChild(codeEl);
  wrap.appendChild(pre);
  return wrap;
}

// -- syntax highlight -----------------------------------------------------
// Self-contained tokeniser: no CDN, no npm. Covers the languages agent
// transcripts actually emit. Unknown languages stay plain monospace text.

const LANG_ALIASES = {
  js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  ts: "typescript", tsx: "typescript",
  py: "python", python3: "python", py3: "python",
  sh: "bash", shell: "bash", zsh: "bash", bash: "bash",
  yml: "yaml", yaml: "yaml",
  md: "markdown", markdown: "markdown",
  rs: "rust", go: "go", golang: "go",
  c: "c", h: "c", cpp: "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp",
  cs: "csharp", csharp: "csharp",
  rb: "ruby", java: "java", kt: "kotlin", kotlin: "kotlin",
  sql: "sql", html: "html", xml: "html", svg: "html",
  css: "css", scss: "css", less: "css",
  json: "json", jsonc: "json",
  toml: "toml", ini: "ini", conf: "ini", env: "ini",
  diff: "diff", patch: "diff",
  dockerfile: "dockerfile", docker: "dockerfile",
  make: "makefile", makefile: "makefile",
  text: "", plain: "", plaintext: "", txt: "",
};

const C_LIKE_KW = splitKw(
  "if else for while do switch case break continue return try catch finally "
  + "throw new class extends implements interface public private protected "
  + "static final abstract void const let var function async await yield "
  + "import export from default typeof instanceof in of this super "
  + "true false null undefined NaN Infinity delete void with debugger "
  + "enum package throws synchronized volatile transient native "
  + "goto struct union typedef extern register signed unsigned sizeof "
  + "namespace using template typename virtual override explicit friend "
  + "operator constexpr noexcept decltype auto constexpr "
  + "public private protected internal sealed partial readonly "
  + "get set value where select group into orderby join on equals "
  + "var fun val object data companion when is as in out typealias "
  + "suspend inline reified crossinline noinline"
);

const PYTHON_KW = splitKw(
  "False None True and as assert async await break class continue def del "
  + "elif else except finally for from global if import in is lambda "
  + "nonlocal not or pass raise return try while with yield match case "
  + "self cls"
);

const BASH_KW = splitKw(
  "if then else elif fi for while until do done case esac function select "
  + "time coproc in return exit break continue shift export local readonly "
  + "declare typeset unset eval exec source alias unalias set unset "
  + "true false"
);

const SQL_KW = splitKw(
  "select from where and or not null is in like between join left right "
  + "inner outer full cross on group by order having limit offset as "
  + "insert into values update set delete create table index view alter "
  + "drop truncate distinct count sum avg min max union all exists case "
  + "when then else end primary key foreign references constraint default "
  + "unique check cascade restrict with recursive over partition"
);

const RUST_KW = splitKw(
  "as async await break const continue crate dyn else enum extern false fn "
  + "for if impl in let loop match mod move mut pub ref return self Self "
  + "static struct super trait true type unsafe use where while yield "
  + "abstract become box do final macro override priv typeof unsized "
  + "virtual yield try"
);

const GO_KW = splitKw(
  "break case chan const continue default defer else fallthrough for func "
  + "go goto if import interface map package range return select struct "
  + "switch type var true false nil iota"
);

const RUBY_KW = splitKw(
  "BEGIN END alias and begin break case class def defined do else elsif "
  + "end ensure false for if in module next nil not or redo rescue retry "
  + "return self super then true undef unless until when while yield"
);

function splitKw(s) {
  return new Set(s.trim().split(/\s+/).filter(Boolean));
}

function resolveLang(raw) {
  const key = String(raw || "").toLowerCase().replace(/^\./, "");
  if (!key) return { id: "", label: "text" };
  const id = LANG_ALIASES[key] !== undefined ? LANG_ALIASES[key] : key;
  if (!id) return { id: "", label: key || "text" };
  return { id, label: key };
}

function highlightInto(codeEl, code, langId) {
  const text = String(code ?? "");
  if (!langId || !text) {
    codeEl.textContent = text;
    return;
  }
  if (langId === "diff") {
    highlightDiff(codeEl, text);
    return;
  }
  if (langId === "json") {
    highlightJson(codeEl, text);
    return;
  }
  const rules = rulesFor(langId);
  if (!rules) {
    codeEl.textContent = text;
    return;
  }
  appendTokens(codeEl, tokenize(text, rules));
}

function highlightDiff(host, text) {
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    if (i) host.appendChild(document.createTextNode("\n"));
    let cls = "";
    if (/^\+/.test(line) && !/^\+\+\+/.test(line)) cls = "tok-ins";
    else if (/^-/.test(line) && !/^---/.test(line)) cls = "tok-del";
    else if (/^@@/.test(line)) cls = "tok-meta";
    else if (/^(diff |index |--- |\+\+\+)/.test(line)) cls = "tok-meta";
    if (cls) {
      const s = el("span", cls);
      s.textContent = line;
      host.appendChild(s);
    } else {
      host.appendChild(document.createTextNode(line));
    }
  });
}

function highlightJson(host, text) {
  // Keys ("x":), string values, numbers, bools/null, punctuation.
  const re = /("(?:\\.|[^"\\])*")(\s*:)?|(-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b|([{}[\],])/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) host.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (m[1] !== undefined) {
      const s = el("span", m[2] !== undefined ? "tok-prop" : "tok-str");
      s.textContent = m[1];
      host.appendChild(s);
      if (m[2]) {
        const ws = m[2].slice(0, -1);
        if (ws) host.appendChild(document.createTextNode(ws));
        const c = el("span", "tok-punct");
        c.textContent = ":";
        host.appendChild(c);
      }
    } else if (m[3] !== undefined) {
      const s = el("span", "tok-num");
      s.textContent = m[3];
      host.appendChild(s);
    } else if (m[4] !== undefined) {
      const s = el("span", "tok-kw");
      s.textContent = m[4];
      host.appendChild(s);
    } else if (m[5] !== undefined) {
      const s = el("span", "tok-punct");
      s.textContent = m[5];
      host.appendChild(s);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) host.appendChild(document.createTextNode(text.slice(last)));
}

function rulesFor(langId) {
  switch (langId) {
    case "javascript":
    case "typescript":
      return {
        keywords: C_LIKE_KW,
        builtins: splitKw("console Math JSON Object Array String Number Boolean Promise Map Set Date Error fetch parseInt parseFloat isNaN"),
        lineComment: "//",
        blockComment: ["/*", "*/"],
        strings: true,
        template: true,
        regex: true,
        numbers: true,
        hashbang: true,
      };
    case "python":
      return {
        keywords: PYTHON_KW,
        builtins: splitKw("print len range open type str int float list dict set tuple bool object super property staticmethod classmethod enumerate zip map filter any all min max sum abs"),
        lineComment: "#",
        strings: true,
        triple: true,
        numbers: true,
        hashbang: true,
        decorators: true,
      };
    case "bash":
      return {
        keywords: BASH_KW,
        lineComment: "#",
        strings: true,
        numbers: true,
        hashbang: true,
        shell: true,
      };
    case "c":
    case "cpp":
    case "csharp":
    case "java":
    case "kotlin":
      return {
        keywords: C_LIKE_KW,
        lineComment: "//",
        blockComment: ["/*", "*/"],
        strings: true,
        numbers: true,
        preprocessor: true,
      };
    case "go":
      return {
        keywords: GO_KW,
        builtins: splitKw("append cap close complex copy delete imag len make new panic print println real recover true false nil"),
        lineComment: "//",
        blockComment: ["/*", "*/"],
        strings: true,
        numbers: true,
      };
    case "rust":
      return {
        keywords: RUST_KW,
        lineComment: "//",
        blockComment: ["/*", "*/"],
        strings: true,
        numbers: true,
        lifetime: true,
      };
    case "ruby":
      return {
        keywords: RUBY_KW,
        lineComment: "#",
        strings: true,
        numbers: true,
        symbols: true,
      };
    case "sql":
      return {
        keywords: SQL_KW,
        lineComment: "--",
        blockComment: ["/*", "*/"],
        strings: true,
        numbers: true,
        caseInsensitiveKw: true,
      };
    case "css":
      return {
        lineComment: null,
        blockComment: ["/*", "*/"],
        strings: true,
        numbers: true,
        css: true,
      };
    case "html":
      return {
        lineComment: null,
        blockComment: null,
        strings: true,
        html: true,
      };
    case "yaml":
      return {
        lineComment: "#",
        strings: true,
        numbers: true,
        yaml: true,
      };
    case "toml":
    case "ini":
      return {
        lineComment: "#",
        strings: true,
        numbers: true,
        ini: true,
      };
    case "dockerfile":
      return {
        keywords: splitKw("FROM RUN CMD LABEL MAINTAINER EXPOSE ENV ADD COPY ENTRYPOINT VOLUME USER WORKDIR ARG ONBUILD STOPSIGNAL HEALTHCHECK SHELL AS"),
        lineComment: "#",
        strings: true,
        numbers: true,
        caseInsensitiveKw: true,
      };
    case "makefile":
      return {
        lineComment: "#",
        strings: true,
        makefile: true,
      };
    case "markdown":
      return {
        markdown: true,
      };
    default:
      // Generic: comments-ish + strings + numbers for unknown fenced langs.
      return {
        keywords: new Set(),
        lineComment: "//",
        blockComment: ["/*", "*/"],
        strings: true,
        numbers: true,
      };
  }
}

function tokenize(src, rules) {
  const out = [];
  let i = 0;
  const n = src.length;
  const push = (type, text) => {
    if (!text) return;
    // Merge adjacent same-type tokens.
    const last = out[out.length - 1];
    if (last && last.type === type) last.text += text;
    else out.push({ type, text });
  };
  const plain = (text) => push("", text);

  while (i < n) {
    // Hashbang
    if (rules.hashbang && i === 0 && src.startsWith("#!")) {
      const end = src.indexOf("\n");
      const take = end < 0 ? src : src.slice(0, end);
      push("tok-meta", take);
      i += take.length;
      continue;
    }

    // HTML tags (before comments that look like <!--)
    if (rules.html && src[i] === "<") {
      if (src.startsWith("<!--", i)) {
        const end = src.indexOf("-->", i + 4);
        const take = end < 0 ? src.slice(i) : src.slice(i, end + 3);
        push("tok-cmt", take);
        i += take.length;
        continue;
      }
      const end = src.indexOf(">", i);
      if (end > i) {
        push("tok-tag", src.slice(i, end + 1));
        i = end + 1;
        continue;
      }
    }

    // Block comment
    if (rules.blockComment && src.startsWith(rules.blockComment[0], i)) {
      const open = rules.blockComment[0];
      const close = rules.blockComment[1];
      const end = src.indexOf(close, i + open.length);
      const take = end < 0 ? src.slice(i) : src.slice(i, end + close.length);
      push("tok-cmt", take);
      i += take.length;
      continue;
    }

    // Line comment
    if (rules.lineComment && src.startsWith(rules.lineComment, i)) {
      // SQL/YAML: "--" / "#" only when not mid-token is fine for our purposes.
      const end = src.indexOf("\n", i);
      const take = end < 0 ? src.slice(i) : src.slice(i, end);
      push("tok-cmt", take);
      i += take.length;
      continue;
    }

    // Preprocessor (#include …)
    if (rules.preprocessor && src[i] === "#" && (i === 0 || src[i - 1] === "\n")) {
      const end = src.indexOf("\n", i);
      const take = end < 0 ? src.slice(i) : src.slice(i, end);
      push("tok-meta", take);
      i += take.length;
      continue;
    }

    // Decorators (@foo)
    if (rules.decorators && src[i] === "@" && /[A-Za-z_]/.test(src[i + 1] || "")) {
      let j = i + 1;
      while (j < n && /[A-Za-z0-9_]/.test(src[j])) j++;
      push("tok-meta", src.slice(i, j));
      i = j;
      continue;
    }

    // Rust lifetimes 'a
    if (rules.lifetime && src[i] === "'" && /[A-Za-z_]/.test(src[i + 1] || "")) {
      let j = i + 1;
      while (j < n && /[A-Za-z0-9_]/.test(src[j])) j++;
      push("tok-type", src.slice(i, j));
      i = j;
      continue;
    }

    // Triple-quoted strings (python)
    if (rules.triple && (src.startsWith('"""', i) || src.startsWith("'''", i))) {
      const q = src.slice(i, i + 3);
      const end = src.indexOf(q, i + 3);
      const take = end < 0 ? src.slice(i) : src.slice(i, end + 3);
      push("tok-str", take);
      i += take.length;
      continue;
    }

    // Template strings
    if (rules.template && src[i] === "`") {
      let j = i + 1;
      while (j < n) {
        if (src[j] === "\\") { j += 2; continue; }
        if (src[j] === "`") { j++; break; }
        j++;
      }
      push("tok-str", src.slice(i, j));
      i = j;
      continue;
    }

    // Strings
    if (rules.strings && (src[i] === '"' || src[i] === "'")) {
      const q = src[i];
      let j = i + 1;
      while (j < n) {
        if (src[j] === "\\") { j += 2; continue; }
        if (src[j] === q) { j++; break; }
        if (src[j] === "\n" && !rules.triple) break; // unclosed
        j++;
      }
      push("tok-str", src.slice(i, j));
      i = j;
      continue;
    }

    // Regex literals (js/ts) — heuristic after = ( [ , : ! & | ? { ; or start
    if (rules.regex && src[i] === "/") {
      const prev = out.length ? out[out.length - 1].text : "";
      const prevCh = prev.replace(/\s+$/, "").slice(-1);
      if (!prevCh || /[=(:,[\!&|?{;]/.test(prevCh) || /\b(return|case|throw|typeof|in|of)$/.test(prev.replace(/\s+$/, ""))) {
        let j = i + 1;
        let ok = false;
        while (j < n) {
          if (src[j] === "\n") break;
          if (src[j] === "\\") { j += 2; continue; }
          if (src[j] === "[") {
            j++;
            while (j < n && src[j] !== "]") {
              if (src[j] === "\\") j++;
              j++;
            }
            j++;
            continue;
          }
          if (src[j] === "/") { j++; ok = true; break; }
          j++;
        }
        if (ok) {
          while (j < n && /[gimsuy]/.test(src[j])) j++;
          push("tok-regex", src.slice(i, j));
          i = j;
          continue;
        }
      }
    }

    // Shell variables $FOO ${FOO}
    if (rules.shell && src[i] === "$") {
      if (src[i + 1] === "{") {
        const end = src.indexOf("}", i + 2);
        if (end > 0) {
          push("tok-prop", src.slice(i, end + 1));
          i = end + 1;
          continue;
        }
      }
      if (/[A-Za-z_?]/.test(src[i + 1] || "") || /[0-9@*#\-$!]/.test(src[i + 1] || "")) {
        let j = i + 1;
        if (/[0-9@*#\-$!]/.test(src[j])) j++;
        else while (j < n && /[A-Za-z0-9_]/.test(src[j])) j++;
        push("tok-prop", src.slice(i, j));
        i = j;
        continue;
      }
    }

    // YAML keys at line start: key:
    if (rules.yaml && (i === 0 || src[i - 1] === "\n")) {
      const m = src.slice(i).match(/^(\s*)([A-Za-z0-9_.-]+)(:)(?=\s|$)/);
      if (m) {
        if (m[1]) plain(m[1]);
        push("tok-prop", m[2]);
        push("tok-punct", m[3]);
        i += m[0].length;
        continue;
      }
    }

    // INI [section] / keys
    if (rules.ini && (i === 0 || src[i - 1] === "\n")) {
      if (src[i] === "[") {
        const end = src.indexOf("]", i + 1);
        if (end > 0) {
          const nl = src.indexOf("\n", i);
          if (nl < 0 || end < nl) {
            push("tok-meta", src.slice(i, end + 1));
            i = end + 1;
            continue;
          }
        }
      }
      const m = src.slice(i).match(/^(\s*)([A-Za-z0-9_.-]+)(\s*=)/);
      if (m) {
        if (m[1]) plain(m[1]);
        push("tok-prop", m[2]);
        push("tok-punct", m[3]);
        i += m[0].length;
        continue;
      }
    }

    // Makefile targets
    if (rules.makefile && (i === 0 || src[i - 1] === "\n") && !/^\t/.test(src.slice(i))) {
      const m = src.slice(i).match(/^([A-Za-z0-9_./%-]+)(:)/);
      if (m) {
        push("tok-fn", m[1]);
        push("tok-punct", m[2]);
        i += m[0].length;
        continue;
      }
    }

    // CSS selectors-ish and properties
    if (rules.css) {
      if (src[i] === "#" || src[i] === ".") {
        let j = i + 1;
        while (j < n && /[A-Za-z0-9_-]/.test(src[j])) j++;
        if (j > i + 1) {
          push("tok-fn", src.slice(i, j));
          i = j;
          continue;
        }
      }
      const m = src.slice(i).match(/^([A-Za-z-]+)(\s*:)/);
      if (m && !/^(if|for)$/.test(m[1])) {
        push("tok-prop", m[1]);
        push("tok-punct", m[2]);
        i += m[0].length;
        continue;
      }
    }

    // Markdown headings / fences lightly
    if (rules.markdown && (i === 0 || src[i - 1] === "\n")) {
      if (src[i] === "#") {
        const end = src.indexOf("\n", i);
        const take = end < 0 ? src.slice(i) : src.slice(i, end);
        push("tok-kw", take);
        i += take.length;
        continue;
      }
      if (src.startsWith("```", i) || src.startsWith("---", i)) {
        const end = src.indexOf("\n", i);
        const take = end < 0 ? src.slice(i) : src.slice(i, end);
        push("tok-meta", take);
        i += take.length;
        continue;
      }
    }

    // Ruby symbols :foo
    if (rules.symbols && src[i] === ":" && /[A-Za-z_]/.test(src[i + 1] || "")) {
      let j = i + 1;
      while (j < n && /[A-Za-z0-9_?!]/.test(src[j])) j++;
      push("tok-str", src.slice(i, j));
      i = j;
      continue;
    }

    // Numbers
    if (rules.numbers && /[0-9]/.test(src[i]) && (i === 0 || /[^\w.]/.test(src[i - 1]))) {
      let j = i;
      if (src.startsWith("0x", i) || src.startsWith("0X", i)) {
        j += 2;
        while (j < n && /[0-9a-fA-F_]/.test(src[j])) j++;
      } else if (src.startsWith("0b", i) || src.startsWith("0B", i)) {
        j += 2;
        while (j < n && /[01_]/.test(src[j])) j++;
      } else {
        while (j < n && /[0-9_]/.test(src[j])) j++;
        if (src[j] === "." && /[0-9]/.test(src[j + 1] || "")) {
          j++;
          while (j < n && /[0-9_]/.test(src[j])) j++;
        }
        if (/[eE]/.test(src[j] || "") && /[0-9+-]/.test(src[j + 1] || "")) {
          j++;
          if (/[+-]/.test(src[j] || "")) j++;
          while (j < n && /[0-9_]/.test(src[j])) j++;
        }
      }
      push("tok-num", src.slice(i, j));
      i = j;
      continue;
    }

    // Identifiers / keywords / builtins / calls
    if (/[A-Za-z_]/.test(src[i])) {
      let j = i + 1;
      while (j < n && /[A-Za-z0-9_]/.test(src[j])) j++;
      const word = src.slice(i, j);
      const low = rules.caseInsensitiveKw ? word.toLowerCase() : word;
      let k = j;
      while (k < n && /\s/.test(src[k])) k++;
      if (rules.keywords && rules.keywords.has(low)) push("tok-kw", word);
      else if (rules.builtins && rules.builtins.has(word)) push("tok-builtin", word);
      else if (src[k] === "(") push("tok-fn", word);
      else if (/^[A-Z]/.test(word) && word.length > 1) push("tok-type", word);
      else plain(word);
      i = j;
      continue;
    }

    // Operators / punctuation — single char at a time for simplicity
    if (/[{}()[\];,.:]/.test(src[i])) {
      push("tok-punct", src[i]);
      i++;
      continue;
    }
    if (/[+\-*/%<>=!&|^~?]/.test(src[i])) {
      let j = i + 1;
      while (j < n && /[+\-*/%<>=!&|^~?]/.test(src[j]) && j - i < 3) j++;
      push("tok-op", src.slice(i, j));
      i = j;
      continue;
    }

    // Whitespace and everything else plain
    plain(src[i]);
    i++;
  }
  return out;
}

function appendTokens(host, tokens) {
  for (const t of tokens) {
    if (!t.type) {
      host.appendChild(document.createTextNode(t.text));
      continue;
    }
    const s = el("span", t.type);
    s.textContent = t.text;
    host.appendChild(s);
  }
}

// -- inline ---------------------------------------------------------------

/**
 * Inline pass. Code spans win over everything (their content is literal),
 * then links, then emphasis. Underscore emphasis only at word boundaries so
 * `snake_case_names` survive — agent output is full of them.
 */
export function inlineInto(node, text) {
  emit(String(text ?? ""), node, { b: false, i: false, s: false });
  return node;
}

function styled(style, text) {
  let node = document.createTextNode(text);
  if (style.s) { const w = el("s"); w.appendChild(node); node = w; }
  if (style.i) { const w = el("em"); w.appendChild(node); node = w; }
  if (style.b) { const w = el("strong"); w.appendChild(node); node = w; }
  return node;
}

function emit(text, host, style) {
  let buf = "";
  const flush = () => {
    if (!buf) return;
    host.appendChild(styled(style, buf));
    buf = "";
  };

  for (let i = 0; i < text.length; ) {
    const c = text[i];

    if (c === "\\" && i + 1 < text.length && !/[A-Za-z0-9]/.test(text[i + 1])) {
      buf += text[i + 1];
      i += 2;
      continue;
    }

    if (c === "`") {
      const ticks = runLength(text, i, "`");
      const close = findRun(text, i + ticks, "`", ticks);
      if (close > 0) {
        flush();
        host.appendChild(el("code", "inline", text.slice(i + ticks, close).trim()));
        i = close + ticks;
        continue;
      }
    }

    if (c === "[" || (c === "!" && text[i + 1] === "[")) {
      const image = c === "!";
      const link = matchLink(text, image ? i + 1 : i);
      if (link) {
        flush();
        const a = el("a", null, (image ? "🖼 " : "") + stripInline(link.label));
        a.href = link.href;
        a.target = "_blank";
        a.rel = "noreferrer noopener";
        host.appendChild(a);
        i = link.end;
        continue;
      }
    }

    if (c === "*" || c === "_") {
      const run = runLength(text, i, c);
      if (run >= 1 && run <= 3 && canOpen(text, i, c)) {
        const close = findEmphasisClose(text, i + run, c, run);
        if (close > 0) {
          flush();
          const next = { ...style };
          if (run === 1) next.i = true;
          else if (run === 2) next.b = true;
          else { next.b = true; next.i = true; }
          emit(text.slice(i + run, close), host, next);
          i = close + run;
          continue;
        }
      }
    }

    if (c === "~" && runLength(text, i, "~") === 2) {
      const close = findRun(text, i + 2, "~", 2);
      if (close > 0) {
        flush();
        emit(text.slice(i + 2, close), host, { ...style, s: true });
        i = close + 2;
        continue;
      }
    }

    if ((c === "h" || c === "w") && isBareUrl(text, i)) {
      const end = bareUrlEnd(text, i);
      if (end > i) {
        flush();
        const raw = text.slice(i, end);
        const a = el("a", null, raw);
        a.href = raw.startsWith("www.") ? "https://" + raw : raw;
        a.target = "_blank";
        a.rel = "noreferrer noopener";
        host.appendChild(a);
        i = end;
        continue;
      }
    }

    buf += c;
    i++;
  }
  flush();
}

function runLength(text, at, ch) {
  let n = 0;
  while (at + n < text.length && text[at + n] === ch) n++;
  return n;
}

function findRun(text, from, ch, length) {
  for (let i = from; i < text.length; ) {
    if (text[i] === ch) {
      const run = runLength(text, i, ch);
      if (run === length) return i;
      i += run;
    } else i++;
  }
  return -1;
}

function canOpen(text, at, ch) {
  if (ch === "*") return at + 1 < text.length && !/\s/.test(text[at + 1]);
  const before = at > 0 ? text[at - 1] : "";
  return (!before || !/[A-Za-z0-9]/.test(before))
      && at + 1 < text.length && !/\s/.test(text[at + 1]);
}

function findEmphasisClose(text, from, ch, length) {
  for (let i = from; i < text.length; ) {
    if (text[i] === "\\") { i += 2; continue; }
    if (text[i] === ch) {
      const run = runLength(text, i, ch);
      const prev = i > 0 ? text[i - 1] : "";
      const after = text[i + run] || "";
      const closes = run >= length && prev && !/\s/.test(prev)
          && (ch === "*" || !/[A-Za-z0-9]/.test(after));
      if (closes) return i;
      i += run;
      continue;
    }
    i++;
  }
  return -1;
}

function matchLink(text, start) {
  let depth = 0;
  let i = start;
  for (; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") { i++; continue; }
    if (c === "[") depth++;
    else if (c === "]") { depth--; if (!depth) break; }
  }
  if (i >= text.length || depth !== 0) return null;
  const label = text.slice(start + 1, i);
  if (text[i + 1] !== "(") return null;
  let j = i + 2;
  let paren = 1;
  for (; j < text.length; j++) {
    const c = text[j];
    if (c === "\\") { j++; continue; }
    if (c === "(") paren++;
    else if (c === ")") { paren--; if (!paren) break; }
  }
  if (j >= text.length) return null;
  const target = text.slice(i + 2, j).trim();
  const href = target.split(" ")[0].replace(/^<|>$/g, "");
  if (!href) return null;
  // Only navigable schemes: a javascript: URL in agent output must not
  // become a clickable link.
  if (!/^(https?:|mailto:|#|\/)/i.test(href)) return null;
  return { label, href, end: j + 1 };
}

function stripInline(label) {
  return label.replace(/[*_`~]/g, "").trim();
}

function isBareUrl(text, at) {
  if (at > 0 && /[A-Za-z0-9/]/.test(text[at - 1])) return false;
  return text.startsWith("http://", at) || text.startsWith("https://", at)
      || text.startsWith("www.", at);
}

function bareUrlEnd(text, at) {
  let end = at;
  while (end < text.length && !/[\s`<>|]/.test(text[end])) end++;
  while (end > at && ".,;:!?)]}\"'".includes(text[end - 1])) end--;
  return end - at > 8 ? end : -1;
}
