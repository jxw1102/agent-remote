# Live TUI

See and drive the **host interactive TUI** (tmux pane for Claude / Grok / Codex)
from Agent Remote clients.

## Name

- **UI:** Live TUI  
- **Cap:** `live_tui`  
- **API:** `GET/POST /api/sessions/<id>/tui…`

## API (daemon ≥ 2.4.0)

```
GET  /api/sessions/<id>/tui
  → { attached, text, seq, job_id, error, ts, … }

POST /api/sessions/<id>/tui/keys
  { "keys": ["Escape","Enter","Up","Ctrl+C"], "text": "literal" }
  → { ok: true } | 409
```

Full-line chat still uses `POST /api/jobs/<id>/input { prompt }` (Enter included
via the interactive manager’s paste path).

## Clients

| Platform | Entry | Display | Input |
|----------|-------|---------|--------|
| Web | Chat header icon | Mono pane + **ANSI colour** | Pane focus = keys; line box; Esc bar |
| Android | Transcript ⋮ → Live TUI | Full screen + **ANSI colour** | Soft keys + line box |
| BB10 | Bezel menu **Live TUI** (Interactive only; Headless keeps **Queue**) | Sheet, mono **B&W** (SGR stripped) | Soft keys + line box |

## Notes

- Requires **Interactive** execution so a tmux TUI exists for the session.
- Poll ~400 ms while open; skip redraw when `seq` unchanged.
- Double **Esc** on web releases keyboard capture from the pane.
- Daemon ≥ **2.4.4** captures with `tmux capture-pane -e` (SGR kept). Web/Android
  render colours; BB strips escapes for a plain Label.
