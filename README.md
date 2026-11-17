# 🖨️ BambuBuddy

A Telegram bot that monitors your **Bambu Lab 3D printer** (A1 / P1 / X1 series) in real-time and sends smart notifications — including live **camera snapshots**, remote print control, **Spoolman** filament inventory integration, and a full print history log.

---

## ✨ Features

- 📡 **Bulletproof MQTT** — Prioritizes local network connections with automatic failover to Bambu Cloud and auto-reconnects.
- 💾 **State Persistence** — Survives Add-on restarts. Recovers mid-print state silently without duplicate start alerts.
- 📸 **Camera snapshots** — included on print start, progress milestones (25/50/75%), and completion.
- 📬 **Smart notifications** — a short start notice, status after layers 1 and 3 finish (when layers 2 and 4 begin), then the existing 25/50/75% milestones, completion, failure, low-filament, and new spool detection. Early status notices include updated weight and time estimates and are saved across restarts. If MQTT skips both early thresholds, only one current status is sent.
- 🚨 **OctoEverywhere alerts** — optional Gadget failure warnings, intervention requests and bed-cooled notices, with duplicate filtering.
- 🎛️ **Remote control** — pause, resume, and cancel prints directly from Telegram.
- 📋 **Print history** — every completed print is logged; view the last 10 with `/history`.
- 🧵 **Spoolman integration** — auto-deducts usage on completion, alerts when a spool runs low, and creates new spools on demand.
- 🆕 **AMS filament detection** — notifies you when a new spool is loaded and helps register it in Spoolman.
- 🔍 **Live status** — `/status` polls in real-time with weight, Spoolman stock, accurate ETAs (Jerusalem TZ), and a snapshot.
- ⚖️ **Auto HA Discovery** — auto-detects Home Assistant weight sensor, camera, and light entities dynamically.
- 💡 **Light control** — `/light` toggles your printer lamp (via HA integration).
- 🌐 **Multi-language** — Hebrew (`he`) and English (`en`).
- 🕒 **Israel local time** — finish times, camera timestamps and new history entries follow `Asia/Jerusalem`, including automatic summer/winter changes. Standalone Python installations need a system timezone database or the Python `tzdata` package; Docker includes it.
- 🐳 **Docker-ready** — runs seamlessly as a Home Assistant add-on or standalone Python script.

---

## 🚀 Installation & Setup

### 🏠 Home Assistant Add-on (Recommended)

1. Go to **Settings → Add-ons → Add-on Store**.
2. Click the ⋮ menu (top right) → **Repositories**.
3. Add: `https://github.com/raziel6453/bambu_telegram_bot`
4. Find **BambuBuddy** and click **Install**.
5. Go to the **Configuration** tab, fill in your details, and click **Start**.

> **Tip:** After any update from GitHub, use **Add-on Store → ⋮ → Check for updates**, then update the add-on to pull the latest code.

---

### 💻 Standalone Installation

1. **Clone the repo**:
   ```bash
   git clone https://github.com/raziel6453/bambu_telegram_bot.git
   cd bambu_telegram_bot
   ```

2. **Choose Docker or Python below.** Copy the example configuration and replace the
   placeholders with your printer and Telegram details. Cloud credentials and
   Spoolman are optional. Camera and light features require the Home Assistant
   add-on's Supervisor API.

3. **Run with Docker** (from the repository root):
   ```bash
   mkdir -p data
   cp options.example.json data/options.json
   ```
   Edit `data/options.json`, then run:
   ```bash
   docker build -t bambu-monitor ./bambu_telegram_bot
   docker run -d --name bambu-monitor --restart unless-stopped \
     -v "$(pwd)/data:/data" bambu-monitor
   ```
   The mounted `data` folder preserves configuration, spool mappings, and print
   history when you replace the container. After pulling an update, rebuild the
   image, run `docker rm -f bambu-monitor`, and repeat the run command above.

4. **Run with Python 3.11+** (from the repository root):
   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install -r bambu_telegram_bot/requirements.txt
   cp options.example.json bambu_telegram_bot/options.json
   ```
   Edit `bambu_telegram_bot/options.json`, then run:
   ```bash
   cd bambu_telegram_bot
   python bambu_monitor.py
   ```
   Keep this working directory: standalone configuration and saved state are
   loaded from it. After an update, stop the bot and run it again.

   Personal configuration and generated state files are ignored by Git. Keep
   credentials out of the tracked add-on `config.yaml` defaults.

### Development checks

From the repository root, run the offline regression tests:

```bash
python3 -B -m unittest discover -s tests -v
```

These tests simulate printer and service responses and do not need credentials.
For each release, keep `version` in `bambu_telegram_bot/config.yaml` and `VERSION`
in `bambu_telegram_bot/bambu_monitor.py` identical. Home Assistant detects add-on
updates through the configuration version.

---

## 🤖 Bot Commands

### 📊 Status
| Command | Description |
|---------|-------------|
| `/status` | Live printer status + active slot + Spoolman remaining weight + camera snapshot + last Gadget alert |
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
| `/addspool` | Guided creation: choose material, color and weight, then save to inventory |
| `/map [slot] [spool_id]` | Pick slot 1–4, then choose a spool button or reply with its ID. `/map 2` opens slot 2; `/map 2 42` maps directly |
| `/set <slot> <brand> <material>` | Create a new spool in Spoolman and map the slot automatically |
| `/update` | Interactive mode: Select a spool and manually update its remaining weight via chat |

### 🔦 Tools
| Command | Description |
|---------|-------------|
| `/light` | Toggle printer lamp on/off (requires HA light entity) |
| `/debug` | Show raw MQTT payload and internal state for troubleshooting |
| `/octo` | Set up selected OctoEverywhere alerts in your authorized private chat |
| `/help` | Show all commands |

---

## 🧵 Spoolman Integration

### Easy spool creation

Send `/addspool` (also available in the bot command menu):

1. Choose PLA, PETG, ABS, ASA or TPU.
2. Tap a color, or type a custom six-digit hex color such as `#12ABEF`.
3. Tap 250g, 500g or 1000g, or type the filament weight in grams. Exclude the empty spool's weight.
4. Review the details and tap **Save**. Nothing is created until you save.

The spool is added to inventory immediately. You can optionally map it to an AMS
slot using the buttons in the success message. Tap **Cancel** or send `/cancel`
while the draft is active to abandon it. Drafts expire after 15 minutes and are
not saved across bot restarts. The flow supports Hebrew and English.

New filaments use a 1.75 mm diameter and a typical density for the chosen material.
Adjust these in Spoolman for unusual blends or sizes, since density affects
length-to-weight conversion. Spools are created without a brand; you can add it
in Spoolman later.

If you run [Spoolman](https://github.com/Donkie/Spoolman) for filament tracking:

1. Set `spoolman_url` in your config (e.g. `http://192.168.1.x:7912`)
2. Map AMS slots to spools:

```
/map              # Choose slot 1–4, then press a spool button or send its ID
/map 2            # Choose a spool for slot 2 (buttons or a numeric reply)
/map 1 42         # Quick mode: Links AMS slot 1 → spool ID 42
/set 2 Bambu PLA  # Creates a NEW spool and links slot 2
/update           # Interactive mode: manually update a spool's remaining weight
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

For example, set `ha_weight_entity: "sensor.a1_print_weight"` for an A1 with that
entity name. At successful print completion, the bot reads its attributes such
as `AMS 1 Tray 1: 81.49` and deducts those **estimated grams** from the spool
mapped to slot 1. Map all used slots with `/map` before printing. The per-slot
breakdown works even when the printer no longer reports an active tray at finish.
Reported MQTT filament lengths take precedence; the bot never deducts both
length and HA weight for the same completion.

Only the first AMS's four slots are supported. Invalid, unsupported or stale
HA breakdowns are reported without charging their total to another slot. When
the print start time is known, the HA report (`last_reported`, or `last_updated`
on older HA versions) must be no earlier than
60 seconds before it (HA may receive a new job slightly before the bot).
Missing usage, missing mappings and failed writes are shown in the completion
message. The sensor provides an estimate for the print, not a physical scale
reading or a live spool balance. Existing completed prints are not backfilled.

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
| `octoeverywhere_notifications` | | Enable the authenticated local OctoEverywhere notification receiver (default: `false`) |
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


## Read-only inventory sharing (v2.0.12)

An optional sharing website lets anyone with its link view spool brands, filament names,
materials, colors, and remaining weights without a Home Assistant account. Visitors
cannot edit Spoolman or control the printer. Locations, notes, prices, and connection
credentials are excluded from the published snapshot.

Configure these two add-on options after setting up a compatible sharing website:

```yaml
inventory_share_url: "https://your-sharing-site.example"
inventory_share_token: "your-private-upload-key"
```

Keep your existing `spoolman_url`. Save and restart the bot. It uploads immediately
and every five minutes; the website checks for updates every minute. The private
upload key must match the site's `INVENTORY_SYNC_TOKEN` secret. Never share this key
with viewers. The upload endpoint is `/api/sync`; public reads use `/api/inventory`.

Sharing is disabled by default. Clear either sharing option and restart to stop future
uploads. This does not delete the last published snapshot or revoke public access;
change the website's access settings separately when you want to stop sharing.
If Spoolman or the sharing service is unavailable, the bot retries after five minutes
and the website retains its last successful snapshot with a last-synced time.


## Printer diagnostics (v2.0.13)

Send `/printerinfo` in the authorized Telegram chat to request printer and AMS firmware
versions plus selected status fields. It only sends `info/get_version` and `pushing/pushall`;
it does not load filament, pause, resume, or change printer settings. A timeout is shown
explicitly and any retained readings are labeled cached. Serial numbers, IP addresses,
access codes and unrelated MQTT fields are excluded. These diagnostics do not establish
support for choosing another AMS slot after a runout.


## Pause reasons (v2.0.14)

Pause notifications now include the printer-reported reason in English or Hebrew.
Known AMS runout codes show a filament-runout explanation and the active slot when
available. Other errors show their hexadecimal code and direct you to the printer
or Bambu Handy. Missing errors are explicitly marked as unreported, not assumed to
be a manual pause. A reason received after the pause generates one follow-up per
changed nonzero error code. Recently reported errors are retained across partial
MQTT messages; old errors and errors from a resumed print are cleared.


## Expanded error explanations (v2.0.15)

Pause messages translate 73 additional verified printer error codes into concise English
or Hebrew explanations, following the configured bot language. Covered reasons include
AMS Lite feeding, retraction and cutting failures; stuck filament; nozzle and bed
temperature faults; and user-requested or file-programmed pauses. The original code
remains visible for support. Unrecognized codes retain the explicit unknown-error
fallback. Descriptions are based on Bambu Studio's official
[error catalog](https://github.com/bambulab/BambuStudio/tree/master/resources/hms).


## BambuBuddy rename (v2.0.16)

The add-on display name, command help and startup messages now use BambuBuddy.
Update the existing add-on normally; do not uninstall it. Its slug, configuration
keys, data paths, Telegram credentials and GitHub repository URL are unchanged.
The Telegram profile display name is managed separately through BotFather.

## Clear inventory names and easier mapping (v2.0.18)

AMS slot buttons now match the AMS Lite layout: **1, 3** in the top row and
**2, 4** in the bottom row. After selecting a slot, send a spool ID such as `42`
or `#42`, or choose its button. `/spools` remains available during selection;
`/cancel` cancels the mapping request, not the print. Other commands exit the
mapping flow. Numeric replies belong to the user who selected the slot and
expire after 15 minutes. Only an existing active spool with filament remaining
can be mapped.

Both the spool picker and `/spools` display the full filament name, brand,
material, spool ID and remaining grams. The picker has navigation buttons;
long inventory lists continue in additional messages without truncation.
This updates the bot's Spoolman mapping only, not printer material settings.

## OctoEverywhere alerts and automatic pause (v2.0.28)

From v2.0.23, `/status` also shows the **last Gadget alert received by the bot**:
warning or Gadget-initiated pause, file name and elapsed time since receipt.
This is historical information, not live monitoring status. With no recorded
alert, the monitoring state is shown as unknown; silence does not mean a print
is healthy. No extra token or setup is needed if the webhook is already configured.
The last alert survives restarts. Only alerts received after this update are
recorded, and other OctoEverywhere events do not clear it. Gadget warnings and
Smart Pause events (7/8) also enter the acknowledged urgent-alert flow below.

Receive these three additional alerts in the existing BambuBuddy Telegram chat:

- **Possible print failure / spaghetti** reported by Gadget (event 7).
- **User intervention required**, with the reported error and code when available (event 12).
- **Print bed cooled down** after printing (event 15), with the latest MQTT bed temperature in °C. If no valid reading has arrived in the last three minutes, the message says the temperature is unavailable.

The notification can include OctoEverywhere's event snapshot and an **Open printer**
button. If the image cannot be sent, the text still arrives. These snapshots do not
use HA camera entities or require changing `/cam`. Gadget incidents use urgent text
with an acknowledgment button; other selected alerts retain their event snapshots.
Events depend on the printer and OctoEverywhere plugin reporting them.

### Gadget automatic pause and urgent reminders

With `octoeverywhere_notifications` enabled, v2.0.28 automatically requests **Pause**
when a Gadget warning reaches the bot for the matched, actively printing job.
This is triggered by a **possible failure warning**, so false positives can pause a print.
OctoEverywhere's own Smart Pause is a separate setting; an event 7 warning alone
previously only produced a message in BambuBuddy.

- A pause request is retried every **10 seconds** while the same job is still running,
  until fresh MQTT status confirms `PAUSE` or you acknowledge the incident.
  MQTT publication success alone is **not** displayed as a confirmed pause.
- An urgent Telegram message arrives immediately, then every **60 seconds** until
  you press **Acknowledge — silence reminders**. Confirmed pause produces a prompt
  status update. The button stops this incident's reminders and pause attempts;
  it **never resumes** printing. Inspect the print and use `/resume` separately.
- Telegram messages request normal notification sound, but cannot override your
  phone's muted chat or Focus settings. A Telegram failure does not prevent the
  pause attempt; failed urgent deliveries retry after 10 seconds.
- The authenticated print-start webhook (event 1) ties OctoEverywhere's PrintId to
  the local job; completion/failure (2/3) clears that binding. These events do not
  add duplicate Telegram messages. The filename must match (ignoring known file
  extensions), and MQTT must be connected with a state report within 30 seconds.
  Stale, missing or mismatched job data causes an urgent **manual-check** alert,
  not a command against an unidentified print. Allow the bot to receive the start
  of the **next print after installation** for automatic pause to work.
- Pending incidents and acknowledgments survive restarts in `gadget_incidents.json`.
  Repeated warnings for an unacknowledged incident do not create extra reminder
  loops. After acknowledgment, a later warning can open a new incident once the
  existing **10-minute duplicate window**, measured from first receipt, has elapsed.
  Each incident has its own acknowledgment button, so an old button cannot mute a
  new incident. Reminders continue even if the print ends, until acknowledged.

No new API key or connection is needed. Keep the existing LAN webhook configured
for this printer. `/status` still shows the last received Gadget event as history;
it is not a live Gadget monitoring check.

### Setup

1. Update BambuBuddy, enable `octoeverywhere_notifications` in its configuration,
   save and restart the add-on.
2. Send **`/octo`** in your configured private Telegram chat. The bot sends the
   webhook URL and a generated **Secret Key** in separate messages for copying.
3. Open [OctoEverywhere webhook notifications](https://octoeverywhere.com/notifications?handler=webhook),
   select **this printer**, choose **LAN** delivery, and paste both values.
4. Save and use **Test Notification**. BambuBuddy should send a confirmation.
   Test events (16) are supported even though normal print lifecycle events are filtered.

The generated URL uses the add-on's own internal hostname and port `8765`.
When OctoEverywhere and BambuBuddy run as add-ons on the same Home Assistant,
leave the host port in the Network section blank. Their internal network is enough.
If OctoEverywhere runs on another computer, explicitly map `8765/tcp` to a host
port in the add-on's Network settings and use `http://<HA-LAN-IP>:<host-port>/octoeverywhere`
instead. Keep LAN delivery selected. For standalone Docker, publish the port only
on the LAN address and use the Docker host's LAN address in the webhook URL.

The key is generated once and saved in `octoeverywhere_notifications.json` in the
bot's data directory, with private file permissions. `/octo` only discloses it in
the authorized **private** chat, never a group. For a bot configured for a group,
the owner must read the key from this private data file; the group cannot retrieve it.
The webhook verifies the key on every request. `/debug` displays whether the
receiver is active, without showing the key. Disabling the setting and restarting
stops the listener; remove or disable the webhook in OctoEverywhere as well.

### Duplicate filtering and delivery

- Start, finish and failure webhooks maintain the Gadget job binding without
  being forwarded. Progress, pause/resume, filament-change and other unselected
  events are acknowledged but not forwarded. The existing bot
  continues to handle its MQTT notifications and Spoolman accounting.
- Identical Gadget warnings and intervention requests are suppressed for **10 minutes**
  per printer and print. A different error code or print can notify immediately.
  Bed-cooled events are sent once per printer/print within a seven-day retention window.
- If an intervention request and an existing MQTT pause notice have the same known
  error code and filename within **two minutes**, only the first delivered notice is
  sent. Intervention webhooks wait three seconds to let the printer's own pause
  explanation arrive first. Unknown codes and different filenames remain separate.
- Deduplication records and pending notifications survive restarts. Webhooks are
  acknowledged after being queued on disk, without waiting for Telegram. Failed
  ordinary deliveries retry with a delay, for up to 15 minutes; older pending ordinary
  alerts expire. Gadget urgent reminders persist until acknowledged as described above.
  As with other Telegram sends, a lost API response can make delivery ambiguous.

This uses OctoEverywhere's existing notification service, rather than submitting
images to a separate AI analysis API. See the official
[LAN webhook setup](https://docs.octoeverywhere.com/webhook-notifications/setup/),
[event list](https://docs.octoeverywhere.com/webhook-notifications/event-types/), and
[payload format](https://docs.octoeverywhere.com/webhook-notifications/json-payload-format/).
