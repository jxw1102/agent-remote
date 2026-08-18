#!/usr/bin/env python3
"""Bundle the web client into ONE self-contained file.

There is exactly one Agent Remote web app, and it belongs to no daemon: the
daemons are just profiles it connects to. So the build output is a single
`agent-remote.html` with the CSS and JS inlined and no external requests at
all — put it wherever you like and it is the same app with the same profiles:

    open web/dist/agent-remote.html            # straight off disk
    ./web/serve.sh                             # http://localhost:8787 (stable origin)
    <upload to any static host>                # a URL you can bookmark anywhere

Inlining is what makes the file:// case work: ES modules are blocked by the
browser over file://, so the module graph is flattened into one classic
script here instead of shipping separate files.

No dependencies, no npm — python3 build.py.
"""

import datetime
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "src"
DIST = ROOT / "dist"
OUT = DIST / "agent-remote.html"


def read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def flatten_modules(md_js: str, app_js: str, app_name: str = "app.js") -> str:
    """md.js + a page script as one classic script, each keeping its own scope.

    Concatenating the two bodies into a single scope looks simpler and is
    wrong: both modules privately declare helpers with the same names (`el`),
    which is legal in separate modules and a duplicate-declaration
    SyntaxError once merged — and a script that fails to parse does nothing at
    all. So each module gets its own IIFE and only its declared exports cross
    the boundary, exactly like the real module graph.
    """
    exported = re.findall(r"^export\s+(?:function|const|let|class)\s+(\w+)",
                          md_js, flags=re.M)
    if not exported:
        raise SystemExit("error: md.js exports nothing — check the export syntax")

    imported = re.search(r'^import\s*\{([^}]*)\}\s*from\s*"\./md\.js";\s*$',
                         app_js, flags=re.M)
    if not imported:
        raise SystemExit(f"error: {app_name} has no import from ./md.js — update the build")
    names = [n.strip() for n in imported.group(1).split(",") if n.strip()]
    missing = [n for n in names if n not in exported]
    if missing:
        raise SystemExit(f"error: {app_name} imports {missing} which md.js does not export")

    md_body = re.sub(r"^export\s+", "", md_js, flags=re.M)
    app_body = app_js.replace(imported.group(0), "")
    returns = ", ".join(exported)
    return (
        "// ---- md.js (module scope) ----\n"
        f"const __md = (function () {{\n{md_body}\nreturn {{ {returns} }};\n}})();\n"
        f"const {{ {', '.join(names)} }} = __md;\n"
        f"// ---- {app_name} (module scope) ----\n"
        f"(function () {{\n{app_body}\n}})();\n"
    )


def syntax_ok(script: str) -> bool:
    """Parse-check the bundle with node when it is available."""
    node = shutil.which("node")
    if not node:
        print("warn: node not found — skipping the bundle parse check")
        return True
    tmp = DIST / ".bundle-check.js"
    DIST.mkdir(exist_ok=True)
    tmp.write_text(script, encoding="utf-8")
    try:
        proc = subprocess.run([node, "--check", str(tmp)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            print("error: bundled script does not parse:\n" + proc.stderr.strip(),
                  file=sys.stderr)
            return False
        return True
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    if not SRC.is_dir():
        print(f"error: {SRC} not found", file=sys.stderr)
        return 2

    html = read("index.html")
    css = read("app.css")
    md_js = read("md.js")
    script = flatten_modules(md_js, read("app.js"), "app.js")

    # Inline the stylesheet and the module script.
    html = html.replace(
        '<link rel="stylesheet" href="app.css">',
        "<style>\n" + css + "\n</style>",
    )
    html = html.replace(
        '<script type="module" src="app.js"></script>',
        "<script>\n(function () {\n" + script + "\n})();\n</script>",
    )

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = html.replace(
        "<title>Agent Remote</title>",
        f"<title>Agent Remote</title>\n<!-- built {stamp} from web/src -->",
    )

    for leftover in ('href="app.css"', 'src="app.js"', 'from "./md.js"'):
        if leftover in html:
            print(f"error: {leftover} still referenced — not self-contained",
                  file=sys.stderr)
            return 3

    # A bundle that does not parse runs NOTHING, and the page still looks
    # half-plausible because the static HTML shell renders. That is far too
    # easy to ship, so the build refuses to emit one.
    if not syntax_ok(script):
        return 4

    DIST.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    size = OUT.stat().st_size
    print(f"OK: {OUT} ({size // 1024} KB, self-contained)")

    share_script = flatten_modules(md_js, read("share.js"), "share.js")
    if not syntax_ok(share_script):
        return 4
    share_html = read("share.html")
    share_html = share_html.replace(
        '<link rel="stylesheet" href="app.css">',
        "<style>\n" + css + "\n</style>",
    )
    share_html = share_html.replace(
        '<script type="module" src="share.js"></script>',
        "<script>\n(function () {\n" + share_script + "\n})();\n</script>",
    )
    share_html = share_html.replace(
        "<title>Shared session · Agent Remote</title>",
        f"<title>Shared session · Agent Remote</title>\n<!-- built {stamp} from web/src -->",
    )
    for leftover in ('href="app.css"', 'src="share.js"', 'from "./md.js"'):
        if leftover in share_html:
            print(f"error: share.html still references {leftover}",
                  file=sys.stderr)
            return 3
    share_out = DIST / "share.html"
    share_out.write_text(share_html, encoding="utf-8")
    print(f"OK: {share_out} ({share_out.stat().st_size // 1024} KB, self-contained)")

    # Packaged with the daemon so GET /share/<token> needs no extra deploy.
    daemon_static = ROOT.parent / "daemon" / "agentremoted" / "static"
    daemon_static.mkdir(parents=True, exist_ok=True)
    shutil.copy2(share_out, daemon_static / "share.html")
    print(f"OK: copied to {daemon_static / 'share.html'}")

    # Same convention as the .bar and .apk builds: land it in the pickup
    # folder so it is reachable from the phone and the drop listing.
    public = pathlib.Path.home() / "Public"
    if public.is_dir():
        shutil.copy2(OUT, public / OUT.name)
        print(f"OK: copied to {public / OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
