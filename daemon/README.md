# agentremoted — one daemon, every harness

Python 3, **stdlib only**. Serves agent sessions to Agent Remote clients
(BlackBerry, Android, web) over a token-authenticated HTTP+JSON API and
runs turns via each harness CLI.

**Preferred setup:** one process with

```json
{ "providers": ["claude", "grok", "codex", "cursor"], "port": 8473 }
```

Clients use **one profile** for that host; new session picks the harness.

## Versioning

Bump `agentremoted/__init__.py` → `__version__` **once per shippable change**
(semver patch for fixes, minor for API/cap changes). Do not micro-bump every
intermediate edit in a single feature. `/api/ping` reports the version so you
can see whether a host picked up a deploy.

**2.14.2** Large downloads. Drop downloads were capped at 64 MB
(`max_drop_mb`) because the daemon read the whole file into memory before
sending it, and every client held the whole reply in memory too. The body now
streams from disk in 256 KB pieces, the cap defaults to none (`0`; a positive
`max_drop_mb` still caps), and a file honours `Range` with `206` plus an
`ETag` checked through `If-Range`, so a client that loses the link resumes
where it stopped. Ping advertises `drop_range: true` and `max_drop_mb`. A
zipped folder is rebuilt per request, so it is not resumable
(`Accept-Ranges: none`). Web, Android 1.5.2 and BlackBerry 3.2.11 write to
disk as bytes arrive, use an idle timeout instead of a total one, and resume.

**2.14.1** Large attachments. The upload cap goes from 16 MB to 256 MB
(`max_upload_mb`, advertised on `/api/ping`), and chunked uploads are
assembled by streaming the parts to disk instead of joining them in memory,
so a big file is safe on a small VPS. Android never managed a chunked upload
at all: it treated the daemon's "chunk received, more to come" reply (no
`path` yet) as a failure, so every file over one 512 KB chunk died after the
first chunk. Android 1.5.1 accepts that reply; BlackBerry now uses the
daemon's advertised cap instead of a hardcoded 16 MB and reads each chunk
from the file as it is sent.

**2.14.0** GitHub Copilot (`copilot`) as a sixth harness. Add `"copilot"` to
`providers`. Sessions are read from `~/.copilot/session-state/<id>/`
(`workspace.yaml` for cwd, name and timestamps; `events.jsonl` for the
conversation and its tool calls, which also drive process view). Headless
turns run `copilot -p … --output-format json --allow-all-tools`; a new turn
passes `--session-id <uuid>` so the daemon knows the id before the CLI starts,
a continuation passes `--resume <uuid>`. The model picker offers `auto` plus
every model this host's own Copilot history has used, and effort maps to
`--reasoning-effort`. Interactive mode drives the real TUI in tmux (`cop-*`,
`copilot-tuis.json`), answers the one-time folder-trust dialog, and ends a
turn when an assistant message with no tool requests is followed by
`assistant.turn_end`; Live TUI works unchanged. `/rewind N` cuts
`events.jsonl` at the Nth-last prompt (verified: a resumed session forgets the
dropped turn). Usage is the seat's premium-request allowance from
`api.github.com/copilot_internal/user`, read with the CLI's own login token.
New config keys: `copilot_bin`, `copilot_home`, `copilot_flags`,
`copilot_env`. Covered by `tests/copilot_test.py`.

**2.13.0** The Live TUI takes a whole keyboard. `POST …/tui/keys` used to
understand one fixed list (Esc / Enter / Tab / arrows / a few Ctrl letters),
so a client had no way to send the chords a terminal agent is actually driven
by. Key names now compose: `Shift+Tab` (→ tmux `BTab`, the permission-mode
cycle), `Ctrl+O`, `Alt+b`, `Ctrl+Alt+k`, `Shift+Up`, `F1`–`F12`, `Ctrl+Space`,
`Ctrl+_`, and the tmux dash spellings (`C-o`, `M-x`). Shift on a letter is the
capital, and `Shift+Enter` is sent as ESC CR — the same sequence
`claude /terminal-setup` binds it to — so it makes a newline instead of
submitting. Everything the old clients send still maps. `/api/ping` advertises
`tui_keys: 2` (also inside `caps`) so a client can tell before it binds.

The host pane is **120 columns**, not 220: nothing rendered 220 comfortably,
and reflowing it into a phone-width pane turned the box drawing into confetti.
`GET …/tui` now reports the real `cols`, so a client can size its font to fit
the grid instead of guessing, and the blank rows below the last drawn line are
trimmed — a 15-row conversation in a 50-row window no longer arrives with 35
empty rows for every client to scroll past. Covered by `tests/tui_keys_test.py`.
Existing TUIs keep the width they were launched with; new ones get 120.

**2.12.2** HTTPS no longer stops the daemon dead. The listening socket was
TLS-wrapped, so every handshake ran inside `serve_forever`'s thread with no
timeout: a port scanner that connected and never finished its ClientHello
parked the accept loop forever, the listen queue filled, and the host went
dark while systemd still called it active (public host, 2026-09-23 — nothing
served for three hours; py-spy found MainThread in `ssl.do_handshake` under
`get_request`). The socket stays plain now; each connection is wrapped with
the handshake deferred and performed in its own worker thread under a 15 s
timeout. Dropped-connection tracebacks (resets, timeouts, bad handshakes) log
at debug instead of a stack trace apiece. Covered by `tests/tls_accept_test.py`.

**2.12.1** Usage buckets may carry `"show_bar": false`. Cursor Enterprise
seats report spend for the billing cycle, not a share of a limit, so the
bucket has no meaningful percentage; the daemon marks it and every client
(web, Android, BlackBerry, iOS, LILYGO) renders the row without a bar or a
"0%" label. Absent means true, so older clients keep drawing bars.

**2.12.0** Cursor Live TUI. Interactive turns now run the real Cursor Agent
terminal in a detached tmux session, with preallocated chat IDs, restart
adoption, transcript-driven completion, mid-turn input, ANSI/plain pane
capture, direct key injection, model selection, guest isolation, and idle
fleet eviction. Requires `tmux`; otherwise Cursor remains headless-only.
**2.11.0** Cursor rewind and usage. `/rewind N` moves the chat's
content-addressed `store.db` root to the last complete snapshot before the
dropped prompt, trims the display transcript, and keeps `.rewind-bak`
backups; files changed by the agent are not restored. `/api/usage` reads the
local Cursor login token (macOS Keychain, Linux Secret Service, IDE state DB,
or `CURSOR_ACCESS_TOKEN`) and maps Cursor's dashboard plan/spend data into the
shared Usage sheet.
**2.10.0** Cursor Agent (`cursor-agent`) as a fifth harness: headless
`--print --output-format stream-json`, sessions from `~/.cursor/chats` +
agent-transcripts. Add `"cursor"` to `providers`. No Live TUI. Phone turns
pass `--force --trust`.
**2.9.2** Inbox: BB10 Qt 4.8 `QUrl(QString)` re-encodes an already
percent-encoded path (`%E6` → `%25E6`), so a Chinese filename 404'd. The
daemon now unquotes until stable (still basename-confined), and the BB10
client builds drop URLs with `QUrl::fromEncoded`.
**2.9.1** Inbox download of non-ASCII filenames. `X-Drop-Name` is an HTTP/1
header (latin-1), so a Chinese name used to 500 the whole transfer. The header
is now percent-encoded UTF-8 when needed, and `Content-Disposition` carries
RFC 5987 `filename*`. Android, BlackBerry, and iOS unquote it so the saved
file keeps the original name.
**2.9.0** Permission dialogs reach the clients. Interactive mode runs the
TUI with `--permission-mode bypassPermissions`, which was assumed to remove
prompts; it does not. A `Read()` deny rule arms the static Bash guards
(`cd-compound-read` / `-write` / `-redirect`), and commands whose arguments
cannot be analysed statically ask regardless, so a turn could sit blocked on
a Yes/No with nobody at the pane. There is no hook for these, so the pane is
polled ~1/s while a turn runs, parsed into a question payload
(`_permission_panel`) and published on the existing `pending_question`
channel — so the web, Android and BlackBerry clients show it with no client
change — then the pick is typed back with Down/Enter. Two guards matter: a
dialog with output below it is treated as already answered (typing there
would hit a live prompt), and an AskUserQuestion panel is skipped so the two
handlers never race for the same keystrokes. Cancel, timeout and an
unrecognised answer all send Escape, declining that one tool call instead of
wedging the TUI. Covered by `tests/permission_panel_test.py`.

**2.8.13** Guest Live TUI actually starts. Headless grok was dying with
`Operation not permitted (os error 1)` because `--cwd` canonicalizes every
parent of the guest folder (blocked `/Users/<host>`); guest jobs now rely on
Popen cwd, matching the Interactive launch. Claude guest TUI no longer waits
only on SessionStart: seed a filtered `$HOME/.claude.json` (onboarding +
oauth, never the host `projects` map) so the first-run theme/login wizard
does not block the pane; if it still appears, dismiss it and treat the idle
footer as ready. Guest tmux panes are never evicted to make room for the
host account's fleet cap.

**2.8.12** Guest HOME stubs for host-secret dirs (`.ssh`, `.Trash`, `.azure`,
`.android`, `.V2rayU`, plus `.aws` / `.gnupg` / `.kube`): empty folders in
the sandbox, never a copy of the host tree. Apps that look in ``~`` hit
the guest HOME; the jail still blocks `/Users/…/<dir>`.

**2.8.11** Guest HOME gets an empty `~/.ssh` (mode 0700). Host keys are
never copied; host `~/.ssh` stays outside the jail.

**2.8.10** Guest Claude TUI: copy `tui-plugin/agentremote-bridge` into
`<guest>/.agentremote/tui-plugin/` and pass that as `--plugin-dir`. The
host plugin sits under `~/Developer`, which deny-default `sandbox-exec`
cannot read, so SessionStart never fired and the TUI never became ready.

**2.8.9** Guest Grok TUI starts again: do not nest `grok --sandbox workspace`
inside `sandbox-exec` (canonicalize of the guest root gets EPERM and grok
refuses to launch). The daemon jail is the filesystem confinement;
`GROK_SANDBOX=off` / `--sandbox off` so a host env cannot re-enable it.

**2.8.8** Guest macOS confinement is deny-default `sandbox-exec` (imports
Apple `system.sb` so dyld/mach still work), not allow-default plus a
folder denylist. `!` / `POST /api/shell` and harness children all run
inside that jail; a missing profile fails closed instead of `cd`-only.
Guest `trusted_folders.toml` is rewritten to the guest root only (no
longer a copy of the operator's Developer trees).

**2.8.7** Guest macOS seatbelt no longer leaves the rest of `$HOME` readable:
`!` / `POST /api/shell` and sandboxed harnesses cannot list `~/.ssh` or write
outside the guest folder. The profile denies `/Users` and `/Volumes` except
the guest root (plus read-only harness install trees). Guest roots equal to
the host home are rejected.

**2.8.6** Chunked attachments: `POST /api/attachments?name=&upload_id=&index=&total=`
assembles a file from short POSTs so a Cloudflare ~100s HTTP timeout no
longer kills an 11 MB upload as a browser "Network error during upload".
Ping advertises `"chunked_upload": true` and `max_upload_mb`. Truncated
uploads no longer 500 with BrokenPipe after the tunnel cancels.

**2.8.5** Guest Grok sessions: `GET/POST /api/sessions/job:<jobid>` (and
`…/messages`, `…/continue`) resolve to the harness uuid once the job knows
it, so a client that stays on the new-session placeholder no longer 404s
"session not found" after the turn. Grok session discovery reads the guest
``<root>/.grok`` store (not the host ``~/.grok``) when ``--session-id`` is
ignored inside the sandbox.

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
| `copilot` | `~/.copilot/session-state/<id>/events.jsonl` | `copilot -p` (headless) or interactive TUI |

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
{ "providers": ["claude", "grok", "codex", "cursor"], "port": 8473 }
```

- One client profile → host **root**
- `GET /api/ping` → `multi: true` when more than one provider, plus
  `providers` and per-harness `provider_details`
- Sessions merged; each row has `provider`
- `POST /api/sessions/new` requires `"provider": "claude"|"grok"|"codex"|"deepseek"|"cursor"|"copilot"`

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

The frame carries `cols` — the host pane width (120) — so a client can size
its font to the grid rather than reflowing it, and blank rows below the last
drawn line are already trimmed.

`keys` takes names, not bytes, and they compose:

| Form | Examples | tmux |
|------|----------|------|
| named key | `Escape` `Enter` `Tab` `Up` `PageUp` `Delete` `Space` `F7` | `Escape` `Enter` `Tab` `Up` `PPage` `DC` `Space` `F7` |
| Ctrl | `Ctrl+O` `Ctrl+C` `Ctrl+Left` `Ctrl+_` | `C-o` `C-c` `C-Left` `C-_` |
| Alt / Option | `Alt+b` `Option+Enter` | `M-b` `M-Enter` |
| Shift | `Shift+Tab` `Shift+Up` `Shift+g` | `BTab` `S-Up` `G` |
| combined | `Ctrl+Alt+k` `Ctrl+F12` | `C-M-k` `C-F12` |
| tmux spelling | `C-o` `M-x` | as given |

Shift on a letter is simply the capital, and `Shift+Enter` is sent as ESC CR
(what `claude /terminal-setup` binds it to) so it makes a newline instead of
submitting the prompt. Unknown names are dropped, never guessed. `text` is
always literal — that is the path for IME input, which no key name can carry.
`/api/ping` reports `tui_keys: 2` for this vocabulary; 1 (or absent) means the
old fixed list.

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
* copilot — `events.jsonl` is cut at the Nth-last `user.message`.

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

`GET /api/drop/<file>` streams with a `Content-Length` and accepts one
`Range: bytes=N-` (or `N-M`); send the `ETag` from the first reply back as
`If-Range` so a file replaced on the host restarts from zero (`200`) instead
of splicing two versions. `X-Drop-Size` is always the whole entry's size.
No size cap unless `"max_drop_mb"` is set above 0.

Auth: `X-Auth-Token` / `Authorization: Bearer` / `?token=`.

## Tests

```bash
cd daemon
python3 tests/smoke_test.py
python3 tests/cursor_test.py
python3 tests/copilot_test.py
python3 tests/render_test.py
python3 tests/focus_test.py        # focus state machine (unit)
python3 tests/focus_api_test.py    # focus over HTTP, incl. enrolment rules
```
