"""The Codex model picker against a synthetic ~/.codex/models_cache.json.

Codex's own catalogue says which models are choices (`visibility`: "list" vs
"hide") and in what order (`priority`). The picker on the phone must agree
with the picker in the TUI, or it offers `gpt-reserve` (a fallback) and
`codex-auto-review` (what `codex review` runs) as if they were chat models.

Run:  python3 tests/codex_models_test.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-codex-models-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.config import Config                            # noqa: E402
from agentremoted.jobs import Job                                 # noqa: E402
from agentremoted import providers as providers_mod               # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def runner_for(home, **extra):
    cfg = Config(dict({"providers": ["codex"], "codex_home": str(home)}, **extra))
    return providers_mod.build_one(cfg, "codex")[1]


def write_cache(home, models):
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / "models_cache.json").write_text(
        json.dumps({"models": models}), encoding="utf-8")


# The real file's shape, out of priority order and with both hidden kinds.
def levels(*names):
    return [{"effort": n, "description": n} for n in names]


ALL = ("low", "medium", "high", "xhigh", "max", "ultra")
NO_ULTRA = ALL[:-1]

CATALOGUE = [
    {"slug": "gpt-6-sol", "visibility": "list", "priority": 4,
     "supported_reasoning_levels": levels(*ALL)},
    {"slug": "codex-auto-review", "visibility": "hide", "priority": 43,
     "supported_reasoning_levels": levels(*NO_ULTRA)},
    {"slug": "gpt-6.1-sol", "visibility": "list", "priority": 1,
     "supported_reasoning_levels": levels(*ALL)},
    {"slug": "gpt-reserve", "visibility": "hide", "priority": 4,
     "supported_reasoning_levels": levels(*NO_ULTRA)},
    {"slug": "gpt-6-astra", "visibility": "list", "priority": 2,
     "supported_reasoning_levels": levels(*ALL)},
    {"slug": "gpt-5.6-luna", "visibility": "list", "priority": 9,
     "supported_reasoning_levels": levels(*NO_ULTRA)},
]

print("codex models()")

home = Path(FAKE_HOME) / ".codex"
write_cache(home, CATALOGUE)
got = runner_for(home).models()
check("default leads", got[:1] == ["default"], got)
check("hidden models are not offered",
      "gpt-reserve" not in got and "codex-auto-review" not in got, got)
check("listed models come in Codex's priority order",
      got == ["default", "gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol",
              "gpt-5.6-luna"], got)

got = runner_for(home, models=["house-tuned", "gpt-reserve"]).models()
check("config extras lead, and can name a hidden model",
      got[:3] == ["default", "house-tuned", "gpt-reserve"], got)
check("an extra is not repeated by the catalogue",
      got.count("gpt-reserve") == 1, got)

empty = Path(FAKE_HOME) / "no-codex-here"
check("no cache file still offers default",
      runner_for(empty).models() == ["default"], runner_for(empty).models())

broken = Path(FAKE_HOME) / ".codex-broken"
broken.mkdir(parents=True, exist_ok=True)
(broken / "models_cache.json").write_text("{not json", encoding="utf-8")
check("a corrupt cache still offers default",
      runner_for(broken).models() == ["default"], runner_for(broken).models())

bare = Path(FAKE_HOME) / ".codex-bare"
write_cache(bare, ["first-one", "second-one"])
check("a cache of plain names keeps its own order",
      runner_for(bare).models() == ["default", "first-one", "second-one"],
      runner_for(bare).models())

# A catalogue from a future CLI that stopped writing `visibility`.
noflag = Path(FAKE_HOME) / ".codex-noflag"
write_cache(noflag, [{"slug": "only-one", "priority": 3}])
check("a model with no visibility field is still offered",
      runner_for(noflag).models() == ["default", "only-one"],
      runner_for(noflag).models())

print("\ncodex efforts() and the per-model clamp")

r = runner_for(home)
got = r.efforts()
check("default leads the effort list", got[:1] == ["default"], got)
check("every level a listed model takes is offered",
      got == ["default", "low", "medium", "high", "xhigh", "max", "ultra"], got)
check("a level the model takes is sent as asked",
      r._effort_for("gpt-6.1-sol", "ultra") == "ultra")
check("a level the model does not take is clamped down",
      r._effort_for("gpt-5.6-luna", "ultra") == "max",
      r._effort_for("gpt-5.6-luna", "ultra"))
check("default sends no override", r._effort_for("gpt-6.1-sol", "default") == "")
check("empty sends no override", r._effort_for("gpt-6.1-sol", "") == "")
check("no model means codex chooses, so the level rides along",
      r._effort_for("default", "high") == "high")
check("an unknown model is trusted rather than clamped",
      r._effort_for("gpt-7-nova", "ultra") == "ultra")

no_levels = Path(FAKE_HOME) / ".codex-nolevels"
write_cache(no_levels, [{"slug": "plain", "priority": 1}])
check("a catalogue with no levels offers no effort picker",
      runner_for(no_levels).efforts() == [], runner_for(no_levels).efforts())

print("\ncodex exec argv")

job = Job("job1", "", "say hi", str(FAKE_HOME))
job.model = "gpt-5.6-luna"
job.effort = "ultra"
cmd, _env = r.prepare(job, "bypassPermissions")
flat = " ".join(cmd)
check("the turn carries the clamped level",
      "-c model_reasoning_effort=max" in flat, flat)
check("and the model it was clamped for", "-m gpt-5.6-luna" in flat, flat)

job2 = Job("job2", "", "say hi", str(FAKE_HOME))
job2.model = "default"
job2.effort = "default"
cmd2, _env2 = r.prepare(job2, "bypassPermissions")
flat2 = " ".join(cmd2)
check("default model and effort add no flags",
      "-m " not in flat2 and "model_reasoning_effort" not in flat2, flat2)

print("\n%s" % ("FAILED: " + ", ".join(failures) if failures else "all ok"))
sys.exit(1 if failures else 0)
