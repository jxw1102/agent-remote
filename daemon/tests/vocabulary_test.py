"""The words and colours every client shares: harness palettes, session states.

Two things are protected here.

1. They do not change by accident. `fixtures/vocabulary.json` is the agreed
   vocabulary; changing a colour or a label means changing that file on
   purpose, in the same commit.
2. The clients do not drift. Web and Android read the vocabulary from the
   daemon but keep built-in values for an older daemon; those must match.
   (This is how the Android Claude heading colour came to differ.) Client
   checks are skipped when the client sources are not next to the daemon,
   as on a host that only has the daemon deployed.

Run:  python3 tests/vocabulary_test.py
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from agentremoted import focus, palette  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
WEB_APP = os.path.join(REPO, "web", "src", "app.js")
WEB_CSS = os.path.join(REPO, "web", "src", "app.css")
ANDROID = os.path.join(REPO, "android", "app", "src", "main", "java",
                       "com", "bb10d", "remote", "ui")

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main():
    with open(os.path.join(HERE, "fixtures", "vocabulary.json"),
              encoding="utf-8") as fh:
        golden = json.load(fh)

    print("daemon:")
    check("palettes match the fixture", palette.PALETTES == golden["palettes"])
    check("aliases match the fixture", palette.ALIASES == golden["aliases"])
    check("states match the fixture", palette.STATES == golden["states"])
    check("states are the focus states, in urgency order",
          [s["id"] for s in palette.STATES] == list(focus.STATES))
    check("state labels agree with the focus list's",
          {s["id"]: s["label"] for s in palette.STATES} == focus.STATE_LABELS)
    hexre = re.compile(r"^#[0-9a-f]{6}$")
    check("every colour is #rrggbb, lower case",
          all(hexre.match(v) for p in palette.PALETTES.values()
              for k, v in p.items() if k != "label")
          and all(s[k] == "" or hexre.match(s[k]) for s in palette.STATES
                  for k in ("color", "color_light")))
    check("states() hands out copies",
          palette.states() == palette.STATES
          and palette.states()[0] is not palette.STATES[0])

    if os.path.exists(WEB_APP):
        print("web:")
        src = read(WEB_APP)
        table = src[src.index("const PROVIDERS = {"):src.index("const NEUTRAL")]
        for name, pal in sorted(palette.PALETTES.items()):
            row = re.search(r"^\s*%s: \{([^}]*)\}" % re.escape(name), table, re.M)
            want = {"label": pal["label"], "accent": pal["accent"],
                    "heading": pal["heading"], "inline": pal["inline"]}
            got = dict(re.findall(r'(\w+): "([^"]*)"', row.group(1))) if row else {}
            check("built-in %s palette matches the daemon" % name, got == want,
                  "%s != %s" % (got, want))
        labels = src[src.index("const FOCUS_LABELS = {"):]
        labels = labels[:labels.index("};")]
        check("built-in state labels match the daemon",
              dict(re.findall(r'(\w+): "([^"]*)"', labels))
              == {s["id"]: s["label"] for s in palette.STATES})
        css = read(WEB_CSS)
        for state in palette.STATES:
            if not state["color"]:
                continue
            var = {"attention": "--attention", "danger": "--danger",
                   "ok": "--ok"}[state["role"]]
            m = re.search(r"%s:\s*(#[0-9a-fA-F]{6})" % re.escape(var), css)
            check("built-in %s colour matches the daemon" % var,
                  bool(m) and m.group(1).lower() == state["color"],
                  m.group(1) if m else "missing")
    else:
        print("web: sources not here, skipped")

    theme = os.path.join(ANDROID, "theme", "Theme.kt")
    if os.path.exists(theme):
        print("android:")
        src = read(theme)
        enum = src[src.index("enum class Accent("):src.index("Neutral(")]
        rows = {label: (tint.lower(), on.lower()) for tint, on, label in re.findall(
            r'\w+\(Color\(0xFF([0-9A-Fa-f]{6})\), Color\(0xFF([0-9A-Fa-f]{6})\), "([^"]+)"\)',
            enum)}
        for name, pal in sorted(palette.PALETTES.items()):
            want = (pal["accent"][1:], pal["surface"][1:])
            check("built-in %s accent and surface match the daemon" % name,
                  rows.get(pal["label"]) == want,
                  "%s != %s" % (rows.get(pal["label"]), want))
        common = read(os.path.join(ANDROID, "components", "Common.kt"))
        for state in palette.STATES:
            check('built-in label for %s is "%s"' % (state["id"], state["label"]),
                  '"%s" -> "%s"' % (state["id"], state["label"]) in common)
    else:
        print("android: sources not here, skipped")

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        for name in failures:
            print("  - " + name)
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
