# 🖨️ Bambu Lab Telegram Monitor

A Telegram bot that monitors your **Bambu Lab 3D printer** (A1 / P1 / X1 series) in real-time and sends smart notifications — including optional **Spoolman** filament inventory integration.

---

## ✨ Features

- 📡 **Real-time MQTT monitoring** — connects directly to your printer (local network or Bambu Cloud fallback)
- 📬 **Telegram notifications** — print start, progress milestones (25/50/75%), completion, failure, and low-filament alerts
- 🔍 **Live status on demand** — `/status` actively polls the printer for an exact, real-time percentage
- 📸 **Live snapshots** — `/cam` sends a real-time photo from your printer (requires HA camera entity)
- 🧵 **Spoolman integration** — automatically deducts filament usage from tracked spools on print completion
- 🗂️ **AMS slot mapping** — map AMS slots to Spoolman spool IDs via bot commands
- 🌐 **Multi-language support** — Hebrew (`he`) and English (`en`)
- 🐳 **Docker-ready** — runs as a Home Assistant add-on or standalone container

---

## 🚀 Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/raziel6453/bambu_telegram_bot.git
cd bambu_telegram_bot
```

### 2. Configure `config.yaml`

Edit `bambu_telegram_bot/config.yaml` with your credentials:

```yaml
printer_ip: "192.168.1.50"          # Your printer's local IP
printer_serial: "01P00A123456789"   # Serial number (found in Bambu Studio)
printer_password: "12345678"        # Printer access code (LAN-only mode)
bambu_username: "user@example.com"  # Bambu Cloud account (for cloud mode)
bambu_password: "yourpassword"
telegram_token: "YOUR_BOT_TOKEN"    # From @BotFather
telegram_chat_id: "YOUR_CHAT_ID"   # Your Telegram user/group ID
language: "en"                      # "en" or "he"
spoolman_url: "http://spoolman:7912" # Optional — leave blank to disable
```

### 3. Run with Docker

```bash
docker build -t bambu-monitor ./bambu_telegram_bot
docker run -d --name bambu-monitor bambu-monitor
```

### 4. Run directly (Python)

```bash
cd bambu_telegram_bot
pip install -r requirements.txt
python bambu_monitor.py
```

---

## 🏠 Home Assistant Add-on

This bot is designed as a **Home Assistant add-on**. To install it locally:

1. Go to **Settings → Add-ons → Add-on Store**
2. Click the ⋮ menu → **Repositories** → add this repo URL
3. Find **Bambu Telegram Monitor** and install it
4. Configure via the add-on's UI and start

---

## 🧵 Spoolman Integration

If you run [Spoolman](https://github.com/Donkie/Spoolman) for filament tracking:

1. Set `spoolman_url` in your config
2. Use the `/map` bot command to associate AMS slots with Spoolman spool IDs:

```
/map 1 42    # Maps AMS slot 1 → Spoolman spool ID 42
/map 2 7     # Maps AMS slot 2 → Spoolman spool ID 7
```

3. When a print completes, the bot will automatically deduct the used filament weight from the mapped spool.

---

## 🤖 Bot Commands

| Command | Description |
|---------|-------------|
| `/status` | Live printer status — polls the printer in real-time for exact percentage, ETA, and a live photo |
| `/ams` | AMS slot status (filament type, colour, remaining weight) |
| `/cam` | Live camera snapshot (requires Home Assistant camera entity) |
| `/spools` | List all spools in your Spoolman inventory |
| `/map <slot> <spool_id>` | Map AMS slot (1–4) to a Spoolman spool ID |
| `/spoolman <spool_id> <slot>` | Same as `/map` — alternative argument order |
| `/help` | Show all available commands |

---

## 📷 Camera Integration (HA Add-on only)

When running as a Home Assistant Add-on, the bot can fetch live snapshots from your printer's camera if you have the **Bambu Lab HA Integration** installed.

1. Ensure the **Bambu Lab HA Integration** is active in your Home Assistant.
2. The bot will try to auto-discover your camera entity (e.g., `camera.p1s_camera`).
3. If it fails, you can manually set the entity ID in the Add-on configuration under `ha_camera_entity`.

> **Note:** All commands are restricted to the configured `telegram_chat_id`. Messages from other users are silently ignored.

---

## 🛠️ Architecture

```
bambu_telegram_bot/
├── bambu_monitor.py   # Main bot logic (MQTT + Telegram + Spoolman)
├── config.yaml        # Add-on configuration schema & defaults
├── requirements.txt   # Python dependencies
├── run.sh             # Container entrypoint
└── Dockerfile         # Docker image definition
```

---

## 📦 Dependencies

| Package | Purpose |
|---------|---------|
| `paho-mqtt` | MQTT communication with the printer |
| `pyTelegramBotAPI` | Telegram bot framework |
| `requests` | HTTP calls to Spoolman API |
| `flask` + `flask-cors` | Internal health/status endpoint |
| `psutil` | System resource monitoring |

---

## 🔑 Getting Your Credentials

### Telegram Bot Token
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the token provided

### Telegram Chat ID
1. Start a chat with your bot
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat": {"id": ...}` in the response

### Printer Access Code
- In **Bambu Studio** → select your printer → Network → LAN-only mode access code

### Spoolman Spool IDs
- Open your Spoolman dashboard → each spool card shows its numeric ID

---

## 📄 License

MIT — feel free to use, modify, and distribute.

---

## 🙏 Acknowledgements

- [Bambu Lab](https://bambulab.com) for their excellent printers
- [Spoolman](https://github.com/Donkie/Spoolman) for open-source filament management
- [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) for the Telegram library
