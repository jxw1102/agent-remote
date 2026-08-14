"""Smart process-view formatting (tool_use / tool_result bodies).

Run:  python3 tests/steps_test.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted import steps  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main():
    # --- tool_use --------------------------------------------------------
    bash = steps.format_tool_use(
        "Bash",
        {"command": "git status -sb", "description": "Check branch state"},
    )
    check("bash shows description", "Check branch state" in bash, bash)
    check("bash shows command", "git status -sb" in bash, bash)
    check("bash not raw json", not bash.strip().startswith("{"), bash)

    bash2 = steps.format_tool_use("Bash", {"command": "sleep 30"})
    check("bash without desc is command", bash2 == "sleep 30", bash2)

    edit = steps.format_tool_use("Edit", {
        "file_path": "/tmp/a.py",
        "old_string": "x = 1\n",
        "new_string": "x = 2\n",
    })
    check("edit has path", "/tmp/a.py" in edit, edit)
    check("edit is diff", ("-x = 1" in edit or "-x = 1\n" in edit
                           or any(ln.startswith("-") and "x = 1" in ln
                                  for ln in edit.splitlines())), edit)
    check("edit not escaped json", r"\n" not in edit or "---" in edit, edit)

    write = steps.format_tool_use("Write", {
        "file_path": "/tmp/b.py",
        "content": "print(1)\n",
    })
    check("write has path", "/tmp/b.py" in write, write)
    check("write has body", "print(1)" in write, write)

    read = steps.format_tool_use("Read", {
        "file_path": "/tmp/c.py", "offset": 10, "limit": 5,
    })
    check("read has path", "/tmp/c.py" in read, read)
    check("read has range", "offset=10" in read and "limit=5" in read, read)

    # --- tool_result -----------------------------------------------------
    sr = steps.format_tool_result("""{
      "type": "SearchReplace",
      "EditsApplied": {
        "old_string": "a",
        "new_string": "b",
        "tool_output_for_prompt": "The file /tmp/a.py has been updated successfully.",
        "tool_output_for_prompt_concise": "The file /tmp/a.py has been updated.",
        "absolute_path": "/tmp/a.py"
      }
    }""")
    check("searchreplace concise",
          sr == "The file /tmp/a.py has been updated.", sr)
    check("searchreplace no json brick", "EditsApplied" not in sr, sr)

    sr2 = steps.format_tool_result("""{
      "type": "SearchReplace",
      "editsApplied": {
        "tool_output_for_prompt_concise": "Updated codex.py",
        "absolute_path": "/x/codex.py"
      }
    }""")
    check("searchreplace camelCase", sr2 == "Updated codex.py", sr2)

    bash_r = steps.format_tool_result("""{
      "type": "Bash",
      "exit_code": 0,
      "output_for_prompt": "hello\\nworld",
      "output": [104, 105],
      "command": "echo hi"
    }""")
    check("bash result uses ofp", bash_r == "hello\nworld", bash_r)

    bash_fail = steps.format_tool_result("""{
      "type": "Bash",
      "exit_code": 2,
      "output_for_prompt": "nope",
      "command": "false"
    }""")
    check("bash failure shows exit", bash_fail.startswith("exit 2"), bash_fail)
    check("bash failure keeps body", "nope" in bash_fail, bash_fail)

    listdir = steps.format_tool_result("""{
      "type": "ListDir",
      "Content": {"content": "- a\\n- b\\n", "absolute_root_path": "/tmp"}
    }""")
    check("listdir unwraps content", "- a" in listdir and "Content" not in listdir,
          listdir)

    plain = steps.format_tool_result(
        "File created successfully at: /tmp/x.py")
    check("plain text passthrough",
          plain.startswith("File created successfully"), plain)

    # Unknown JSON stays pretty-printed (still readable, not invented).
    unk = steps.format_tool_result('{"foo": 1, "bar": "x"}')
    check("unknown json pretty", '"foo"' in unk and "1" in unk, unk)

    # clip still works with smart bodies
    step = steps.tool_use("r1", "", "Edit", "/tmp/a.py", edit)
    check("tool_use step has preview", bool(step.get("preview")), step)
    check("tool_use kind", step["kind"] == "tool_use", step)

    # --- language hints --------------------------------------------------
    check("lang from .py", steps.lang_from_path("/x/a.py") == "python")
    check("lang from .ts", steps.lang_from_path("foo.ts") == "typescript")
    check("lang from Dockerfile",
          steps.lang_from_path("/app/Dockerfile") == "dockerfile")
    check("lang empty for no ext", steps.lang_from_path("/tmp/foo") == "")

    edit_lang = steps.lang_for_tool_use("Edit", {
        "file_path": "/tmp/a.py", "old_string": "a", "new_string": "b",
    })
    check("edit tool_use lang is diff", edit_lang == "diff", edit_lang)

    write_lang = steps.lang_for_tool_use("Write", {
        "file_path": "/tmp/a.py", "content": "x=1\n",
    })
    check("write tool_use lang is python", write_lang == "python", write_lang)

    bash_lang = steps.lang_for_tool_use(
        "Bash", {"command": "ls", "description": "list"})
    check("bash tool_use lang is bash", bash_lang == "bash", bash_lang)

    read_res = steps.lang_for_tool_result(
        "Read", "/tmp/a.py", "1\tdef foo():\n2\t    return 1\n")
    check("read result lang is python", read_res == "python", read_res)

    ok_msg = steps.lang_for_tool_result(
        "Edit", "/tmp/a.py", "The file /tmp/a.py has been updated.")
    check("edit success has no lang", ok_msg == "", ok_msg)

    step_l = steps.tool_use("r2", "", "Write", "/tmp/a.py", "x", lang="python")
    check("lang field on tool_use", step_l.get("lang") == "python", step_l)

    if failures:
        print("FAILED:", ", ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
