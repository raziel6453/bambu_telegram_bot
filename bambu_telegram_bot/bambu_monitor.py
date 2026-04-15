#!/usr/bin/env python3
"""
Bambu Lab Telegram Monitor for Home Assistant Add-on
Sends print start/finish notifications, progress updates, and Spoolman filament tracking.
"""

import os, sys, time, json, threading, logging, requests, ssl, yaml, html
from datetime import datetime
VERSION = "2026-04-16.v2"

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Missing paho-mqtt. Run: pip install paho-mqtt")
    
try:
    import telebot
except ImportError:
    sys.exit("Missing pyTelegramBotAPI. Run: pip install pyTelegramBotAPI")

# ── Load config (Hybrid: HA Add-on or Standalone) ────────
def load_config():
    options_path = "/data/options.json"
    local_config = "config.yaml"
    local_options = "options.json"

    # 1. Try HA Add-on path
    if os.path.exists(options_path):
        try:
            with open(options_path) as f:
                return json.load(f), "/data"
        except Exception as e:
            logging.error(f"Failed to load {options_path}: {e}")

    # 2. Try local config.yaml (standalone fallback)
    if os.path.exists(local_config):
        try:
            with open(local_config) as f:
                cfg = yaml.safe_load(f)
                if isinstance(cfg, dict) and "options" in cfg:
                    return cfg["options"], "."
        except Exception as e:
            logging.error(f"Failed to load {local_config}: {e}")

    # 3. Try local options.json (alternative standalone)
    if os.path.exists(local_options):
        try:
            with open(local_options) as f:
                return json.load(f), "."
        except Exception as e:
            logging.error(f"Failed to load {local_options}: {e}")

    return {}, "."

options, data_dir = load_config()
SPOOLMAN_MAPPING_FILE = os.path.join(data_dir, "spoolman_mapping.json")

PRINTER_IP       = options.get("printer_ip", "")
PRINTER_SERIAL   = options.get("printer_serial", "")
PRINTER_PASSWORD = options.get("printer_password", "")
BAMBU_USERNAME   = options.get("bambu_username", "")
BAMBU_PASSWORD   = options.get("bambu_password", "")
TELEGRAM_TOKEN   = options.get("telegram_token", "")
TELEGRAM_CHAT_ID = options.get("telegram_chat_id", "")
LANGUAGE         = options.get("language", "he")
SPOOLMAN_URL      = options.get("spoolman_url", "").strip().rstrip("/")
HA_CAMERA_ENTITY  = options.get("ha_camera_entity", "").strip()
HA_LIGHT_ENTITY   = options.get("ha_light_entity", "").strip()
HA_WEIGHT_ENTITY  = options.get("ha_weight_entity", "").strip()
LOW_STOCK_THRESHOLD = int(options.get("low_stock_threshold", 100))
PRINT_HISTORY_FILE  = os.path.join(data_dir, "print_history.json")

# Discovered weight entity (populated at runtime if not configured)
_discovered_weight_entity = None

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
HA_API_BASE      = "http://supervisor/core/api"
HA_API_AVAILABLE = SUPERVISOR_TOKEN is not None

if "YOUR_" in PRINTER_IP or not PRINTER_IP:
    if not options:
        sys.exit("❌ Error: No configuration found. Please clarify if you are running as an HA Add-on or provide a config.yaml file.")
    sys.exit("❌ Error: Please fill in the configuration with your printer and Telegram details.")

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
        "print_start":    "🖨️ ההדפסה התחילה!\nקובץ: {filename}\nמשקל צפוי: {weight}\nמשך משוער: {eta} (יגמר בסביבות {finish})\n",
        "print_done":     "✅ ההדפסה הסתיימה!\nקובץ: {filename}\nמשקל חוט שהישתמש: {weight}\nסה\"כ זמן: {duration}",
        "print_failed":   "❌ ההדפסה נכשלה.\nקובץ: {filename}",
        "progress":       "📊 התקדמות: {pct}%\nנותר: {remaining} (יגמר בסביבות {finish})",
        "low_filament":   "⚠️ נגמר חוט! סלוט {slot} נותר בערך: {grams}",
        "connected":      "✅ הבוט מחובר ועובד!",
        "disconnected":   "🔴 הפסקתי עבודה.",
        "status_printing": "🖨️ מדפיס כעת...\nקובץ: {filename}\nמשקל צפוי להדפסה: {weight}\nנותר בספול: {spool_rem}\nהתקדמות: {pct}%\nזמן נותר: {eta}\n🏹 יגמר בסביבות: {finish}\n{light_status}",
        "status_idle":    "💤 המדפסת כרגע במצב המתנה.\n{light_status}",
        "ams_title":      "📦 סטטוס מערכת ה-AMS:\n",
        "ams_slot":       "סלוט {slot}: {emoji} סוג: {type} ({brand}) - נותר משוער: {grams}\n",
        "ams_spoolman_slot": "סלוט {slot}: {emoji} סוג: {brand} {material} - נותר: {grams} (Spoolman)\n",
        "ams_spoolman_fail": "סלוט {slot}: ❌ שגיאת חיבור ל-Spoolman (ID {sid})\n",
        "ams_empty":      "סלוט {slot}: ❌ ריק\n",
        "spoolman_success": "✅ מאגר Spoolman ID {sid} שויך לסלוט {slot} בהצלחה. החסרה אוטומטית הופעלה.",
        "spoolman_fail": "❌ שגיאה: יש להקליד במבנה: /spoolman <מזהה_ספול> <סלוט_1-4>",
        "spoolman_not_enabled": "❌ Spoolman לא הוגדר בהגדרות Addon של Home Assistant.",
        "spools_title":   "📦 מאגר Spoolman:\n",
        "spools_item":    "ID: #{id} | {emoji} {brand} {material} | משקל נותר: {grams}\n",
        "spools_empty":   "המאגר ריק.\n",
        "help": (
            "🖨️ *Bambu Telegram Monitor — פקודות:*\n\n"
            "📊 *סטטוס*\n"
            "/status — סטטוס נוכחי + צילום\n"
            "/ams — סטטוס מגשי ה-AMS\n"
            "/history — 10 הדפסות אחרונות\n\n"
            "🎥 *מצלמה*\n"
            "/cam — צילום חי (מצריך HA)\n\n"
            "⚡ *שליטה מרחוק*\n"
            "/pause — עצירת הדפסה\n"
            "/resume — המשך הדפסה\n"
            "/cancel — ביטול הדפסה (יבקש אישור)\n"
            "📦 *Spoolman*\n"
            "/spools — רשימת ספולים\n"
            "/map <סלוט> <id> — שיוך סלוט לספולים קיים\n"
            "/set <סלוט> <מותג> <סוג> — יצירת ספול חדש ושיוך אוטומטי\n\n"
            "🔦 *כלים*\n"
            "/light — הדלקה/כיבוי מנורה\n"
            "/debug — נתוני MQTT גולמיים\n"
            "/help — תפריט זה"
        ),
        "cam_error":      "❌ שגיאה במשיכת תמונה מ-Home Assistant: {error}",
        "cam_no_entity":  "❌ לא הוגדרה מצלמה ולא נמצאה מצלמת Bambu אוטומטית ב-Home Assistant.",
        "light_on":       "💡 המנורה דולקת",
        "light_off":      "🌑 המנורה כבויה",
        "light_fail":     "❌ שגיאה בשליטה על המנורה: {error}",
        "light_no_entity": "❌ לא הוגדר גוף תאורה ולא נמצא גוף תאורה אוטומטי של Bambu ב-Home Assistant.",
        "ha_no_api":      "❌ הבוט לא רץ כ-Add-on עם הרשאות API של Home Assistant.",
        "ams_new_filament": "🆕 חוט חדש זוהה בסלוט {slot}!\nצבע: {emoji} | סוג: {ftype}\n\nלרישום ב-Spoolman: /set {slot} <מותג> <סוג>\nלדוגמא: /set {slot} Bambu PLA",
        "setfilament_ok": "✅ ספול חדש נוצר ב-Spoolman ושויך לסלוט {slot}.",
        "setfilament_fail": "❌ שגיאה ביצירת ספול. ודא ש-Spoolman מוגדר.",
        "setfilament_usage": "❌ שימוש: /set <סלוט> <מותג> <סוג>\nלדוגמא: /set 1 Bambu PLA",
        "ctrl_pause":     "⏸️ הפסקת הדפסה בבקשה...",
        "ctrl_resume":    "▶️ המשך הדפסה בבקשה...",
        "ctrl_cancel_confirm": "⚠️ בטוח שברצונך לבטל את ההדפסה?",
        "ctrl_cancel_yes": "❌ מבטל את ההדפסה...",
        "ctrl_cancel_no":  "✅ ביטול בוטל. הדפסה ממשיכת.",
        "ctrl_not_printing": "❌ אין הדפסה פעילה כעת.",
        "low_stock":      "⚠️ ספול #{sid} ({label}) על סיום — נותר רק {grams}g!",
        "history_title":  "💻 היסטוריית הדפסות (10 אחרונות):\n",
        "history_item":   "🗓 {date} | {filename} | {duration} | {grams}\n",
        "history_empty":  "אין הדפסות שמורות שעד."
    },
    "en": {
        "print_start":    "🖨️ Print started!\nFile: {filename}\nEst. filament: {weight}\nETA: {eta} (finishes at {finish})",
        "print_done":     "✅ Print finished!\nFile: {filename}\nFilament used: {weight}\nTotal time: {duration}",
        "print_failed":   "❌ Print failed.\nFile: {filename}",
        "progress":       "📊 Progress: {pct}%\nRemaining: {remaining} (finishes at {finish})",
        "low_filament":   "⚠️ Low filament! Slot {slot}: ~{grams} left",
        "connected":      "✅ Bot connected and running!",
        "disconnected":   "🔴 Bot stopped.",
        "status_printing": "🖨️ Currently Printing...\nFile: {filename}\nEst. filament: {weight}\nSpool remaining: {spool_rem}\nProgress: {pct}%\nTime remaining: {eta}\n🏹 Finishes at: {finish}\n{light_status}",
        "status_idle":    "💤 Printer is currently idle.\n{light_status}",
        "ams_title":      "📦 AMS Status:\n",
        "ams_slot":       "Slot {slot}: {emoji} Type: {type} ({brand}) - Estimated: {grams}\n",
        "ams_spoolman_slot": "Slot {slot}: {emoji} Type: {brand} {material} - Remaining: {grams} (Spoolman)\n",
        "ams_spoolman_fail": "Slot {slot}: ❌ Spoolman Connection Error (ID {sid})\n",
        "ams_empty":      "Slot {slot}: ❌ Empty\n",
        "spoolman_success": "✅ Spoolman ID {sid} mapped to Slot {slot}. Auto-subtraction enabled.",
        "spoolman_fail": "❌ Error: Use format: /spoolman <spool_id> <slot_1-4>",
        "spoolman_not_enabled": "❌ Spoolman URL is not configured in Home Assistant Add-on options.",
        "spools_title":   "📦 Spoolman Inventory:\n",
        "spools_item":    "ID: #{id} | {emoji} {brand} {material} | Weight: {grams}\n",
        "spools_empty":   "Inventory is empty.\n",
        "help": (
            "🖨️ *Bambu Telegram Monitor — Commands:*\n\n"
            "📊 *Status*\n"
            "/status — Current status + camera snapshot\n"
            "/ams — AMS slot status\n"
            "/history — Last 10 completed prints\n\n"
            "🎥 *Camera*\n"
            "/cam — Live snapshot (requires HA)\n\n"
            "⚡ *Remote Control*\n"
            "/pause — Pause current print\n"
            "/resume — Resume paused print\n"
            "/cancel — Cancel print (asks confirmation)\n"
            "📦 *Spoolman*\n"
            "/spools — List inventory\n"
            "/map <slot> <id> — Map slot to existing spool\n"
            "/set <slot> <brand> <material> — Create new spool \\& map slot\n\n"
            "🔦 *Tools*\n"
            "/light — Toggle printer lamp\n"
            "/debug — Raw MQTT data for troubleshooting\n"
            "/help — Show this menu"
        ),
        "cam_error":      "❌ Error fetching snapshot from Home Assistant: {error}",
        "cam_no_entity":  "❌ No camera entity configured and no Bambu camera discovered autoamtically in Home Assistant.",
        "light_on":       "💡 Light is ON",
        "light_off":      "🌑 Light is OFF",
        "light_fail":     "❌ Error controlling the light: {error}",
        "light_no_entity": "❌ No light entity configured and no Bambu light discovered automatically in Home Assistant.",
        "ha_no_api":      "❌ Bot is not running as an Add-on with Home Assistant API access.",
        "ams_new_filament": "🆕 New filament detected in Slot {slot}!\nColor: {emoji} | Type: {ftype}\n\nTo register in Spoolman: /set {slot} <brand> <material>\nExample: /set {slot} Bambu PLA",
        "setfilament_ok": "✅ New spool created in Spoolman and mapped to Slot {slot}.",
        "setfilament_fail": "❌ Failed to create spool. Make sure Spoolman is configured.",
        "setfilament_usage": "❌ Usage: /set <slot> <brand> <material>\nExample: /set 1 Bambu PLA",
        "ctrl_pause":     "⏸️ Pausing print...",
        "ctrl_resume":    "▶️ Resuming print...",
        "ctrl_cancel_confirm": "⚠️ Are you sure you want to cancel the print?",
        "ctrl_cancel_yes": "❌ Cancelling print...",
        "ctrl_cancel_no":  "✅ Cancel aborted. Print continues.",
        "ctrl_not_printing": "❌ No print is currently active.",
        "low_stock":      "⚠️ Spool #{sid} ({label}) is running low — only {grams}g left!",
        "history_title":  "💻 Print History (last 10):\n",
        "history_item":   "🗓 {date} | {filename} | {duration} | {grams}\n",
        "history_empty":  "No prints saved yet."
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

def send_telegram_with_photo(text):
    """Send a message with a live camera snapshot if available, else plain text."""
    if HA_API_AVAILABLE:
        try:
            img_bytes, _ = get_ha_snapshot(HA_CAMERA_ENTITY)
            if img_bytes:
                bot.send_photo(TELEGRAM_CHAT_ID, img_bytes, caption=text)
                return
        except Exception as e:
            log.warning(f"Photo send failed, falling back to text: {e}")
    send_telegram(text)

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

def get_ha_sensor_state(entity_id):
    """Fetches the state of a specific sensor from HA."""
    if not SUPERVISOR_TOKEN or not entity_id:
        return None
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    try:
        r = requests.get(f"{HA_API_BASE}/states/{entity_id}", headers=headers, timeout=5)
        if r.status_code == 200:
            val = r.json().get("state")
            if val not in ("unknown", "unavailable", "None", ""):
                return val
    except Exception as e:
        log.error(f"Failed to fetch {entity_id} from HA: {e}")
    return None

def discover_ha_weight_entity():
    """Scan HA entities to find a Bambu weight sensor automatically."""
    global _discovered_weight_entity
    if not SUPERVISOR_TOKEN:
        return None
    if _discovered_weight_entity:
        return _discovered_weight_entity
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    try:
        r = requests.get(f"{HA_API_BASE}/states", headers=headers, timeout=10)
        if r.status_code == 200:
            for state in r.json():
                eid  = state.get("entity_id", "")
                sval = state.get("state", "")
                # Look for entities ending in print_weight, or containing bambu+weight
                if (eid.startswith("sensor.") 
                        and ("print_weight" in eid.lower() or ("bambu" in eid.lower() and "weight" in eid.lower()))
                        and sval not in ("", "unknown", "unavailable", "None")):
                    _discovered_weight_entity = eid
                    log.info(f"Auto-discovered HA weight entity: {eid} = {sval}")
                    return eid
    except Exception as e:
        log.error(f"Weight entity discovery failed: {e}")
    return None

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
    "print_weight": 0.0,
    "tray_now": 255,
    "_raw_print": {},  # Internal: Last raw print object for debugging
    "spool_start_weight": None,   # Spoolman weight at print start (for filament estimate)
}
_ams_state = {}
_ams_fingerprints = {}  # slot_id -> "color:type" string to detect new spool loads
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


def _smart_remaining():
    """Calculate remaining minutes using HA sensors or mathematical extrapolation."""
    # 1. Supreme Priority: Direct Home Assistant Sensor (if available)
    if HA_API_AVAILABLE and SUPERVISOR_TOKEN:
        weight_entity = HA_WEIGHT_ENTITY or _discovered_weight_entity
        if weight_entity and "_print_weight" in weight_entity:
            ha_prefix = weight_entity.replace("_print_weight", "")
            raw_rem = get_ha_sensor_state(f"{ha_prefix}_remaining_time")
            if raw_rem and str(raw_rem).isdigit():
                return int(raw_rem)

    # 2. Primary: Mathematical Extrapolation
    mqtt_rem = _state.get("mc_remaining_time", 0)
    pct      = _state.get("mc_percent", 0)
    start    = _state.get("start_time")

    if start and pct and pct > 5:
        elapsed_mins = (datetime.now() - start).total_seconds() / 60.0
        remaining = elapsed_mins * (100 - pct) / pct
        return max(0, int(remaining))

    # 3. Fallback: Trust the MQTT value early in the print
    if mqtt_rem > 0:
        return mqtt_rem

    return 0

def _finish_time(remaining_mins):
    """Calculates exactly when the print will finish."""
    # 1. Supreme Priority: Direct Home Assistant Sensor (if available)
    if HA_API_AVAILABLE and SUPERVISOR_TOKEN:
        weight_entity = HA_WEIGHT_ENTITY or _discovered_weight_entity
        if weight_entity and "_print_weight" in weight_entity:
            ha_prefix = weight_entity.replace("_print_weight", "")
            raw_end = get_ha_sensor_state(f"{ha_prefix}_end_time")
            if raw_end and "T" in str(raw_end):
                try:
                    from datetime import timezone, timedelta
                    jerusalem = timezone(timedelta(hours=3))
                    dt = datetime.fromisoformat(str(raw_end).replace('Z', '+00:00'))
                    return dt.astimezone(jerusalem).strftime("%H:%M")
                except Exception:
                    pass

    # 2. Local Fallback
    if remaining_mins <= 0:
        return "–"
    try:
        from datetime import timezone, timedelta
        jerusalem = timezone(timedelta(hours=3))
        finish = datetime.now(jerusalem) + timedelta(minutes=int(remaining_mins))
        return finish.strftime("%H:%M")
    except Exception:
        return "–"

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

def send_printer_command(cmd_dict):
    """Publish a print control command to the printer via MQTT."""
    global _mqtt_client
    if _mqtt_client is None:
        log.warning("Cannot send command: MQTT not connected")
        return False
    topic   = f"device/{PRINTER_SERIAL}/request"
    payload = json.dumps({"print": {"sequence_id": "0", **cmd_dict}})
    try:
        _mqtt_client.publish(topic, payload)
        log.info(f"Sent printer command: {cmd_dict}")
        return True
    except Exception as e:
        log.error(f"Printer command publish failed: {e}")
        return False

def append_print_history(entry):
    """Append a completed print record to the history JSON file."""
    try:
        history = []
        if os.path.exists(PRINT_HISTORY_FILE):
            with open(PRINT_HISTORY_FILE, 'r') as f:
                history = json.load(f)
        history.append(entry)
        history = history[-100:]  # keep last 100
        with open(PRINT_HISTORY_FILE, 'w') as f:
            json.dump(history, f)
    except Exception as e:
        log.error(f"Failed to save print history: {e}")

def check_spoolman_low_stock(spool_id):
    """Alert if a Spoolman spool has fallen below LOW_STOCK_THRESHOLD."""
    if not SPOOLMAN_URL or not spool_id:
        return
    try:
        r = requests.get(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", timeout=5)
        if r.status_code == 200:
            data  = r.json()
            rem   = data.get("remaining_weight", 9999)
            if rem < LOW_STOCK_THRESHOLD:
                fil   = data.get("filament", {})
                label = f"{fil.get('brand','')} {fil.get('name','')} {fil.get('material','')}".strip()
                send_telegram(t("low_stock", sid=spool_id, label=label, grams=f"{float(rem):.1f}"))
                log.info(f"Low stock alert: spool {spool_id} has {rem}g remaining")
    except Exception as e:
        log.error(f"Low stock check failed: {e}")

# ── Interactive Bot Commands ──────────────────────────────
@bot.message_handler(commands=['status'])
def send_status(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return

    try:
        # Request a live snapshot from the printer and wait up to 3 s for the reply
        _status_refresh.clear()
        request_pushall()
        _status_refresh.wait(timeout=3)

        # Fetch current light status from HA if available
        light_str = ""
        if HA_API_AVAILABLE:
            light_state, _ = get_ha_light_state(HA_LIGHT_ENTITY)
            light_str = "\n" + (t("light_on") if light_state == "on" else t("light_off"))

        # Try to fetch weight from HA sensor/entity (configured or auto-discovered)
        ha_weight = None
        ha_prefix = None
        if HA_API_AVAILABLE:
            weight_entity = HA_WEIGHT_ENTITY or discover_ha_weight_entity()
            if weight_entity:
                if "_print_weight" in weight_entity:
                    ha_prefix = weight_entity.replace("_print_weight", "")
                try:
                    val = get_ha_sensor_state(weight_entity)
                    if val:
                        cleaned_val = str(val).lower().replace('g', '').strip()
                        ha_weight = round(float(cleaned_val), 1)
                        log.info(f"Fetched weight from HA entity {weight_entity}: {ha_weight}g")
                except Exception as e:
                    log.error(f"Failed to fetch weight from HA sensor: {e}")

        with _lock:
            if _state["printing"]:
                filename = _state["filename"] or "Unknown"
                pct      = _state["mc_percent"]
                tray     = _state.get("tray_now", 255)

                # ── Weight (filament used estimate) ───────────────
                weight_str = "N/A"

                if ha_weight is not None and ha_weight > 0:
                    weight_str = f"{ha_weight:.1f}g"
                elif _state.get("print_weight", 0.0) > 0:
                    weight_str = f"{float(_state['print_weight']):.1f}g"
                elif SPOOLMAN_URL and tray != 255:
                    mapping    = load_spoolman_mapping()
                    spool_id   = mapping.get(str(tray))
                    sw_start   = _state.get("spool_start_weight")
                    if spool_id and sw_start:
                        try:
                            r2 = requests.get(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", timeout=5)
                            if r2.status_code == 200:
                                cur_rem = r2.json().get("remaining_weight")
                                if cur_rem is not None:
                                    used = float(sw_start) - float(cur_rem)
                                    if used > 0:
                                        weight_str = f"~{used:.1f}g"
                        except Exception:
                            pass

                # ── Spool remaining ───────────────────────────────
                spool_rem_str = "N/A"
                if SPOOLMAN_URL and tray != 255:
                    mapping  = load_spoolman_mapping()
                    spool_id = mapping.get(str(tray))
                    if spool_id:
                        try:
                            r3 = requests.get(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", timeout=5)
                            if r3.status_code == 200:
                                rem = r3.json().get("remaining_weight")
                                if rem is not None:
                                    spool_rem_str = f"{float(rem):.1f}g"
                        except Exception:
                            pass

                # ── Remaining time (smart: HA > MQTT/Extrapolated) ──
                rem_mins = _smart_remaining()
                eta      = _format_minutes(rem_mins)
                finish   = _finish_time(rem_mins)

                res = t("status_printing", filename=filename, pct=pct, weight=weight_str,
                        spool_rem=spool_rem_str, eta=eta, finish=finish, light_status=light_str)
            else:
                res = t("status_idle", light_status=light_str)
        
        # Try to add a snapshot if HA is available
        if HA_API_AVAILABLE:
            img_bytes, error = get_ha_snapshot(HA_CAMERA_ENTITY)
            if img_bytes:
                try:
                    bot.send_photo(message.chat.id, img_bytes, caption=res)
                    return
                except Exception as e:
                    log.error(f"Failed to send status photo: {e}")
                
        # Fallback to text if snapshot fails or is not enabled or no HA token
        bot.reply_to(message, res)

    except Exception as e:
        import traceback
        err_msg = f"Crash in /status:\n{e}\n\n{traceback.format_exc()}"
        bot.reply_to(message, err_msg)

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

@bot.message_handler(commands=['set'])
def handle_set(message):
    """Create a new spool in Spoolman and map it to an AMS slot."""
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return

    if not SPOOLMAN_URL or SPOOLMAN_URL == "http://":
        bot.reply_to(message, t("spoolman_not_enabled"))
        return

    parts = message.text.strip().split(maxsplit=3)
    # /set <slot> <brand> <material>
    if len(parts) < 4:
        bot.reply_to(message, t("setfilament_usage"))
        return

    try:
        slot_num = int(parts[1])          # 1-based from user
        brand    = parts[2]
        material = parts[3]
        slot_id  = str(slot_num - 1)     # 0-based internally

        if slot_num < 1 or slot_num > 4:
            bot.reply_to(message, t("setfilament_usage"))
            return

        # Pull color from current AMS state for this slot
        ams_info = _ams_state.get(slot_id, {})
        color    = ams_info.get("color", "FFFFFF")

        # Create spool in Spoolman
        payload = {
            "filament": {"name": f"{brand} {material}", "material": material, "color_hex": color},
            "remaining_weight": 1000,
        }
        r = requests.post(f"{SPOOLMAN_URL}/api/v1/spool", json=payload, timeout=10)
        if r.status_code not in (200, 201):
            log.error(f"Spoolman create spool failed: {r.status_code} {r.text}")
            bot.reply_to(message, t("setfilament_fail"))
            return

        spool_id = r.json().get("id")
        if not spool_id:
            bot.reply_to(message, t("setfilament_fail"))
            return

        # Map slot to the new spool
        mapping = load_spoolman_mapping()
        mapping[slot_id] = spool_id
        save_spoolman_mapping(mapping)

        log.info(f"Created Spoolman spool {spool_id} ({brand} {material}) -> slot {slot_id}")
        bot.reply_to(message, t("setfilament_ok", slot=slot_num))

    except (ValueError, IndexError):
        bot.reply_to(message, t("setfilament_usage"))
    except Exception as e:
        log.error(f"set command error: {e}")
        bot.reply_to(message, t("setfilament_fail"))

# ── Remote Control ────────────────────────────────────────
@bot.message_handler(commands=['pause'])
def handle_pause(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    with _lock:
        if not _state["printing"]:
            bot.reply_to(message, t("ctrl_not_printing")); return
    bot.reply_to(message, t("ctrl_pause"))
    send_printer_command({"command": "pause"})

@bot.message_handler(commands=['resume'])
def handle_resume(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    bot.reply_to(message, t("ctrl_resume"))
    send_printer_command({"command": "resume"})

@bot.message_handler(commands=['cancel'])
def handle_cancel(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    with _lock:
        if not _state["printing"]:
            bot.reply_to(message, t("ctrl_not_printing")); return
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("✅ Yes, cancel", callback_data="cancel_yes"),
        telebot.types.InlineKeyboardButton("❌ No, keep going", callback_data="cancel_no"),
    )
    bot.reply_to(message, t("ctrl_cancel_confirm"), reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ("cancel_yes", "cancel_no"))
def handle_cancel_callback(call):
    if str(call.message.chat.id) != str(TELEGRAM_CHAT_ID): return
    bot.answer_callback_query(call.id)
    if call.data == "cancel_yes":
        bot.edit_message_text(t("ctrl_cancel_yes"),
                              call.message.chat.id, call.message.message_id)
        send_printer_command({"command": "stop"})
    else:
        bot.edit_message_text(t("ctrl_cancel_no"),
                              call.message.chat.id, call.message.message_id)

# ── Print History ─────────────────────────────────────────
@bot.message_handler(commands=['history'])
def handle_history(message):
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    try:
        if not os.path.exists(PRINT_HISTORY_FILE):
            bot.reply_to(message, t("history_empty")); return
        with open(PRINT_HISTORY_FILE, 'r') as f:
            history = json.load(f)
        if not history:
            bot.reply_to(message, t("history_empty")); return
        last10 = history[-10:][::-1]  # newest first
        res = t("history_title")
        for entry in last10:
            grams = f"{float(entry.get('grams', 0)):.1f}g" if entry.get('grams') else "–"
            res += t("history_item",
                     date=entry.get("date", "?"),
                     filename=entry.get("filename", "?")[:20],
                     duration=entry.get("duration", "?"),
                     grams=grams)
        bot.reply_to(message, res)
    except Exception as e:
        log.error(f"history command error: {e}")
        bot.reply_to(message, "❌ Error reading history.")

@bot.message_handler(commands=['cam', 'snapshot'])
def handle_cam(message):
    """Fetches and sends a live camera snapshot from Home Assistant."""
    if str(message.chat.id) != str(TELEGRAM_CHAT_ID): return
    
    if not HA_API_AVAILABLE:
        bot.reply_to(message, t("ha_no_api"))
        return

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
    
    if not HA_API_AVAILABLE:
        bot.reply_to(message, t("ha_no_api"))
        return

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

@bot.message_handler(commands=['debug'])
def handle_debug(message):
    """Sends current state and last raw MQTT print object to Telegram."""
    log.info(f"Debug command received from chat_id: {message.chat.id}")
    if str(message.chat.id).strip() != str(TELEGRAM_CHAT_ID).strip():
        log.warning(f"Unauthorized debug attempt from {message.chat.id}")
        return
    
    try:
        with _lock:
            state_copy = dict(_state)
            # Separate the heavy raw_print object
            raw_print = state_copy.pop("_raw_print", {})
            
            state_json = json.dumps(state_copy, indent=2, default=str)
            raw_json = json.dumps(raw_print, indent=2, default=str)
        
        # Use HTML for better reliability with raw JSON characters
        debug_msg = (
            "<b>🔍 Debug Info</b>\n\n"
            "<b>State:</b>\n<pre>" + html.escape(state_json) + "</pre>\n\n"
            "<b>Last Raw MQTT:</b>\n<pre>" + html.escape(raw_json) + "</pre>"
        )
        
        if len(debug_msg) > 4000:
            debug_msg = debug_msg[:4000] + "\n... (truncated)"
        
        bot.reply_to(message, debug_msg, parse_mode="HTML")
    except Exception as e:
        log.error(f"Debug command failed: {e}")
        bot.reply_to(message, f"❌ Debug failed: {e}")

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

        # Store raw print for debugging
        _state["_raw_print"] = print_data

        # Process Print State & Weight Detection
        gcode_state     = print_data.get("gcode_state", _state["gcode_state"])
        mc_percent      = print_data.get("mc_percent", _state["mc_percent"])
        mc_remaining    = print_data.get("mc_remaining_time", _state["mc_remaining_time"])
        filename        = print_data.get("subtask_name", _state["filename"]) or _state["filename"]
        
        # Try multiple fields for weight (grams or milligrams fallbacks)
        new_weight = _state["print_weight"]
        
        # 1. Direct grams fields
        w_grams = print_data.get("subtask_weight") or print_data.get("print_weight")
        if w_grams is not None:
            try:
                # Clean 'g' and convert to float
                cleaned_w = str(w_grams).lower().replace('g', '').strip()
                parsed_w = float(cleaned_w)
                if parsed_w > 0:
                    new_weight = round(parsed_w, 1)
            except:
                pass
        
        # Fallback to milligrams if still 0
        if new_weight <= 0:
            w_mg = print_data.get("total_weight") or print_data.get("weight")
            if w_mg is not None:
                try:
                    parsed_mg = float(str(w_mg).lower().replace('g','').strip())
                    if parsed_mg > 0:
                        new_weight = round(parsed_mg / 1000.0, 1)
                except:
                    pass

        weight_estimate = new_weight

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
            _state["last_milestone"] = 0
            
            # If we connected mid-print, backdate the start_time so extrapolation doesn't think we just started at 0 minutes.
            if 1 < mc_percent < 100 and mc_remaining > 0:
                total_est_mins = mc_remaining / (1 - (mc_percent / 100.0))
                elapsed_mins = total_est_mins * (mc_percent / 100.0)
                _state["start_time"] = datetime.now() - timedelta(minutes=elapsed_mins)
            else:
                _state["start_time"] = datetime.now()

            
            # Optionally capture spool start weight
            tray = _state.get("tray_now", 255)
            if SPOOLMAN_URL and tray != 255:
                mapping = load_spoolman_mapping()
                spool_id = mapping.get(str(tray))
                if spool_id:
                    try:
                        r = requests.get(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", timeout=3)
                        if r.status_code == 200:
                            _state["spool_start_weight"] = r.json().get("remaining_weight")
                    except Exception:
                        pass
                        
            rem_mins = _smart_remaining()
            eta = _format_minutes(rem_mins)
            log.info(f"Print started: {filename}")
            
            # Format weight for notification
            w_disp = f"{float(_state['print_weight']):.1f}g"
            
            send_telegram_with_photo(t("print_start", filename=filename or "–", weight=w_disp, eta=eta, finish=_finish_time(rem_mins)))

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
            weight_str = f"{float(weight_used):.1f}g"
            send_telegram_with_photo(t("print_done", filename=filename or "–", weight=weight_str, duration=dur))

            # Save to print history
            append_print_history({
                "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
                "filename": filename or "Unknown",
                "duration": dur or "–",
                "grams":    weight_used,
                "slot":     tray,
            })

            # Spoolman low stock check
            if tray != 255:
                mapping = load_spoolman_mapping()
                sid = mapping.get(str(tray))
                if sid:
                    threading.Thread(target=check_spoolman_low_stock, args=(sid,), daemon=True).start()

        # Print failed / cancelled
        elif gcode_state in ("FAILED", "PAUSE") and was_printing and gcode_state == "FAILED":
            _state["printing"] = False
            log.info(f"Print failed: {filename}")
            send_telegram_with_photo(t("print_failed", filename=filename or "–"))

        # Progress milestones 25 / 50 / 75
        elif gcode_state == "RUNNING" and was_printing:
            for milestone in (25, 50, 75):
                if mc_percent >= milestone > _state["last_milestone"]:
                    _state["last_milestone"] = milestone
                    rem_mins = _smart_remaining()
                    send_telegram_with_photo(t("progress", pct=milestone, remaining=_format_minutes(rem_mins), finish=_finish_time(rem_mins)))
                    break

        # Process AMS Slot data
        ams_data = print_data.get("ams", {}).get("ams", [])
        for ams_unit in ams_data:
            for tray in ams_unit.get("tray", []):
                slot_id = tray.get("id")
                if slot_id is None: continue
                remain_pct = tray.get("remain", -1)
                ftype  = tray.get("tray_type", "Unknown")
                color  = tray.get("tray_color", "FFFFFF")
                brand  = tray.get("tray_sub_brands", "")

                _ams_state[str(slot_id)] = {
                    "type": ftype, "color": color,
                    "brand": brand, "remain": remain_pct
                }

                # ── New spool detection ──────────────────────
                fingerprint = f"{color}:{ftype}"
                prev_fp = _ams_fingerprints.get(str(slot_id))
                if (fingerprint != "FFFFFF:Unknown"
                        and fingerprint != ":"
                        and prev_fp is not None
                        and prev_fp != fingerprint):
                    # Spool changed — notify user
                    emoji = color_to_emoji(color)
                    note = t("ams_new_filament",
                             slot=int(slot_id)+1,
                             emoji=emoji,
                             ftype=ftype or "Unknown")
                    send_telegram(note)
                    log.info(f"New filament detected in slot {slot_id}: {fingerprint}")
                _ams_fingerprints[str(slot_id)] = fingerprint

                # ── Low Filament alert ───────────────────────
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
