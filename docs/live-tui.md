# Live TUI

See and drive the **host interactive TUI** (tmux pane for Claude / Grok / Codex)
from Agent Remote clients.

## Name

- **UI:** Live TUI  
- **Cap:** `live_tui`  
- **API:** `GET/POST /api/sessions/<id>/tui…`

## API (daemon ≥ 2.4.0)

```
GET  /api/sessions/<id>/tui[?ansi=1]
  → { attached, text, seq, job_id, error, ansi, ts, … }

POST /api/sessions/<id>/tui/keys
  { "keys": ["Escape","Enter","Up","Ctrl+C"], "text": "literal" }
  → { ok: true } | 409
```

**Text modes** (daemon ≥ **2.4.5**):

| Query | Payload |
|-------|---------|
| *(default)* | **Plain** — no SGR; box-drawing / braille chrome → ASCII. For BB and simple clients. |
| `?ansi=1` (also `true` / `yes` / `on`) | **ANSI** — `tmux capture-pane -e` with SGR. For web / Android colour renderers. |

Full-line chat still uses `POST /api/jobs/<id>/input { prompt }` (Enter included
via the interactive manager’s paste path).

## Clients

| Platform | Entry | Display | Input |
|----------|-------|---------|--------|
| Web | Chat header icon | Mono pane + **ANSI** (`?ansi=1`) | Pane focus = keys; line box; Esc bar |
| Android | Transcript ⋮ → Live TUI | Full screen + **ANSI** (`?ansi=1`) | Soft keys + line box |
| BB10 | Bezel menu **Live TUI** (Interactive only; Headless keeps **Queue**) | Sheet, mono **plain** (default, no flag) | Soft keys + line box |

## Notes

- Requires **Interactive** execution so a tmux TUI exists for the session.
- Poll ~400 ms while open; skip redraw when `seq` unchanged.
- Double **Esc** on web releases keyboard capture from the pane.
- Colour is **opt-in** via `?ansi=1`. Default plain keeps BB Labels readable.
