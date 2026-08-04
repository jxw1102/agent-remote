"""Quick checks for the vendored markdown→Cascades-HTML renderer.

Run:  python3 tests/render_test.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.render_blocks import markdown_to_blocks, inline_to_rich  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main():
    md = ("# Title\n\nSome **bold** and `code` text.\n\n- item one\n\n"
          "```python\ndef f():\n    return 1\n```")
    blocks = markdown_to_blocks(md)
    kinds = [b["k"] for b in blocks]
    print("kinds:", kinds)
    for b in blocks:
        print("   ", b["k"], "|", b["text"][:40], "|", (b.get("rich") or "")[:80])

    check("heading block", "h" in kinds)
    check("paragraph block", "p" in kinds)
    check("list block", "li" in kinds)
    check("code block", "code" in kinds)
    h = [b for b in blocks if b["k"] == "h"][0]
    check("heading is purple bold html", "<font" in h["rich"] and "<b>" in h["rich"])
    p = [b for b in blocks if b["k"] == "p"][0]
    check("bold survives in rich", "<b>" in p["rich"], p["rich"])
    check("inline code colored", 'color="#67e8f9"' in p["rich"], p["rich"])
    code = [b for b in blocks if b["k"] == "code"][0]
    check("code block colored", "<font" in code["rich"], code["rich"][:80])

    plain, rich = inline_to_rich("plain text only")
    check("plain text passthrough", plain == "plain text only" and rich == plain)

    if failures:
        print("\n%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
