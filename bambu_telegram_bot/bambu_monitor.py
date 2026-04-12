#!/usr/bin/env python3
"""
Bambu Lab Telegram Monitor for Home Assistant Add-on
Sends print start/finish notifications, progress updates, and filament tracking.
"""

import os, sys, time, json, threading, logging, requests, ssl

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Missing paho-mqtt. Run: pip install paho-mqtt")
    
try:
    import telebot
except ImportError:
    sys.exit("Missing pyTelegramBotAPI. Run: pip install pyTelegramBotAPI")

# ── Load config from HA Add-on options.json ───────────────
OPTIONS_PATH = "/data/options.json"
options = {}

if os.path.exists(OPTIONS_PATH):
    with open(OPTIONS_PATH) as f:
        options = json.load(f)
else:
    logging.error("No options.json found at /data/options.json. This must be run as an HA Addon.")
    sys.exit(1)

PRINTER_IP       = options.get("printer_ip", "")
PRINTER_SERIAL   = options.get("printer_serial", "")
PRINTER_PASSWORD = options.get("printer_password", "")
BAMBU_USERNAME   = options.get("bambu_username", "")
BAMBU_PASSWORD   = options.get("bambu_password", "")
TELEGRAM_TOKEN   = options.get("telegram_token", "")
TELEGRAM_CHAT_ID = options.get("telegram_chat_id", "")
LANGUAGE         = options.get("language", "he")
AMS_SLOTS        = {}

if "YOUR_" in PRINTER_IP or not PRINTER_IP:
    sys.exit("❌ Please fill in the add-on configuration with your printer and Telegram details.")

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("bambu")

# ── Telegram Bot Init ─────────────────────────────────────
bot = telebot.TeleBot(TELEGRAM_TOKEN)

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
        "status_printing": "🖨️ מדפיס כעת...\nקובץ: {filename}\nהתקדמות: {pct}%\nזמן נותר: {eta}",
        "status_idle":    "💤 המדפסת כרגע במצב המתנה.",
    },
    "en": {
        "print_start":    "🖨️ Print started!\nFile: {filename}\nETA: {eta}",
        "print_done":     "✅ Print finished!\nFile: {filename}\nTotal time: {duration}",
        "print_failed":   "❌ Print failed.\nFile: {filename}",
        "progress":       "📊 Progress: {pct}%\nRemaining: {remaining}",
        "low_filament":   "⚠️ Low filament! Slot {slot}: {grams}g left",
        "connected":      "✅ Bot connected and running!",
        "disconnected":   "🔴 Bot stopped.",
        "status_printing": "🖨️ Currently Printing...\nFile: {filename}\nProgress: {pct}%\nETA: {eta}",
        "status_idle":    "💤 Printer is currently idle.",
    }
}

def t(key, **kwargs):
    tmpl = STRINGS.get(LANGUAGE, STRINGS["en"]).get(key, key)
    return tmpl.format(**kwargs)

# ── Telegram helpers ──────────────────────────────────────
from datetime import datetime
def send_telegram(text):
    try:
        bot.send_message(TELEGRAM_CHAT_ID, text)
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
    
# ── Interactive Bot Commands ──────────────────────────────
@bot.message_handler(commands=['status'])
def send_status(message):
    # Only reply to the configured owner
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID):
        return
        
    with _lock:
        if _state["printing"]:
            filename = _state["filename"] or "Unknown"
            pct = _state["mc_percent"]
            eta = _format_minutes(_state["mc_remaining_time"])
            res = t("status_printing", filename=filename, pct=pct, eta=eta)
        else:
            res = t("status_idle")
            
    bot.reply_to(message, res)

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

# ── Bambu Cloud MQTT ─────────────────────────────────────────
def get_bambu_token():
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
import socket
def main():
    log.info("Bambu Monitor Add-on starting...")
    
    # Start Telegram polling thread
    t_bot = threading.Thread(target=bot.infinity_polling, daemon=True)
    t_bot.start()

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
