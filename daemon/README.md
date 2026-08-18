# agentremoted — one daemon, every harness

Python 3, **stdlib only**. Serves agent sessions to Agent Remote clients
(BlackBerry, Android, web) over a token-authenticated HTTP+JSON API and
runs turns via each harness CLI.

**Preferred setup:** one process with

```json
{ "providers": ["claude", "grok", "codex"], "port": 8473 }
```

Clients use **one profile** for that host; new session picks the harness.

## Versioning

Bump `agentremoted/__init__.py` → `__version__` **once per shippable change**
(semver patch for fixes, minor for API/cap changes). Do not micro-bump every
intermediate edit in a single feature. `/api/ping` reports the version so you
can see whether a host picked up a deploy.

**2.8.2** DeepSeek no longer requires you to start `dsh web` yourself: if the
configured loopback URL (`dsh_url`, default `http://127.0.0.1:3080`) already
answers `session.list`, the daemon adopts it; otherwise it starts
`dsh web --host 127.0.0.1 --port …` (log `~/.agentremoted/dsh-web.log`, pid
file so a daemon restart re-attaches). A remote `dsh_url` is adopt-only.
Set `"dsh_manage": false` to keep the old "start it yourself" behaviour.

**2.8.1** DeepSeek transcript + process view: dsh injects `<system-reminder>` /
`<task-notification>` / `<monitor-event>` blocks as user-role history events
the human never typed — the DeepSeek provider now strips them (matching the
Claude / Grok providers) so the transcript, previews, and share links only
show real user text. Process view (`?detail=steps`) is now wired to dsh's real
content-block model: tool calls live inside `assistant/message` blocks and
results arrive as `tool/result` events, so the phone shows tool_use /
tool_result / thinking rows (canonical `steps` format) and can expand truncated
bodies via `GET /api/sessions/<id>/steps/<ref>`. The live transcript stream
shows only user-visible text — dsh `reasoning` blocks no longer leak into
normal view (they stay thinking steps in process view). Sending a new prompt
while a DeepSeek turn is running now queues it behind that job instead of
starting a second concurrent dsh turn: `/api/sessions/<id>/continue` routes
into the in-flight job's queue (`running_for_session`), so a follow-up prompt
is no longer dropped with `agent-busy`.
**2.8.0** DeepSeek Harness (`dsh web`) as a fourth provider: the daemon is a
localhost client of `http://127.0.0.1:3080/api` (list / history / prompt /
cancel / models). Add `"deepseek"` to `providers`. Do not expose :3080. No
TUI / Live TUI. From 2.8.2 the daemon starts `dsh web` when it is not
already running.
**2.7.0** session share: `POST /api/sessions/<id>/share` mints a 7-day
read-only token; `GET /share/<token>` is a hosted transcript viewer (same
look as the web client) and `GET /api/share/<token>` returns that session
only. Ping advertises `"share": true`. The token is not the daemon auth
token and cannot be pointed at another session. LILYGO does not mint links.
**2.6.5** drop Inbox: skip macOS `~/Public/Drop Box` in listings; allow
recursive folder delete via `POST /api/drop/<name>/delete` (still confined to
the drop dir; Drop Box and the drop root itself are protected).
**2.6.4** process-view steps carry an optional `lang` (from file path or
body kind: `diff` / `bash` / `python` / …) so clients can syntax-highlight
Read/Write bodies and Edit diffs. Web process view uses the same tokeniser
as fenced markdown blocks.
**2.6.3** process view (`?detail=steps`) smart-formats tool bodies on the
daemon: Bash shows description + command, Edit/search_replace shows a
unified diff, Write shows path + body; results unwrap Grok
SearchReplace/Bash/ListDir envelopes to the success line or shell output
instead of dumping JSON. Unknown shapes still pretty-print. Claude, Grok
and Codex all use `agentremoted.steps.format_tool_use` /
`format_tool_result` (window + expand endpoint).
**2.6.2** live-status `tool_detail` / `phase_detail` prefer a tool's human
`description` (Bash, `run_terminal_command`, …) over the raw `command`, so
the banner stays short; falls back to command/path when description is
absent. Same key order on Claude, Grok, and Codex detail helpers.
**2.6.1** interactive TUIs move to a private tmux socket named after
`$AGENTREMOTED_HOME` (`config.tmux_socket`), the `_adopt_or_reap` reapers no
longer kill unrecognised sessions, and tmux-server creation is serialised —
three causes of `claude TUI exited mid-turn`. Adds `$AGENTREMOTED_HOME/tmux`,
a generated wrapper for reaching the fleet (`~/.agentremoted/tmux ls`).
Drop folders: `/api/drop` lists directories (`type`, `entries`) and
`GET /api/drop/<name>` zips one on the fly (temp archive, deleted after the
transfer). Process view: `?detail=steps` on
`/api/sessions/<id>/messages` attaches tool calls, results and thinking to
the messages they happened between, with `GET /api/sessions/<id>/steps/<ref>`
for the full text behind a truncated one — Claude, Grok and Codex alike
(`agentremoted/steps.py` owns the shape). Without `detail=steps` the
response is unchanged.
**2.6.0** optional scoped tokens via `$AGENTREMOTED_HOME/guests.json` (folder
isolation + harness allow-list), plus Focus: `/api/focus`, focus membership +
state tags on session rows, session rename / retitle, and `"focus": true` on
`/api/ping`. **2.5.3** adds `auth` on `/api/ping`.

| provider | sessions from | runs turns with |
|----------|---------------|-----------------|
| `claude` | `~/.claude/projects/**/*.jsonl` | `claude` (headless or interactive TUI) |
| `grok`   | `~/.grok/sessions/<group>/<id>/` | `grok` (headless or interactive TUI) |
| `codex`  | `~/.codex` state / rollouts | `codex exec` / interactive TUI |
| `deepseek` | `dsh web` `/api` on localhost (adopted or daemon-started) | `session.prompt` / `session.cancel` |

Everything CLI-specific lives in `agentremoted/providers/`. Queue, stop,
permission bridge, and status streams are shared.

---

## Launch the daemon

### Prerequisites

- Python 3 on `PATH`
- Harness CLIs for the entries in `"providers"` on `PATH` for the same user
  that runs the daemon

### Config and token

Default home: **`~/.agentremoted`** (override with env `AGENTREMOTED_HOME`).

```bash
mkdir -p ~/.agentremoted
cp ../deploy/config.example.json ~/.agentremoted/config.json
# Trim "providers" to the CLIs you have; keep the multi shape.
```

On first run the daemon creates **`~/.agentremoted/token`**. Show it:

```bash
cd /path/to/bb10-remote/daemon
PYTHONPATH=. python3 -m agentremoted --print-token
```

Clients send that value as `X-Auth-Token`, `Authorization: Bearer …`, or
`?token=`.

### Foreground (macOS, Linux, anywhere)

```bash
cd /path/to/bb10-remote/daemon
PYTHONPATH=. python3 -m agentremoted
```

```bash
curl -s "http://127.0.0.1:8473/api/ping"
```

---

### macOS — launchd (recommended)

Script: [`scripts/install-launchd.sh`](scripts/install-launchd.sh)

```bash
cd /path/to/bb10-remote/daemon
./scripts/install-launchd.sh          # install / update + start
./scripts/install-launchd.sh --remove # stop and uninstall
```

| | Path / value |
|--|--|
| Client URL | `http://127.0.0.1:8473` |
| Token | `~/.agentremoted/token` |
| Config | `~/.agentremoted/config.json` |
| Log | `~/.agentremoted/daemon.log` |
| Label | `com.agentremoted` |

```bash
curl -s http://127.0.0.1:8473/api/ping
tail -f ~/.agentremoted/daemon.log
launchctl print "gui/$(id -u)/com.agentremoted"
launchctl kickstart -k "gui/$(id -u)/com.agentremoted"
```

---

### Linux — systemd

#### A) Deploy script

From the repo root:

```bash
./deploy/deploy.sh user@your-host
# KEY=~/.ssh/your_key PORT=8473 PROVIDERS=claude,grok,codex ./deploy/deploy.sh user@host
# Only Grok on a VPS:  PROVIDERS=grok PORT=2096 ./deploy/deploy.sh user@host
```

Copies `daemon/` + units to **`/opt/bb10-remote`**, writes multi-shaped
`/root/.agentremoted/config.json`, enables **`agentremoted`**.

```bash
systemctl status agentremoted
journalctl -u agentremoted -f
cat /root/.agentremoted/token
curl -s http://127.0.0.1:8473/api/ping
```

#### B) Manual install

Stock unit ([`deploy/agentremoted.service`](../deploy/agentremoted.service)):

| Setting | Value |
|---------|--------|
| Code | `/opt/bb10-remote/daemon` |
| Home | `/root/.agentremoted` |
| Start | `/usr/bin/python3 -m agentremoted` |

```bash
sudo mkdir -p /opt/bb10-remote
sudo cp -a daemon deploy /opt/bb10-remote/

sudo mkdir -p /root/.agentremoted
sudo cp /opt/bb10-remote/deploy/config.example.json \
        /root/.agentremoted/config.json

sudo cp /opt/bb10-remote/deploy/agentremoted.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentremoted
```

**Non-root / different paths:** edit `WorkingDirectory=`,
`Environment=AGENTREMOTED_HOME=…`, `PYTHONPATH=…`, optional `User=`.

---

## Multi provider model

```json
{ "providers": ["claude", "grok", "codex"], "port": 8473 }
```

- One client profile → host **root**
- `GET /api/ping` → `multi: true` when more than one provider, plus
  `providers` and per-harness `provider_details`
- Sessions merged; each row has `provider`
- `POST /api/sessions/new` requires `"provider": "claude"|"grok"|"codex"|"deepseek"`

Path mounts (`/claude/…`, `/grok/…`) still work. `/internal/permission` and
`/internal/hook` stay unprefixed (MCP / TUI).

If `"providers"` is empty, a single `"provider"` string is still accepted as a
fallback so an empty config can start; install scripts always write
`"providers"`.

## Execution modes

| Mode | Behaviour |
|------|-----------|
| **Interactive** | Host TUI / permission callbacks; phone can Allow/Deny and answer questions |
| **Headless** | Non-interactive CLI flags (auto-approve style) |

Exact flags are per provider under `agentremoted/providers/`. Caps from
`/api/ping` tell the UI what to offer.

## API (overview)

```
GET  /api/ping                                  liveness + provider + caps + auth (no auth token)
GET  /api/usage                                 subscription usage (when supported)
GET  /api/projects
GET  /api/sessions?project=<id>&limit=<n>
GET  /api/sessions/search?q=<text>&project=&limit=
GET  /api/sessions/<id>
GET  /api/sessions/<id>/messages?offset=&limit=[&detail=steps]
GET  /api/sessions/<id>/steps/<ref>             full text behind a truncated step
POST /api/sessions/new {cwd?, prompt, provider?, permission_mode?, …}
POST /api/sessions/<id>/continue {prompt, permission_mode?, …}
GET  /api/jobs
GET  /api/jobs/<id>?since=<seq>
POST /api/jobs/<id>/queue {prompt}
POST /api/jobs/<id>/queue/<qid>/cancel
POST /api/jobs/<id>/stop
POST /api/jobs/<id>/permission {request_id, allow}
POST /api/jobs/<id>/input {prompt}              type a full line into interactive TUI
GET  /api/sessions/<id>/tui[?ansi=1]            Live TUI pane (plain default; ansi=1 for SGR)
POST /api/sessions/<id>/tui/keys {keys?,text?}  key/text injection into Live TUI
POST /api/attachments
GET  /api/focus                                 focus rows only, urgency-sorted
POST /api/focus/<key>/done                      take a row out of Focus
POST /api/focus/<key>/restore                   undo done (7-day window)
POST /api/sessions/<id>/title {title}           rename ("" restores derived name)
POST /api/sessions/<id>/title/regenerate        re-derive the title (Haiku)
GET  /api/drop                                  files and folders (type=file|dir;
                                                skips macOS "Drop Box")
GET  /api/drop/<name>                           a folder downloads as <name>.zip
POST /api/drop/<name>/delete                    file or folder (recursive)
GET  /ws/status
GET  /sse/status
```

### Focus

The projects you are actually carrying, so a session you forgot about does not
sink out of a recency-sorted list. Clients treat it as a *filter* over the one
session list — same rows, same layout, plus a state tag.

Rows on `/api/sessions` gain two fields: `focus` (is this row in Focus) and
`focus_state`, one of:

| `focus_state` | meaning | what it wants |
| --- | --- | --- |
| `needs_answer` | blocked on you: an AskUserQuestion (phase `asking`), a plan approval, or a tool permission | answer it |
| `failed` | the latest turn ended in an error | look at it |
| `working` | a turn is running | nothing |
| `turn_finished` | the turn ended cleanly | decide the next step |

Tested in that order, because the states overlap — a running turn blocked on a
question is `needs_answer`, not `working`. There is deliberately no read/unread
split: whether you have opened a finished turn is not a property of the session.
`failed` reads the in-memory job list, so it is forgotten if the job is evicted
or the daemon restarts, and the row falls back to `turn_finished`.

Membership is **opt-in by human action**: a row appears only when a turn is
started, continued, typed into, or queued *through the daemon* — every one of
those handlers sits behind the auth gate. Agent-initiated traffic arrives on
`/internal/*`, which is handled before that gate, so subagents, hook posts and
permission callbacks can never enrol anything. Marking done removes the row;
the session itself is untouched and stays in the full list.

State is never stored — it is derived per request from live job state plus the
read cursor, so a tag cannot go stale. Membership, the cursor and title
overrides live in `focus.json` beside the config, shared by every client of the
daemon so Focus looks the same on web, phone, BlackBerry and the pager.
`/api/ping` reports `"focus": true`; clients gate the mode on it.

Cap `live_tui` on `/api/ping` when the harness can host a tmux TUI. Clients
poll `GET …/tui` (~2–5 Hz; plain by default, `?ansi=1` for colour) and send
keys via `POST …/tui/keys`.

### Mid-turn resume after daemon restart

Interactive turns (tmux TUIs) survive a clean daemon restart:

1. Running job snapshots are written every few seconds to
   `~/.agentremoted/active-jobs-<provider>.json`.
2. On startup the daemon re-adopts live tmux sessions (`tuis.json` /
   `grok-tuis.json` / `codex-tuis.json`), then rehydrates those jobs and
   continues watching the same host TUI **without re-sending the prompt**.
3. Clients keep polling the same `job_id` — Live TUI and the status banner
   reconnect automatically.

Headless (`-p` / subprocess) turns cannot resume: the CLI process dies with
the old daemon. Those jobs finish as `error` with a short notice.

Use systemd `KillMode=process` (see `deploy/agentremoted.service`) so restart
does not kill the tmux server.

### Rewind

A prompt of `/rewind [N]` (N defaults to 1) never reaches the harness: the
daemon rewinds the session N user messages by editing the harness's own
session journal, then the next turn resumes from the rewound point.

* claude — the session transcript (`~/.claude/projects/…/<id>.jsonl`) is cut
  at the Nth-last human message on the active `parentUuid` branch.
* grok — the daemon appends grok's own `rewind_marker` record (the same one
  its TUI /rewind writes) to `updates.jsonl`; grok honors it on `--resume`.
* codex — the rollout JSONL is truncated at the turn boundary of the
  Nth-last `user_message`.

Conversation only: file changes on the host are never reverted. Works in
BOTH execution modes (the journals are what `--resume` replays); a live
interactive TUI for the session is killed first and the next turn respawns
it resumed. A one-deep `*.rewind-bak` copy is left next to claude/codex
files as a safety net. Advertised as the per-harness `rewind` cap and as
`/rewind` in `slash_commands`.

### Whose sessions get listed

Human-started sessions only (subagents / empty shells filtered). `&all=1`
shows everything. Lookups by id never filter.

### Host→phone file drop

Default: **`~/Public`** on macOS, **`~/.agentremoted/drop`** elsewhere
(`"drop_dir"` in config).

Auth: `X-Auth-Token` / `Authorization: Bearer` / `?token=`.

## Tests

```bash
cd daemon
python3 tests/smoke_test.py
python3 tests/render_test.py
python3 tests/focus_test.py        # focus state machine (unit)
python3 tests/focus_api_test.py    # focus over HTTP, incl. enrolment rules
```
