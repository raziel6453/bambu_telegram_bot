#!/usr/bin/env python3
"""
Bambu Lab Telegram Monitor for Home Assistant Add-on
Sends print start/finish notifications, progress updates, and Spoolman filament tracking.
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
SPOOLMAN_MAPPING_FILE = "/data/spoolman_mapping.json"
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
SPOOLMAN_URL     = options.get("spoolman_url", "").strip().rstrip("/")
HA_CAMERA_ENTITY = options.get("ha_camera_entity", "").strip()
HA_LIGHT_ENTITY  = options.get("ha_light_entity", "").strip()
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
HA_API_BASE      = "http://supervisor/core/api"

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

# ── Spoolman Slot Mappings ────────────────────────────────
def load_spoolman_mapping():
    if os.path.exists(SPOOLMAN_MAPPING_FILE):
        try:
            with open(SPOOLMAN_MAPPING_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_spoolman_mapping(data):
    try:
        with open(SPOOLMAN_MAPPING_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        log.error(f"Failed to save spoolman mapping: {e}")

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
        "status_printing": "🖨️ מדפיס כעת...\nקובץ: {filename}\nמשקל: {weight}g\nהתקדמות: {pct}%\nזמן נותר: {eta}\n{light_status}",
        "status_idle":    "💤 המדפסת כרגע במצב המתנה.\n{light_status}",
        "ams_title":      "📦 סטטוס מערכת ה-AMS:\n",
        "ams_slot":       "סלוט {slot}: {emoji} סוג: {type} ({brand}) - נותר משוער: {grams}g\n",
        "ams_spoolman_slot": "סלוט {slot}: {emoji} סוג: {brand} {material} - נותר: {grams}g (Spoolman)\n",
        "ams_spoolman_fail": "סלוט {slot}: ❌ שגיאת חיבור ל-Spoolman (ID {sid})\n",
        "ams_empty":      "סלוט {slot}: ❌ ריק\n",
        "spoolman_success": "✅ מאגר Spoolman ID {sid} שויך לסלוט {slot} בהצלחה. החסרה אוטומטית הופעלה.",
        "spoolman_fail": "❌ שגיאה: יש להקליד במבנה: /spoolman <מזהה_ספול> <סלוט_1-4>",
        "spoolman_not_enabled": "❌ Spoolman לא הוגדר בהגדרות Addon של Home Assistant.",
        "spools_title":   "📦 מאגר Spoolman:\n",
        "spools_item":    "ID: #{id} | {emoji} {brand} {material} | משקל נותר: {grams}g\n",
        "spools_empty":   "המאגר ריק.\n",
        "help": (
            "🖨️ *Bambu Telegram Monitor — פקודות זמינות:*\n\n"
            "/status — סטטוס נוכחי של המדפסת\n"
            "/ams — סטטוס מגשי ה-AMS\n"
            "/cam — צילום חי מהמדפסת (מצריך HA)\n"
            "/light — הדלקה/כיבוי של מנורת המדפסת\n"
            "/spools — רשימת ספולים ב-Spoolman\n"
            "/map <slot> <spool\\_id> — שיוך סלוט ל-Spoolman\n"
            "/spoolman <spool\\_id> <slot> — שיוך (פורמט חלופי)\n"
            "/help — הצגת תפריט זה"
        ),
        "cam_error":      "❌ שגיאה במשיכת תמונה מ-Home Assistant: {error}",
        "cam_no_entity":  "❌ לא הוגדרה מצלמה ולא נמצאה מצלמת Bambu אוטומטית ב-Home Assistant.",
        "light_on":       "💡 המנורה דולקת",
        "light_off":      "🌑 המנורה כבויה",
        "light_fail":     "❌ שגיאה בשליטה על המנורה: {error}",
        "light_no_entity": "❌ לא הוגדר גוף תאורה ולא נמצא גוף תאורה אוטומטי של Bambu ב-Home Assistant.",
        "ha_no_api":      "❌ הבוט לא רץ כ-Add-on עם הרשאות API של Home Assistant."
    },
    "en": {
        "print_start":    "🖨️ Print started!\nFile: {filename}\nWeight: {weight}g\nETA: {eta}",
        "print_done":     "✅ Print finished!\nFile: {filename}\nWeight: {weight}g\nTotal time: {duration}",
        "print_failed":   "❌ Print failed.\nFile: {filename}",
        "progress":       "📊 Progress: {pct}%\nRemaining: {remaining}",
        "low_filament":   "⚠️ Low filament! Slot {slot}: ~{grams}g left",
        "connected":      "✅ Bot connected and running!",
        "disconnected":   "🔴 Bot stopped.",
        "status_printing": "🖨️ Currently Printing...\nFile: {filename}\nWeight: {weight}g\nProgress: {pct}%\nETA: {eta}\n{light_status}",
        "status_idle":    "💤 Printer is currently idle.\n{light_status}",
        "ams_title":      "📦 AMS Status:\n",
        "ams_slot":       "Slot {slot}: {emoji} Type: {type} ({brand}) - Estimated: {grams}g\n",
        "ams_spoolman_slot": "Slot {slot}: {emoji} Type: {brand} {material} - Remaining: {grams}g (Spoolman)\n",
        "ams_spoolman_fail": "Slot {slot}: ❌ Spoolman Connection Error (ID {sid})\n",
        "ams_empty":      "Slot {slot}: ❌ Empty\n",
        "spoolman_success": "✅ Spoolman ID {sid} mapped to Slot {slot}. Auto-subtraction enabled.",
        "spoolman_fail": "❌ Error: Use format: /spoolman <spool_id> <slot_1-4>",
        "spoolman_not_enabled": "❌ Spoolman URL is not configured in Home Assistant Add-on options.",
        "spools_title":   "📦 Spoolman Inventory:\n",
        "spools_item":    "ID: #{id} | {emoji} {brand} {material} | Weight: {grams}g\n",
        "spools_empty":   "Inventory is empty.\n",
        "help": (
            "🖨️ *Bambu Telegram Monitor — Available Commands:*\n\n"
            "/status — Current printer status\n"
            "/ams — AMS slot status\n"
            "/cam — Live snapshot (requires HA)\n"
            "/light — Toggle printer lamp (requires HA)\n"
            "/spools — List Spoolman inventory\n"
            "/map <slot> <spool\\_id> — Map AMS slot to Spoolman spool\n"
            "/spoolman <spool\\_id> <slot> — Map spool (alternative format)\n"
            "/help — Show this menu"
        ),
        "cam_error":      "❌ Error fetching snapshot from Home Assistant: {error}",
        "cam_no_entity":  "❌ No camera entity configured and no Bambu camera discovered autoamtically in Home Assistant.",
        "light_on":       "💡 Light is ON",
        "light_off":      "🌑 Light is OFF",
        "light_fail":     "❌ Error controlling the light: {error}",
        "light_no_entity": "❌ No light entity configured and no Bambu light discovered automatically in Home Assistant.",
        "ha_no_api":      "❌ Bot is not running as an Add-on with Home Assistant API access."
    }
}

def t(key, **kwargs):
    tmpl = STRINGS.get(LANGUAGE, STRINGS["en"]).get(key, key)
    return tmpl.format(**kwargs)

def color_to_emoji(hexcode):
    if not hexcode or len(hexcode) < 6: return "🧵"
    hexcode = hexcode.replace("#", "")
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

def get_ha_snapshot(entity_id=None):
    """Fetches a camera snapshot from Home Assistant via the Supervisor API."""
    if not SUPERVISOR_TOKEN:
        return None, t("ha_no_api")
    
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    
    if not entity_id:
        # Try auto-discovery by looking for camera entities with 'bambu' in their ID
        try:
            r = requests.get(f"{HA_API_BASE}/states", headers=headers, timeout=10)
            if r.status_code == 200:
                states = r.json()
                for state in states:
                    eid = state.get("entity_id", "")
                    if eid.startswith("camera.") and "bambu" in eid.lower():
                        entity_id = eid
                        log.info(f"Auto-discovered camera: {entity_id}")
                        break
        except Exception as e:
            log.error(f"HA state discovery failed: {e}")
            
    if not entity_id:
        return None, t("cam_no_entity")

    try:
        url = f"{HA_API_BASE}/camera_proxy/{entity_id}"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.content, None
        return None, t("cam_error", error=f"HTTP {r.status_code}")
    except Exception as e:
        return None, t("cam_error", error=str(e))

def get_ha_light_state(entity_id=None):
    """Fetches the current state of a light entity from Home Assistant."""
    if not SUPERVISOR_TOKEN:
        return None, None
    
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    
    if not entity_id:
        # Auto-discover light entity
        try:
            r = requests.get(f"{HA_API_BASE}/states", headers=headers, timeout=10)
            if r.status_code == 200:
                states = r.json()
                for state in states:
                    eid = state.get("entity_id", "")
                    # Look for light domain containing 'bambu' and 'lamp' or 'light'
                    if eid.startswith("light.") and "bambu" in eid.lower() and ("lamp" in eid.lower() or "light" in eid.lower()):
                        entity_id = eid
                        log.info(f"Auto-discovered light: {entity_id}")
                        break
        except Exception as e:
            log.error(f"HA state discovery failed: {e}")
            
    if not entity_id:
        return None, None

    try:
        r = requests.get(f"{HA_API_BASE}/states/{entity_id}", headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json().get("state"), entity_id
    except Exception:
        pass
    return None, entity_id

def set_ha_light_state(entity_id, state):
    """Turns a light entity on or off."""
    if not SUPERVISOR_TOKEN:
        return t("ha_no_api")
    
    service = "turn_on" if state == "on" else "turn_off"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    
    try:
        r = requests.post(f"{HA_API_BASE}/services/light/{service}", 
                          headers=headers, json={"entity_id": entity_id}, timeout=10)
        if r.status_code in (200, 201):
            return None
        return f"HTTP {r.status_code}"
    except Exception as e:
        return str(e)

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
_mqtt_client = None          # set in main() after connection
_status_refresh = threading.Event()  # signals that fresh data arrived

def _format_minutes(mins):
    if mins <= 0:
        return "–"
    h, m = divmod(int(mins), 60)
    return f"{h}h {m}m" if h else f"{m}m"

def _format_duration(seconds):
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"

def request_pushall():
    """Ask the printer to push its full current state over MQTT."""
    global _mqtt_client
    if _mqtt_client is None:
        return
    topic   = f"device/{PRINTER_SERIAL}/request"
    payload = json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}})
    try:
        _mqtt_client.publish(topic, payload)
        log.info("Sent pushall request to printer")
    except Exception as e:
        log.error(f"pushall publish failed: {e}")

# ── Interactive Bot Commands ──────────────────────────────
@bot.message_handler(commands=['status'])
def send_status(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return

    # Request a live snapshot from the printer and wait up to 3 s for the reply
    _status_refresh.clear()
    request_pushall()
    _status_refresh.wait(timeout=3)

    # Fetch current light status from HA
    light_state, _ = get_ha_light_state(HA_LIGHT_ENTITY)
    light_str = t("light_on") if light_state == "on" else t("light_off")

    with _lock:
        if _state["printing"]:
            filename = _state["filename"] or "Unknown"
            pct      = _state["mc_percent"]
            weight   = _state["print_weight"]
            eta      = _format_minutes(_state["mc_remaining_time"])
            res = t("status_printing", filename=filename, pct=pct, weight=weight, eta=eta, light_status=light_str)
        else:
            res = t("status_idle", light_status=light_str)
    
    # Try to add a snapshot if camera is available
    img_bytes, error = get_ha_snapshot(HA_CAMERA_ENTITY)
    if img_bytes:
        try:
            bot.send_photo(message.chat.id, img_bytes, caption=res)
            return
        except Exception as e:
            log.error(f"Failed to send status photo: {e}")
            
    # Fallback to text if snapshot fails or is not enabled or no HA token
    bot.reply_to(message, res)

@bot.message_handler(commands=['spools', 'inventory'])
def send_spools(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    if not SPOOLMAN_URL or SPOOLMAN_URL == "http://":
        bot.reply_to(message, t("spoolman_not_enabled"))
        return
        
    try:
        r = requests.get(f"{SPOOLMAN_URL}/api/v1/spool", timeout=10)
        if r.status_code == 200:
            spools = r.json()
            if not spools:
                bot.reply_to(message, t("spools_title") + t("spools_empty"))
                return
                
            res = t("spools_title")
            for spool in spools:
                weight = round(spool.get("remaining_weight", 0))
                if weight <= 0: continue
                
                spool_id = spool.get("id")
                filament = spool.get("filament", {})
                hexcolor = filament.get("color_hex", "")
                emoji = color_to_emoji(hexcolor)
                brand = filament.get("vendor", {}).get("name", "Unknown")
                mat = filament.get("material", "Unknown")
                
                res += t("spools_item", id=spool_id, emoji=emoji, brand=brand, material=mat, grams=weight)
            
            if len(res) > 4000:
                res = res[:4000] + "\n... (too long)"
            bot.reply_to(message, res)
        else:
            bot.reply_to(message, f"❌ Spoolman Error: {r.status_code}")
    except Exception as e:
        log.error(f"Failed to fetch spools: {e}")
        bot.reply_to(message, f"❌ Spoolman Connection Error")

@bot.message_handler(commands=['spoolman'])
def handle_spoolman(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    if not SPOOLMAN_URL or SPOOLMAN_URL == "http://":
        bot.reply_to(message, t("spoolman_not_enabled"))
        return
        
    parts = message.text.split()
    if len(parts) >= 3:
        try:
            spool_id = int(parts[1])
            slot_input = int(parts[2])
            if 1 <= slot_input <= 4:
                slot_id_str = str(slot_input - 1)
                mapping = load_spoolman_mapping()
                mapping[slot_id_str] = spool_id
                save_spoolman_mapping(mapping)
                bot.reply_to(message, t("spoolman_success", sid=spool_id, slot=slot_input))
                return
        except ValueError:
            pass
    bot.reply_to(message, t("spoolman_fail"))

@bot.message_handler(commands=['ams'])
def send_ams(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    with _lock:
        if not _ams_state:
            bot.reply_to(message, "Waiting for AMS data from printer...")
            return

        mapping = load_spoolman_mapping()
        res = t("ams_title")
        
        for i in range(4):
            slot_str = str(i)
            slot_data = _ams_state.get(slot_str)
            
            # If Spoolman is mapped to this slot, query it
            if slot_str in mapping and SPOOLMAN_URL and SPOOLMAN_URL != "http://":
                spool_id = mapping[slot_str]
                try:
                    r = requests.get(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", timeout=5)
                    if r.status_code == 200:
                        spool = r.json()
                        rem_w = round(spool.get("remaining_weight", 0))
                        
                        filament = spool.get("filament", {})
                        hexcolor = filament.get("color_hex", "")
                        emoji = color_to_emoji(hexcolor)
                        brand = filament.get("vendor", {}).get("name", "Unknown")
                        mat = filament.get("material", "Unknown")
                        
                        res += t("ams_spoolman_slot", slot=i+1, emoji=emoji, brand=brand, material=mat, grams=rem_w)
                    else:
                        res += t("ams_spoolman_fail", slot=i+1, sid=spool_id)
                except Exception:
                    res += t("ams_spoolman_fail", slot=i+1, sid=spool_id)
            
            # If no Spoolman, fallback to basic Bambu native reporting
            else:
                if not slot_data or slot_data.get("type") in ("Unknown", "") or slot_data.get("remain", -1) < 0:
                    res += t("ams_empty", slot=i+1)
                else:
                    emoji = color_to_emoji(slot_data.get("color"))
                    typ = slot_data.get("type", "")
                    brand = slot_data.get("brand", "")
                    grams = slot_data.get("remain", 0) * 10
                    res += t("ams_slot", slot=i+1, emoji=emoji, type=typ, brand=brand, grams=grams)

    bot.reply_to(message, res)

@bot.message_handler(commands=['help'])
def send_help(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    bot.reply_to(message, t("help"), parse_mode="Markdown")

@bot.message_handler(commands=['map'])
def handle_map(message):
    """Alias for /spoolman with argument order: /map <slot> <spool_id>"""
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    if not SPOOLMAN_URL or SPOOLMAN_URL == "http://":
        bot.reply_to(message, t("spoolman_not_enabled"))
        return

    parts = message.text.split()
    if len(parts) >= 3:
        try:
            slot_input = int(parts[1])
            spool_id   = int(parts[2])
            if 1 <= slot_input <= 4:
                slot_id_str = str(slot_input - 1)
                mapping = load_spoolman_mapping()
                mapping[slot_id_str] = spool_id
                save_spoolman_mapping(mapping)
                bot.reply_to(message, t("spoolman_success", sid=spool_id, slot=slot_input))
                return
        except ValueError:
            pass
    bot.reply_to(message, t("spoolman_fail"))

@bot.message_handler(commands=['cam', 'snapshot'])
def handle_cam(message):
    """Fetches and sends a live camera snapshot from Home Assistant."""
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    
    # Notify user we are working on it (chat action "upload_photo")
    try:
        bot.send_chat_action(message.chat.id, 'upload_photo')
    except Exception:
        pass
    
    img_bytes, error = get_ha_snapshot(HA_CAMERA_ENTITY)
    if error:
        bot.reply_to(message, error)
        return
        
    try:
        bot.send_photo(message.chat.id, img_bytes, caption=f"📸 {datetime.now().strftime('%H:%M:%S')}")
    except Exception as e:
        log.error(f"Failed to send photo: {e}")
        bot.reply_to(message, f"❌ Failed to send photo: {e}")

@bot.message_handler(commands=['light', 'lamp'])
def handle_light(message):
    """Toggles the printer light in Home Assistant."""
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    
    current_state, entity_id = get_ha_light_state(HA_LIGHT_ENTITY)
    if not entity_id:
        bot.reply_to(message, t("light_no_entity"))
        return
        
    # Toggle logic: if 'on' -> turn 'off', else -> turn 'on'
    new_state = "off" if current_state == "on" else "on"
    error = set_ha_light_state(entity_id, new_state)
    
    if error:
        bot.reply_to(message, t("light_fail", error=error))
    else:
        msg = t("light_on") if new_state == "on" else t("light_off")
        bot.reply_to(message, msg)

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
        # Signal any waiting /status command that fresh data has arrived
        _status_refresh.set()

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
            
            # Spoolman auto-deduction
            if weight_used > 0 and tray != 255:
                mapping = load_spoolman_mapping()
                str_tray = str(tray)
                if SPOOLMAN_URL and SPOOLMAN_URL != "http://" and str_tray in mapping:
                    spool_id = mapping[str_tray]
                    try:
                        r = requests.put(
                            f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}/use",
                            json={"use_weight": weight_used},
                            timeout=10
                        )
                        if r.status_code == 200:
                            log.info(f"Subtracted {weight_used}g from Spoolman ID {spool_id} successfully.")
                        else:
                            log.error(f"Failed to subtract from Spoolman API: {r.status_code} {r.text}")
                    except Exception as e:
                        log.error(f"Spoolman API subtraction failed due to exception: {e}")

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

                # Low Filament alert (only for native 10% Bambu spools for speed)
                mapping = load_spoolman_mapping()
                if str(slot_id) not in mapping:
                    check_grams = 9999
                    if 0 <= remain_pct <= 100:
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
    global _mqtt_client
    client = None
    try:
        sock = socket.create_connection((PRINTER_IP, 8883), timeout=5)
        sock.close()
        client = connect_local_mqtt()
        log.info("Using LOCAL MQTT")
    except Exception:
        log.info("Local MQTT not reachable – trying Cloud MQTT...")
        client = connect_cloud_mqtt()
    _mqtt_client = client

    if not client:
        sys.exit("Could not connect to printer via local or cloud MQTT.")

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        send_telegram(t("disconnected"))
        log.info("Stopped by user.")

if __name__ == "__main__":
    main()
