# Agent Remote — LILYGO T-LoRa Pager (SX1262)

Firmware client for the **LILYGO T-LoRa Pager** (`tlora_pager_sx1262` / K257): a pocket ESP32-S3 with QWERTY, 480×222 display, battery management, and audio. It talks to **agentremoted** over **Wi‑Fi** (same HTTP API as Android / BB10 / web).

LoRa is left powered down; this build is an Agent Remote remote, not a mesh node.

## Features

| Area | Support |
|------|---------|
| **Keyboard** | TCA8418 matrix + serial fallback for desk flash |
| **Display** | ST7796 480×222 via LovyanGFX |
| **Power** | BQ25896 status, backlight dim, deep-sleep after idle |
| **Network** | Wi‑Fi STA, NVS-stored SSID/password, HTTP(S) to daemon |
| **Chime** | **Essential** — Status / Done / Error / Attention (same pitches as BB10/Android), I2S + optional DRV2605 haptic |
| **Agent** | Session list, compose/send, new session, status poll + auto-chime |
| **Share** | Not on device (no URL generation). A share link minted on web/Android/BB10 still opens in a browser. |

## Hardware

- MCU: ESP32-S3 (16 MB flash, PSRAM)
- Display: ST7796 IPS 2.33″ 480×222
- Keyboard: TCA8418 QWERTY
- Audio: ES8311 I2S (+ amp enable on XL9555)
- Power: BQ25896 + fuel gauge
- Radio: SX1262 (unused in this client)

Board selection in Arduino IDE: **LilyGo-T-LoRa-Pager**, revision **Radio-SX1262**, partition **16M Flash (3M APP/9.9MB FATFS)**, USB CDC On Boot **Enabled**.

## Build & flash (PlatformIO)

```bash
cd bb10-remote/pager
# macOS: brew install platformio
pio run -e tlora_pager_sx1262
pio run -e tlora_pager_sx1262 -t upload
pio device monitor -b 115200
```

### Device dead / no boot after flash

Usually **wrong flash/PSRAM mode** or a hang in early init. The board
definition pairs `qio_qspi` memory type with a **DIO image header**
(matches the official arduino-esp32 `LilyGo-T-LoRa-Pager` entry; a QIO
header fails on some flash chips → dead after flash, no serial). Recovery:

1. Hold **BOOT**, tap **RST**, release **BOOT** (download mode — USB stays solid).
2. Erase and reflash with the fixed board definition (`qio_qspi`, 16 MB):
   ```bash
   pio run -e tlora_pager_sx1262 -t erase
   pio run -e tlora_pager_sx1262 -t upload
   ```
3. Tap **RST**. Open serial at **115200** — you should see `[boot] … ready`.

Serial lines tell you where it stopped (`boardEarlyInit` / `display` / `keyboard` / …).

### Arduino IDE alternative

1. Install ESP32 core ≥ 3.3 and [LilyGoLib](https://github.com/Xinyuan-LilyGO/LilyGoLib) + ThirdParty deps for bring-up.
2. This tree is organized for PlatformIO; you can open `src/main.cpp` as a sketch root if you copy `include/` and library deps manually.

## First-run setup (on device)

1. Flash firmware; you should hear a short **status** chime.
2. **Esc** or skip boot → **Wi‑Fi setup**: SSID → Enter → password → Enter.
3. **Daemon setup**: base URL (e.g. `http://192.168.1.10:8473`) → token from `~/.agentremoted/token`.
4. Home menu:
   - `1` Sessions  
   - `2` Compose / new session  
   - `3` Status + **chime test** (`1`–`4` play cues; `t` toggles sound)  
   - `4` Re-run setup  

Serial console also accepts typing (USB) if the matrix map needs calibration.

## Chime cues

Same family as `android/.../Chime.kt` / BB10 `chime.cpp`:

| Cue | When |
|-----|------|
| Status | Phase/tool change, message accepted |
| Done | All jobs idle after work |
| Error | API / send failure |
| Attention | Permission or question pending |

Disable with **Status → `t`** (persisted in NVS).

## Pin / keyboard map note

Pin numbers follow the public Meshtastic `tlora-pager` variant. If display SPI or key matrix is wrong on your PCB revision, edit:

- `include/board_pins.h`
- `src/hw/keyboard.cpp` (`kLower` / matrix geometry)
- `src/hw/display.cpp` (SPI pins in LovyanGFX bus config)

## API surface used

```
GET  /api/ping
GET  /api/sessions?limit=…
POST /api/sessions/new
POST /api/sessions/<id>/continue   (fallback: …/prompt)
```

Token: `Authorization: Bearer …` or `X-Auth-Token`.

## Limits (honest)

- Not a full Live TUI / multi-profile Android peer — optimized for **session list + send + chimes** on a small screen.
- Keyboard map may need tuning per factory firmware revision.
- HTTPS uses `setInsecure()` for home/LAN daemons with self-signed certs.
- LoRa / GPS / NFC are not driven (power rails left default).

## Repo layout

```
esp32/
  platformio.ini
  include/board_pins.h app_config.h
  src/main.cpp
  src/hw/     display keyboard power wifi_mgr chime
  src/net/    daemon HTTP client
  src/ui/     screens + input
  README.md
```

Part of [Agent Remote](../README.md). Client parity notes: [AGENTS.md](../AGENTS.md).
