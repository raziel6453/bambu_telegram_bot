#!/bin/bash

# ============================================================
#  Bambu Lab Telegram Monitor - Installer
#  For Raspberry Pi (tested on Pi 4 & Pi 5, Raspberry Pi OS)
# ============================================================

set -e

INSTALL_DIR="$HOME/bambu_monitor"
SERVICE_NAME="bambu-monitor"
VENV_DIR="$INSTALL_DIR/venv"
CONFIG_FILE="$INSTALL_DIR/config.py"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Bambu Lab Telegram Monitor - Installer     ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. System dependencies ────────────────────────────────
echo -e "${YELLOW}[1/7] Updating system packages...${NC}"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv git curl

# ── 2. Create install directory ───────────────────────────
echo -e "${YELLOW}[2/7] Creating install directory at $INSTALL_DIR ...${NC}"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ── 3. Python virtualenv + packages ───────────────────────
echo -e "${YELLOW}[3/7] Setting up Python environment...${NC}"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet \
    paho-mqtt \
    requests \
    flask \
    flask-cors \
    psutil

# ── 4. Write config.py (user fills this in) ───────────────
echo -e "${YELLOW}[4/7] Writing configuration file...${NC}"

if [ -f "$CONFIG_FILE" ]; then
    echo -e "${YELLOW}    config.py already exists — skipping (your settings are safe).${NC}"
else
cat > "$CONFIG_FILE" << 'CONFIGEOF'
# ============================================================
#  config.py  –  Fill in your details before running the bot
# ============================================================

# ── Bambu Lab printer ────────────────────────────────────
PRINTER_IP        = "YOUR_PRINTER_IP"          # e.g. "192.168.1.50"
PRINTER_SERIAL    = "YOUR_PRINTER_SERIAL"      # e.g. "01P00A123456789"
PRINTER_PASSWORD  = "YOUR_PRINTER_ACCESS_CODE" # 8-character code shown in printer settings
BAMBU_USERNAME    = "YOUR_BAMBU_EMAIL"         # Bambu Lab account email
BAMBU_PASSWORD    = "YOUR_BAMBU_ACCOUNT_PASS"  # Bambu Lab account password

# ── Telegram bot ─────────────────────────────────────────
TELEGRAM_TOKEN    = "YOUR_TELEGRAM_BOT_TOKEN"  # From @BotFather
TELEGRAM_CHAT_ID  = "YOUR_CHAT_ID"             # Your personal chat ID

# ── Language (he = Hebrew, en = English) ─────────────────
LANGUAGE = "he"

# ── AMS filament slots (optional – leave empty if no AMS) ─
# Format: { slot_number: {"type": "PLA", "color": "🔵", "weight_g": 1000, "price_per_kg": 80} }
AMS_SLOTS = {}
CONFIGEOF
    echo -e "${GREEN}    config.py created. Edit it with your details before starting the bot.${NC}"
fi

# ── 5. Write main bot file ────────────────────────────────
echo -e "${YELLOW}[5/7] Installing bot files...${NC}"

cat > "$INSTALL_DIR/bambu_monitor.py" << 'BOTEOF'
#!/usr/bin/env python3
"""
Bambu Lab Telegram Monitor
Sends print start/finish notifications, progress updates, and filament tracking.
Requires: config.py filled with your printer and Telegram details.
"""

import os, sys, time, json, threading, logging, requests, ssl, socket
from datetime import datetime, timedelta

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Missing paho-mqtt. Run: pip install paho-mqtt")

# ── Load config ───────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.py")
if not os.path.exists(CONFIG_PATH):
    sys.exit(f"config.py not found at {CONFIG_PATH}")

config = {}
with open(CONFIG_PATH) as f:
    exec(f.read(), config)

PRINTER_IP       = config.get("PRINTER_IP", "")
PRINTER_SERIAL   = config.get("PRINTER_SERIAL", "")
PRINTER_PASSWORD = config.get("PRINTER_PASSWORD", "")
BAMBU_USERNAME   = config.get("BAMBU_USERNAME", "")
BAMBU_PASSWORD   = config.get("BAMBU_PASSWORD", "")
TELEGRAM_TOKEN   = config.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = config.get("TELEGRAM_CHAT_ID", "")
LANGUAGE         = config.get("LANGUAGE", "he")
AMS_SLOTS        = config.get("AMS_SLOTS", {})

if "YOUR_" in PRINTER_IP or not PRINTER_IP:
    sys.exit("❌  Please fill in config.py with your printer and Telegram details before running.")

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "bambu_monitor.log"))
    ]
)
log = logging.getLogger("bambu")

# ── Messages ──────────────────────────────────────────────
STRINGS = {
    "he": {
        "print_start":    "🖨️ ההדפסה התחילה!\nקובץ: {filename}\nמשך משוער: {eta}",
        "print_done":     "✅ ההדפסה הסתיימה!\nקובץ: {filename}\nסה\"כ זמן: {duration}",
        "print_failed":   "❌ ההדפסה נכשלה.\nקובץ: {filename}",
        "progress":       "📊 התקדמות: {pct}%\nנותר: {remaining}",
        "low_filament":   "⚠️ נגמר חוט! סלוט {slot} נותר: {grams}g",
        "connected":      "✅ הבוט מחובר ועובד!",
        "disconnected":   "🔴 הפסקתי עבודה.",
    },
    "en": {
        "print_start":    "🖨️ Print started!\nFile: {filename}\nETA: {eta}",
        "print_done":     "✅ Print finished!\nFile: {filename}\nTotal time: {duration}",
        "print_failed":   "❌ Print failed.\nFile: {filename}",
        "progress":       "📊 Progress: {pct}%\nRemaining: {remaining}",
        "low_filament":   "⚠️ Low filament! Slot {slot}: {grams}g left",
        "connected":      "✅ Bot connected and running!",
        "disconnected":   "🔴 Bot stopped.",
    }
}

def t(key, **kwargs):
    tmpl = STRINGS.get(LANGUAGE, STRINGS["en"]).get(key, key)
    return tmpl.format(**kwargs)

# ── Telegram helpers ──────────────────────────────────────
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        if not r.ok:
            log.warning(f"Telegram error: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

# ── State ─────────────────────────────────────────────────
_state = {
    "printing": False,
    "filename": "",
    "start_time": None,
    "mc_percent": 0,
    "mc_remaining_time": 0,
    "last_milestone": 0,
    "gcode_state": "",
}
_lock = threading.Lock()

def _format_minutes(mins):
    if mins <= 0:
        return "–"
    h, m = divmod(int(mins), 60)
    return f"{h}h {m}m" if h else f"{m}m"

def _format_duration(seconds):
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"

# ── MQTT callbacks ────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to printer MQTT")
        topic = f"device/{PRINTER_SERIAL}/report"
        client.subscribe(topic)
        log.info(f"Subscribed to {topic}")
        send_telegram(t("connected"))
    else:
        log.error(f"MQTT connect failed, rc={rc}")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return

    print_data = payload.get("print", {})
    if not print_data:
        return

    with _lock:
        gcode_state  = print_data.get("gcode_state", _state["gcode_state"])
        mc_percent   = print_data.get("mc_percent", _state["mc_percent"])
        mc_remaining = print_data.get("mc_remaining_time", _state["mc_remaining_time"])
        filename     = print_data.get("subtask_name", _state["filename"]) or _state["filename"]

        prev_state  = _state["gcode_state"]
        was_printing = _state["printing"]

        _state["gcode_state"]        = gcode_state
        _state["mc_percent"]         = mc_percent
        _state["mc_remaining_time"]  = mc_remaining
        if filename:
            _state["filename"] = filename

        # Print started
        if gcode_state == "RUNNING" and prev_state in ("PREPARE", "IDLE", "FINISH", ""):
            _state["printing"]   = True
            _state["start_time"] = datetime.now()
            _state["last_milestone"] = 0
            eta = _format_minutes(mc_remaining)
            log.info(f"Print started: {filename}")
            send_telegram(t("print_start", filename=filename or "–", eta=eta))

        # Print finished
        elif gcode_state == "FINISH" and was_printing:
            _state["printing"] = False
            dur = ""
            if _state["start_time"]:
                dur = _format_duration((datetime.now() - _state["start_time"]).total_seconds())
            log.info(f"Print finished: {filename}")
            send_telegram(t("print_done", filename=filename or "–", duration=dur))

        # Print failed / cancelled
        elif gcode_state in ("FAILED", "PAUSE") and was_printing and gcode_state == "FAILED":
            _state["printing"] = False
            log.info(f"Print failed: {filename}")
            send_telegram(t("print_failed", filename=filename or "–"))

        # Progress milestones 25 / 50 / 75
        elif gcode_state == "RUNNING" and was_printing:
            for milestone in (25, 50, 75):
                if mc_percent >= milestone > _state["last_milestone"]:
                    _state["last_milestone"] = milestone
                    send_telegram(t("progress", pct=milestone, remaining=_format_minutes(mc_remaining)))
                    break

        # Low filament check
        for slot_id, slot_info in AMS_SLOTS.items():
            weight = slot_info.get("weight_g", 9999)
            if weight < 100:
                send_telegram(t("low_filament", slot=slot_id, grams=weight))


# ── Bambu Cloud MQTT (for printers behind firmware 01.07+) ─
def get_bambu_token():
    """Obtain JWT from Bambu Cloud for MQTT auth."""
    try:
        r = requests.post(
            "https://api.bambulab.com/v1/user-service/user/login",
            json={"account": BAMBU_USERNAME, "password": BAMBU_PASSWORD},
            timeout=15
        )
        data = r.json()
        token = data.get("accessToken") or data.get("token")
        if token:
            log.info("Bambu Cloud token obtained.")
            return token
        log.warning(f"Token response: {data}")
    except Exception as e:
        log.error(f"Token fetch failed: {e}")
    return None

def connect_cloud_mqtt():
    token = get_bambu_token()
    if not token:
        log.error("Cannot connect to Cloud MQTT without token.")
        return None

    client = mqtt.Client()
    client.username_pw_set("bblp", token)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message

    broker = "us.mqtt.bambulab.com"
    port   = 8883
    log.info(f"Connecting to Cloud MQTT {broker}:{port} ...")
    client.connect(broker, port, keepalive=60)
    return client

def connect_local_mqtt():
    client = mqtt.Client()
    client.username_pw_set("bblp", PRINTER_PASSWORD)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message

    log.info(f"Connecting to local MQTT {PRINTER_IP}:8883 ...")
    client.connect(PRINTER_IP, 8883, keepalive=60)
    return client

# ── Main ──────────────────────────────────────────────────
def main():
    log.info("Bambu Monitor starting...")

    # Try local first, fall back to cloud
    client = None
    try:
        sock = socket.create_connection((PRINTER_IP, 8883), timeout=5)
        sock.close()
        client = connect_local_mqtt()
        log.info("Using LOCAL MQTT")
    except Exception:
        log.info("Local MQTT not reachable – trying Cloud MQTT...")
        client = connect_cloud_mqtt()

    if not client:
        sys.exit("Could not connect to printer via local or cloud MQTT.")

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        send_telegram(t("disconnected"))
        log.info("Stopped by user.")

if __name__ == "__main__":
    main()
BOTEOF

chmod +x "$INSTALL_DIR/bambu_monitor.py"

# ── 6. Create systemd service ─────────────────────────────
echo -e "${YELLOW}[6/7] Setting up systemd service...${NC}"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" > /dev/null << SVCEOF
[Unit]
Description=Bambu Lab Telegram Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python3 $INSTALL_DIR/bambu_monitor.py
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
echo -e "${GREEN}    systemd service created and enabled.${NC}"

# ── 7. Write helper commands ──────────────────────────────
echo -e "${YELLOW}[7/7] Writing helper script...${NC}"

cat > "$INSTALL_DIR/manage.sh" << 'MGEOF'
#!/bin/bash
SERVICE="bambu-monitor"
case "$1" in
  start)   sudo systemctl start  $SERVICE && echo "Started." ;;
  stop)    sudo systemctl stop   $SERVICE && echo "Stopped." ;;
  restart) sudo systemctl restart $SERVICE && echo "Restarted." ;;
  status)  sudo systemctl status $SERVICE ;;
  logs)    sudo journalctl -u $SERVICE -f ;;
  *)       echo "Usage: $0 {start|stop|restart|status|logs}" ;;
esac
MGEOF
chmod +x "$INSTALL_DIR/manage.sh"

# ── Done ──────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          Installation complete! ✅           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "Next steps:"
echo ""
echo "  1. Edit your config file:"
echo "     nano $CONFIG_FILE"
echo ""
echo "  2. Start the bot:"
echo "     $INSTALL_DIR/manage.sh start"
echo ""
echo "  3. Check it's running:"
echo "     $INSTALL_DIR/manage.sh status"
echo ""
echo "  4. Watch live logs:"
echo "     $INSTALL_DIR/manage.sh logs"
echo ""
