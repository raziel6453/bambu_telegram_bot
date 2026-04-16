# 🖨️ Bambu Lab Telegram Monitor

A Telegram bot that monitors your **Bambu Lab 3D printer** (A1 / P1 / X1 series) in real-time and sends smart notifications — including live **camera snapshots**, remote print control, **Spoolman** filament inventory integration, and a full print history log.

---

## ✨ Features

- 📡 **Bulletproof MQTT** — Prioritizes local network connections with automatic failover to Bambu Cloud and auto-reconnects.
- 💾 **State Persistence** — Survives Add-on restarts. Recovers mid-print state silently without duplicate start alerts.
- 📸 **Camera snapshots** — included on print start, progress milestones (25/50/75%), and completion.
- 📬 **Smart notifications** — print start, progress, completion, failure, low-filament, and new spool detection.
- 🎛️ **Remote control** — pause, resume, and cancel prints directly from Telegram.
- 📋 **Print history** — every completed print is logged; view the last 10 with `/history`.
- 🧵 **Spoolman integration** — auto-deducts usage on completion, alerts when a spool runs low, and creates new spools on demand.
- 🆕 **AMS filament detection** — notifies you when a new spool is loaded and helps register it in Spoolman.
- 🔍 **Live status** — `/status` polls in real-time with weight, Spoolman stock, accurate ETAs (Jerusalem TZ), and a snapshot.
- ⚖️ **Auto HA Discovery** — auto-detects Home Assistant weight sensor, camera, and light entities dynamically.
- 💡 **Light control** — `/light` toggles your printer lamp (via HA integration).
- 🌐 **Multi-language** — Hebrew (`he`) and English (`en`).
- 🐳 **Docker-ready** — runs seamlessly as a Home Assistant add-on or standalone Python script.

---

## 🚀 Installation & Setup

### 🏠 Home Assistant Add-on (Recommended)

1. Go to **Settings → Add-ons → Add-on Store**.
2. Click the ⋮ menu (top right) → **Repositories**.
3. Add: `https://github.com/raziel6453/bambu_telegram_bot`
4. Find **Bambu Telegram Monitor** and click **Install**.
5. Go to the **Configuration** tab, fill in your details, and click **Start**.

> **Tip:** After any update from GitHub, use **Add-on Store → ⋮ → Check for updates**, then update the add-on to pull the latest code.

---

### 💻 Standalone Installation

1. **Clone the repo**:
   ```bash
   git clone https://github.com/raziel6453/bambu_telegram_bot.git
   cd bambu_telegram_bot
   ```

2. **Configure**: Edit `options:` in `bambu_telegram_bot/config.yaml` or create `options.json`.

3. **Run with Docker**:
   ```bash
   docker build -t bambu-monitor ./bambu_telegram_bot
   docker run -d --name bambu-monitor bambu-monitor
   ```

4. **Run with Python**:
   ```bash
   pip install -r requirements.txt
   python bambu_monitor.py
   ```

---

## 🤖 Bot Commands

### 📊 Status
| Command | Description |
|---------|-------------|
| `/status` | Live printer status + Spoolman remaining weight + camera snapshot |
| `/ams` | AMS slot details (type, colour, remaining %) |
| `/history` | Last 10 completed prints (date, file, duration, grams) |

### 🎥 Camera
| Command | Description |
|---------|-------------|
| `/cam` | Manual live snapshot (requires HA camera entity) |

### ⚡ Remote Control
| Command | Description |
|---------|-------------|
| `/pause` | Pause the current print |
| `/resume` | Resume a paused print |
| `/cancel` | Cancel the print — asks for confirmation first |

### 📦 Spoolman
| Command | Description |
|---------|-------------|
| `/spools` | List all spools in your Spoolman inventory |
| `/map <slot> <spool_id>` | Map an AMS slot (1–4) to an existing Spoolman spool |
| `/set <slot> <brand> <material>` | Create a new spool in Spoolman and map the slot automatically |

### 🔦 Tools
| Command | Description |
|---------|-------------|
| `/light` | Toggle printer lamp on/off (requires HA light entity) |
| `/debug` | Show raw MQTT payload and internal state for troubleshooting |
| `/help` | Show all commands |

---

## 🧵 Spoolman Integration

If you run [Spoolman](https://github.com/Donkie/Spoolman) for filament tracking:

1. Set `spoolman_url` in your config (e.g. `http://192.168.1.x:7912`)
2. Map AMS slots to spools:

```
/map 1 42    # Links AMS slot 1 → spool ID 42
/set 2 Bambu PLA  # Creates a NEW spool and links slot 2
```

3. On print completion, the bot automatically deducts usage from the mapped spool.
4. If a spool drops below `low_stock_threshold` (default **100g**), you get an alert.

### New Spool Detection
When you load a new filament, the bot detects the color/type change and prompts:
```
🆕 New filament detected in Slot 1!
Color: 🔴 | Type: PLA

To register: /set 1 Bambu PLA
```

---

## 📷 Camera & Weight (HA Add-on only)

### Camera
- The bot **auto-discovers** your Bambu camera entity from Home Assistant.
- Override manually with `ha_camera_entity: "camera.my_printer"` in config.
- Every notification (start, progress, done) automatically includes a snapshot.

### Weight
- The bot **auto-discovers** the HA weight sensor for printers that don't broadcast weight via MQTT (like the A1).
- Override manually with `ha_weight_entity: "sensor.my_printer_weight"`.

> All commands are restricted to the configured `telegram_chat_id`. Messages from other users are silently ignored.

---

## ⚙️ Configuration Options

| Option | Required | Description |
|--------|----------|-------------|
| `printer_ip` | ✅ | Printer local IP address |
| `printer_serial` | ✅ | Printer serial number |
| `printer_password` | ✅ | LAN access code |
| `telegram_token` | ✅ | Telegram bot token |
| `telegram_chat_id` | ✅ | Your Telegram chat ID |
| `language` | | `he` or `en` (default: `he`) |
| `spoolman_url` | | Spoolman base URL |
| `low_stock_threshold` | | Alert below this many grams (default: `100`) |
| `ha_camera_entity` | | HA camera entity ID (auto-discovered if blank) |
| `ha_light_entity` | | HA light entity ID (auto-discovered if blank) |
| `ha_weight_entity` | | HA weight sensor entity ID (auto-discovered if blank) |
| `bambu_username` | | Bambu Cloud email (for cloud MQTT fallback) |
| `bambu_password` | | Bambu Cloud password (for cloud MQTT fallback) |

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
| `requests` | HTTP calls to Home Assistant & Spoolman API |
| `PyYAML` | Standalone config loading |

---

## 🔑 Getting Your Credentials

### Telegram Bot Token
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the token

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
