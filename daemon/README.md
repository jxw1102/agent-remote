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

Bump `agentremoted/__init__.py` → `__version__` in the **same change** as any
daemon edit (semver patch for fixes/features, minor for API/cap changes).
`/api/ping` reports it so you can see whether a host picked up a deploy.

| provider | sessions from | runs turns with |
|----------|---------------|-----------------|
| `claude` | `~/.claude/projects/**/*.jsonl` | `claude` (headless or interactive TUI) |
| `grok`   | `~/.grok/sessions/<group>/<id>/` | `grok` (headless or interactive TUI) |
| `codex`  | `~/.codex` state / rollouts | `codex exec` / interactive TUI |

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
cd /path/to/agent-remote/daemon
PYTHONPATH=. python3 -m agentremoted --print-token
```

Clients send that value as `X-Auth-Token`, `Authorization: Bearer …`, or
`?token=`.

### Foreground (macOS, Linux, anywhere)

```bash
cd /path/to/agent-remote/daemon
PYTHONPATH=. python3 -m agentremoted
```

```bash
curl -s "http://127.0.0.1:8473/api/ping"
```

---

### macOS — launchd (recommended)

Script: [`scripts/install-launchd.sh`](scripts/install-launchd.sh)

```bash
cd /path/to/agent-remote/daemon
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
- `POST /api/sessions/new` requires `"provider": "claude"|"grok"|"codex"`

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
GET  /api/ping                                  liveness + provider + caps (no auth)
GET  /api/usage                                 subscription usage (when supported)
GET  /api/projects
GET  /api/sessions?project=<id>&limit=<n>
GET  /api/sessions/search?q=<text>&project=&limit=
GET  /api/sessions/<id>
GET  /api/sessions/<id>/messages?offset=&limit=
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
GET  /api/drop
GET  /api/drop/<name>
POST /api/drop/<name>/delete
GET  /ws/status
GET  /sse/status
```

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
```
