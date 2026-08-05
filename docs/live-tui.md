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
| Web | Chat header icon | Mono pane + bar | Pane focus = keys; line box; Esc bar |
| Android | Transcript ⋮ → Live TUI | Full screen | Soft keys + line box |
| BB10 | Bezel menu **Live TUI** (Interactive mode only; Headless keeps **Queue**) | Sheet, mono pane | Soft keys + line box |

## Notes

- Requires **Interactive** execution so a tmux TUI exists for the session.
- Poll ~400 ms while open; skip redraw when `seq` unchanged.
- Double **Esc** on web releases keyboard capture from the pane.
