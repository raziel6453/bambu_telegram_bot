#!/usr/bin/env python3
"""
Bambu Lab Telegram Monitor for Home Assistant Add-on
Sends print start/finish notifications, progress updates, and filament tracking.
"""

import os, sys, time, json, threading, logging, requests, ssl
from datetime import datetime

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
CUSTOM_SPOOLS_FILE = "/data/custom_spools.json"
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

# ── Custom Spools Store ───────────────────────────────────
def load_custom_spools():
    if os.path.exists(CUSTOM_SPOOLS_FILE):
        try:
            with open(CUSTOM_SPOOLS_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_custom_spools(data):
    try:
        with open(CUSTOM_SPOOLS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        log.error(f"Failed to save custom spools: {e}")

# ── Telegram Bot Init ─────────────────────────────────────
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ── Messages ──────────────────────────────────────────────
STRINGS = {
    "he": {
        "print_start":    "🖨️ ההדפסה התחילה!\nקובץ: {filename}\nמשקל: {weight}g\nמשך משוער: {eta}",
        "print_done":     "✅ ההדפסה הסתיימה!\nקובץ: {filename}\nמשקל: {weight}g\nסה\"כ זמן: {duration}",
        "print_failed":   "❌ ההדפסה נכשלה.\nקובץ: {filename}",
        "progress":       "📊 התקדמות: {pct}%\nנותר: {remaining}",
        "low_filament":   "⚠️ נגמר חוט! סלוט {slot} נותר בערך: {grams}g",
        "connected":      "✅ הבוט מחובר ועובד!",
        "disconnected":   "🔴 הפסקתי עבודה.",
        "status_printing": "🖨️ מדפיס כעת...\nקובץ: {filename}\nמשקל: {weight}g\nהתקדמות: {pct}%\nזמן נותר: {eta}",
        "status_idle":    "💤 המדפסת כרגע במצב המתנה.",
        "ams_title":      "📦 סטטוס מערכת ה-AMS:\n",
        "ams_slot":       "סלוט {slot}: {emoji} סוג: {type} ({brand}) - נותר משוער: {grams}g\n",
        "ams_custom_slot":"סלוט {slot}: {emoji} סוג: {type} ({brand}) - נותר: {grams}g (מוזן ידנית)\n",
        "ams_empty":      "סלוט {slot}: ❌ ריק\n",
        "setslot_success": "✅ סלוט {slot} עודכן ל-{grams} גרם. הבוט יחסיר ממשקל זה באופן אוטומטי בהדפסות הבאות.",
        "setslot_fail":    "❌ שגיאה: יש להקליד במבנה: /setslot <מספר 1-4> <גרמים>",
    },
    "en": {
        "print_start":    "🖨️ Print started!\nFile: {filename}\nWeight: {weight}g\nETA: {eta}",
        "print_done":     "✅ Print finished!\nFile: {filename}\nWeight: {weight}g\nTotal time: {duration}",
        "print_failed":   "❌ Print failed.\nFile: {filename}",
        "progress":       "📊 Progress: {pct}%\nRemaining: {remaining}",
        "low_filament":   "⚠️ Low filament! Slot {slot}: ~{grams}g left",
        "connected":      "✅ Bot connected and running!",
        "disconnected":   "🔴 Bot stopped.",
        "status_printing": "🖨️ Currently Printing...\nFile: {filename}\nWeight: {weight}g\nProgress: {pct}%\nETA: {eta}",
        "status_idle":    "💤 Printer is currently idle.",
        "ams_title":      "📦 AMS Status:\n",
        "ams_slot":       "Slot {slot}: {emoji} Type: {type} ({brand}) - Estimated: {grams}g\n",
        "ams_custom_slot":"Slot {slot}: {emoji} Type: {type} ({brand}) - Remaining: {grams}g (Manual Tracker)\n",
        "ams_empty":      "Slot {slot}: ❌ Empty\n",
        "setslot_success": "✅ Slot {slot} updated to {grams} grams. It will auto-subtract after successful prints.",
        "setslot_fail":    "❌ Error: Use format: /setslot <1-4> <grams>",
    }
}

def t(key, **kwargs):
    tmpl = STRINGS.get(LANGUAGE, STRINGS["en"]).get(key, key)
    return tmpl.format(**kwargs)

def color_to_emoji(hexcode):
    if not hexcode or len(hexcode) < 6: return "🧵"
    try:
        r, g, b = int(hexcode[0:2], 16), int(hexcode[2:4], 16), int(hexcode[4:6], 16)
        if r > 200 and g > 200 and b > 200: return "⚪"
        if r < 50 and g < 50 and b < 50: return "⚫"
        if r > 200 and g < 100 and b < 100: return "🔴"
        if r < 100 and g > 200 and b < 100: return "🟢"
        if r < 100 and g < 100 and b > 200: return "🔵"
        if r > 200 and g > 200 and b < 100: return "🟡"
        if r > 200 and g > 100 and b < 100: return "🟠"
    except Exception:
        pass
    return "🧵"

# ── Telegram helpers ──────────────────────────────────────
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
    "print_weight": 0,
    "tray_now": 255,
}
_ams_state = {}
_alerted_slots = set()
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
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    with _lock:
        if _state["printing"]:
            filename = _state["filename"] or "Unknown"
            pct = _state["mc_percent"]
            weight = _state["print_weight"]
            eta = _format_minutes(_state["mc_remaining_time"])
            res = t("status_printing", filename=filename, pct=pct, weight=weight, eta=eta)
        else:
            res = t("status_idle")
    bot.reply_to(message, res)

@bot.message_handler(commands=['setslot'])
def handle_setslot(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    parts = message.text.split()
    if len(parts) >= 3:
        try:
            slot_input = int(parts[1])
            if 1 <= slot_input <= 4:
                slot_id = str(slot_input - 1)
                grams = int(parts[2])
                spools = load_custom_spools()
                spools[slot_id] = grams
                save_custom_spools(spools)
                bot.reply_to(message, t("setslot_success", slot=slot_input, grams=grams))
                return
        except ValueError:
            pass
    bot.reply_to(message, t("setslot_fail"))

@bot.message_handler(commands=['ams'])
def send_ams(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    with _lock:
        if not _ams_state:
            bot.reply_to(message, "Waiting for AMS data from printer...")
            return

        custom_spools = load_custom_spools()
        res = t("ams_title")
        for i in range(4):
            slot_data = _ams_state.get(str(i))
            # If no data is seen from printer, it's typically empty
            if not slot_data or slot_data.get("type") in ("Unknown", "") or slot_data.get("remain") < 0:
                # If they saved manual grams for an empty slot we can still display it, but usually empty means pulled.
                res += t("ams_empty", slot=i+1)
            else:
                emoji = color_to_emoji(slot_data.get("color"))
                typ = slot_data.get("type", "")
                brand = slot_data.get("brand", "")
                
                if str(i) in custom_spools:
                    grams = custom_spools[str(i)]
                    res += t("ams_custom_slot", slot=i+1, emoji=emoji, type=typ, brand=brand, grams=grams)
                else:
                    remain_pct = slot_data.get("remain", 0)
                    grams = remain_pct * 10
                    res += t("ams_slot", slot=i+1, emoji=emoji, type=typ, brand=brand, grams=grams)

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
        # Update AMS Active Tray tracker
        ams_block = print_data.get("ams", {})
        if "tray_now" in ams_block:
            try:
                _state["tray_now"] = int(ams_block["tray_now"])
            except Exception:
                pass

        # Process Print State
        gcode_state     = print_data.get("gcode_state", _state["gcode_state"])
        mc_percent      = print_data.get("mc_percent", _state["mc_percent"])
        mc_remaining    = print_data.get("mc_remaining_time", _state["mc_remaining_time"])
        filename        = print_data.get("subtask_name", _state["filename"]) or _state["filename"]
        weight_estimate = print_data.get("print_weight", print_data.get("subtask_weight", _state["print_weight"]))

        prev_state   = _state["gcode_state"]
        was_printing = _state["printing"]

        _state["gcode_state"]        = gcode_state
        _state["mc_percent"]         = mc_percent
        _state["mc_remaining_time"]  = mc_remaining
        _state["print_weight"]       = weight_estimate
        if filename:
            _state["filename"] = filename

        # Print started
        if gcode_state == "RUNNING" and prev_state in ("PREPARE", "IDLE", "FINISH", ""):
            _state["printing"]   = True
            _state["start_time"] = datetime.now()
            _state["last_milestone"] = 0
            eta = _format_minutes(mc_remaining)
            log.info(f"Print started: {filename}")
            send_telegram(t("print_start", filename=filename or "–", weight=_state["print_weight"], eta=eta))

        # Print finished
        elif gcode_state == "FINISH" and was_printing:
            _state["printing"] = False
            dur = ""
            if _state["start_time"]:
                dur = _format_duration((datetime.now() - _state["start_time"]).total_seconds())
            
            weight_used = _state.get("print_weight", 0)
            tray = _state.get("tray_now", 255)
            
            # Custom deduction
            if weight_used > 0 and tray != 255:
                spools = load_custom_spools()
                str_tray = str(tray)
                if str_tray in spools:
                    new_w = max(0, spools[str_tray] - weight_used)
                    spools[str_tray] = new_w
                    save_custom_spools(spools)
                    log.info(f"Subtracted {weight_used}g from custom Slot {tray+1}. Remaining: {new_w}g")

            log.info(f"Print finished: {filename}")
            send_telegram(t("print_done", filename=filename or "–", weight=weight_used, duration=dur))

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

        # Process AMS Slot data
        ams_data = print_data.get("ams", {}).get("ams", [])
        for ams_unit in ams_data:
            for tray in ams_unit.get("tray", []):
                slot_id = tray.get("id")
                if slot_id is None: continue
                remain_pct = tray.get("remain", -1)

                _ams_state[str(slot_id)] = {
                    "type": tray.get("tray_type", "Unknown"),
                    "color": tray.get("tray_color", "FFFFFF"),
                    "brand": tray.get("tray_sub_brands", ""),
                    "remain": remain_pct
                }

                # Alerts based on threshold
                # If custom spool falls below 100g, or if normal spool drops below 10%
                check_grams = 9999
                spools = load_custom_spools()
                if str(slot_id) in spools:
                    check_grams = spools[str(slot_id)]
                elif 0 <= remain_pct <= 100:
                    check_grams = remain_pct * 10
                
                if check_grams < 100:
                    if slot_id not in _alerted_slots:
                        send_telegram(t("low_filament", slot=int(slot_id)+1, grams=check_grams))
                        _alerted_slots.add(slot_id)
                else:
                    if slot_id in _alerted_slots:
                        _alerted_slots.remove(slot_id)

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
