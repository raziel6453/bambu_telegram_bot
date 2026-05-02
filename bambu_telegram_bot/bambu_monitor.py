#!/usr/bin/env python3
"""
Bambu Lab Telegram Monitor — Home Assistant Add-on
Clean rewrite. Supports A1/P1/X1 via local or cloud MQTT.
"""

import os, sys, json, html, ssl, socket, threading, logging, requests, yaml, time
from datetime import datetime, timedelta, timezone

VERSION = "2026-04-16.v3"

# ── Dependencies ──────────────────────────────────────────────────────────────
try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Missing paho-mqtt. Run: pip install paho-mqtt")

try:
    import telebot
except ImportError:
    sys.exit("Missing pyTelegramBotAPI. Run: pip install pyTelegramBotAPI")


# ── Config Loading ────────────────────────────────────────────────────────────
def load_config():
    # 1. HA Add-on path
    if os.path.exists("/data/options.json"):
        try:
            with open("/data/options.json") as f:
                return json.load(f), "/data"
        except Exception as e:
            print(f"Failed to load /data/options.json: {e}")

    # 2. Local options.json
    if os.path.exists("options.json"):
        try:
            with open("options.json") as f:
                return json.load(f), "."
        except Exception as e:
            print(f"Failed to load options.json: {e}")

    # 3. Local config.yaml (dev fallback)
    if os.path.exists("config.yaml"):
        try:
            with open("config.yaml") as f:
                cfg = yaml.safe_load(f)
                if isinstance(cfg, dict) and "options" in cfg:
                    return cfg["options"], "."
        except Exception as e:
            print(f"Failed to load config.yaml: {e}")

    return {}, "."


options, DATA_DIR = load_config()

# ── Settings ──────────────────────────────────────────────────────────────────
PRINTER_IP       = options.get("printer_ip", "").strip()
PRINTER_SERIAL   = options.get("printer_serial", "").strip()
PRINTER_PASSWORD = options.get("printer_password", "").strip()
BAMBU_USERNAME   = options.get("bambu_username", "").strip()
BAMBU_PASSWORD_  = options.get("bambu_password", "").strip()
TELEGRAM_TOKEN   = options.get("telegram_token", "").strip()
TELEGRAM_CHAT_ID = str(options.get("telegram_chat_id", "")).strip()
LANGUAGE         = options.get("language", "he")
SPOOLMAN_URL     = options.get("spoolman_url", "").strip().rstrip("/")
HA_CAMERA_ENTITY = options.get("ha_camera_entity", "").strip()
HA_LIGHT_ENTITY  = options.get("ha_light_entity", "").strip()
HA_WEIGHT_ENTITY = options.get("ha_weight_entity", "").strip()
LOW_STOCK_THRESH = int(options.get("low_stock_threshold", 100))

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_API           = "http://supervisor/core/api"
HA_AVAILABLE     = bool(SUPERVISOR_TOKEN)

SPOOLMAN_MAPPING_FILE = os.path.join(DATA_DIR, "spoolman_mapping.json")
PRINT_HISTORY_FILE    = os.path.join(DATA_DIR, "print_history.json")
STATE_FILE            = os.path.join(DATA_DIR, "persist_state.json")

JERUSALEM = timezone(timedelta(hours=3))

# ── Validation ────────────────────────────────────────────────────────────────
if not options:
    sys.exit("❌ No configuration found. Provide /data/options.json or config.yaml.")
if not PRINTER_IP or "YOUR_" in PRINTER_IP:
    sys.exit("❌ printer_ip not configured.")
if not TELEGRAM_TOKEN or "YOUR_" in TELEGRAM_TOKEN:
    sys.exit("❌ telegram_token not configured.")
if not TELEGRAM_CHAT_ID or "YOUR_" in TELEGRAM_CHAT_ID:
    sys.exit("❌ telegram_chat_id not configured.")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bambu")

log.info(f"=== Bambu Monitor {VERSION} ===")
log.info(f"Printer IP: {PRINTER_IP} | Serial: {PRINTER_SERIAL}")
log.info(f"Spoolman: {SPOOLMAN_URL or 'not configured'}")
log.info(f"HA API: {'enabled' if HA_AVAILABLE else 'disabled'}")
log.info(f"Language: {LANGUAGE}")

# ── Telegram Init ─────────────────────────────────────────────────────────────
bot = telebot.TeleBot(TELEGRAM_TOKEN)

def setup_bot_commands():
    cmds = [
        telebot.types.BotCommand("status", "Current status + camera snapshot"),
        telebot.types.BotCommand("ams", "AMS slot status"),
        telebot.types.BotCommand("history", "Last 10 completed prints"),
        telebot.types.BotCommand("cam", "Live camera snapshot"),
        telebot.types.BotCommand("pause", "Pause current print"),
        telebot.types.BotCommand("resume", "Resume paused print"),
        telebot.types.BotCommand("cancel", "Cancel print (asks confirmation)"),
        telebot.types.BotCommand("spools", "List Spoolman inventory"),
        telebot.types.BotCommand("map", "Map slot to existing spool"),
        telebot.types.BotCommand("set", "Create new spool and map slot"),
        telebot.types.BotCommand("update", "Update spool remaining weight"),
        telebot.types.BotCommand("light", "Toggle printer lamp"),
        telebot.types.BotCommand("debug", "Raw MQTT data"),
        telebot.types.BotCommand("help", "Show this menu"),
    ]
    try:
        bot.set_my_commands(cmds)
        log.info("Registered Telegram bot commands menu.")
    except Exception as e:
        log.warning(f"Could not register bot commands: {e}")

setup_bot_commands()


# ── Localisation ─────────────────────────────────────────────────────────────
STRINGS = {
    "he": {
        "connected":         "✅ הבוט מחובר ועובד!\nסדרתי: {serial}\nגרסה: {version}",
        "mqtt_failed":       "❌ חיבור MQTT נכשל: {reason}",
        "disconnected":      "🔴 הבוט הופסק.",
        "print_start":       "🖨️ ההדפסה התחילה!\n📄 קובץ: {filename}\n⚖️ משקל צפוי: {weight}\n⏱️ ETA: {eta} | יסיים ב: {finish}",
        "print_done":        "✅ ההדפסה הסתיימה!\n📄 קובץ: {filename}\n🧵 חוט שהשתמש: {weight}\n⏱️ סה\"כ זמן: {duration}",
        "print_failed":      "❌ ההדפסה נכשלה.\n📄 קובץ: {filename}",
        "print_paused":      "⏸️ ההדפסה הושהתה.\n📄 קובץ: {filename} | {pct}% (שכבה {layer}/{total_layers})",
        "print_resumed":     "▶️ ההדפסה חזרה.\n📄 קובץ: {filename}",
        "progress":          "📊 התקדמות: {pct}% (שכבה {layer}/{total_layers})\n⏱️ נותר: {remaining} | יסיים ב: {finish}",
        "status_printing":   (
            "🖨️ *מדפיס כעת...*\n"
            "📄 קובץ: `{filename}`\n"
            "⚖️ משקל צפוי: {weight}\n"
            "📦 סלוט פעיל: {slot_info}\n"
            "🧵 נותר בספול: {spool_rem}\n"
            "📊 התקדמות: {pct}% (שכבה {layer}/{total_layers})\n"
            "⏱️ נותר: {eta}\n"
            "🏁 יסיים ב: {finish}\n"
            "{light}"
        ),
        "status_paused":     "⏸️ *ההדפסה מושהית*\n📄 קובץ: `{filename}`\n📊 {pct}% (שכבה {layer}/{total_layers})\n{light}",
        "status_idle":       "💤 המדפסת במצב המתנה.\n{light}",
        "external_spool":    "סליל חיצוני",
        "ams_title":         "📦 <b>סטטוס AMS:</b>\n",
        "ams_slot_spoolman": "סלוט {slot}: {emoji} {brand} {material} {filname} {color_name} — {grams}g (Spoolman)\n",
        "ams_slot_native":   "סלוט {slot}: {emoji} {ftype} ({brand}) {color_name} — {grams}g\n",
        "ams_slot_empty":    "סלוט {slot}: ❌ ריק\n",
        "ams_no_data":       "❌ אין נתוני AMS עדיין.\nשלח /debug לבדוק חיבור MQTT.",
        "ask_spool_id":      "👇 בחר ספול מ-Spoolman כדי לשייך לסלוט {slot}:",
        "ask_slot_to_map":   "👇 באיזה סלוט תרצה לשייך ספול?",
        "invalid_number":    "❌ שגיאה: נא להקליד מספר חוקי.",
        "spools_title":      "📦 *מלאי Spoolman:*\n",
        "spools_item":       "#{id} | {emoji} {brand} {material} | {grams}g\n",
        "spools_empty":      "המלאי ריק.",
        "spoolman_mapped":   "✅ Spoolman ID {sid} שויך לסלוט {slot}.",
        "spoolman_usage":    "❌ שימוש: /map [סלוט 1-4] [spoolman_id]",
        "spoolman_no_url":   "❌ Spoolman לא הוגדר בהגדרות ה-Add-on.",
        "set_ok":            "✅ ספול חדש נוצר ב-Spoolman ושויך לסלוט {slot}.",
        "set_fail":          "❌ שגיאה ביצירת ספול.",
        "set_usage":         "❌ שימוש: /set [סלוט] [מותג] [חומר]\nלדוגמא: /set 1 Bambu PLA",
        "set_usage":         "❌ שימוש: /set [סלוט] [מותג] [חומר]\nלדוגמא: /set 1 Bambu PLA",
        "ask_update_spool":  "👇 בחר איזה ספול תרצה לעדכן:",
        "ask_new_weight":    "⚖️ מהו המשקל הנותר העדכני (בגרמים) עבור ספול #{sid}?\n\n(שלח מספר, או /cancel לביטול)",
        "update_success":    "✅ ספול #{sid} עודכן בהצלחה! משקל נותר: {weight}g.",
        "update_cancel":     "❌ העדכון בוטל.",
        "spoolman_deduct_ok": "\n\n✅ <b>Spoolman:</b> קוזז {weight}g מספול #{sid}",
        "spoolman_deduct_fail": "\n\n❌ <b>Spoolman:</b> שגיאה בקיזוז משקל מספול #{sid}",
        "low_stock":         "⚠️ ספול #{sid} ({label}) על סיום — נותר {grams}g בלבד!",
        "history_title":     "🗒️ *היסטוריית הדפסות:*\n",
        "history_item":      "📅 {date} | {filename} | {duration} | {grams}\n",
        "history_empty":     "אין הדפסות שמורות.",
        "cam_ok":            "📸 {time}",
        "cam_no_entity":     "❌ לא נמצאה מצלמת Bambu ב-HA.",
        "cam_error":         "❌ שגיאה במשיכת תמונה: {error}",
        "ha_no_api":         "❌ הבוט לא רץ עם HA API.",
        "light_on":          "💡 המנורה דולקת",
        "light_off":         "🌑 המנורה כבויה",
        "light_no_entity":   "❌ לא נמצא רכיב תאורה של Bambu.",
        "light_error":       "❌ שגיאה בשליטה על המנורה: {error}",
        "ctrl_pause":        "⏸️ שולח פקודת עצירה...",
        "ctrl_resume":       "▶️ שולח פקודת המשך...",
        "ctrl_cancel_ask":   "⚠️ בטוח שברצונך לבטל את ההדפסה?",
        "ctrl_cancel_yes":   "❌ מבטל הדפסה...",
        "ctrl_cancel_no":    "✅ ביטול בוטל — ההדפסה ממשיכת.",
        "ctrl_not_printing": "❌ אין הדפסה פעילה כעת.",
        "help": (
            "🖨️ <b>Bambu Telegram Monitor — פקודות:</b>\n\n"
            "📊 <b>סטטוס</b>\n"
            "/status — סטטוס נוכחי + תמונה\n"
            "/ams — מצב מגשי AMS\n"
            "/history — 10 הדפסות אחרונות\n\n"
            "🎥 <b>מצלמה</b>\n"
            "/cam — צילום חי\n\n"
            "⚡ <b>שליטה מרחוק</b>\n"
            "/pause — עצירת הדפסה\n"
            "/resume — המשך הדפסה\n"
            "/cancel — ביטול הדפסה (עם אישור)\n\n"
            "📦 <b>Spoolman</b>\n"
            "/spools — רשימת ספולים\n"
            "/map [סלוט] [id] — שיוך סלוט לספול\n"
            "/set [סלוט] [מותג] [חומר] — ספול חדש + שיוך\n"
            "/update — עדכון ידני של משקל ספול\n\n"
            "🔦 <b>כלים</b>\n"
            "/light — הדלקה/כיבוי נורה\n"
            "/debug — נתוני MQTT גולמיים\n"
            "/help — תפריט זה"
        ),
    },
    "en": {
        "connected":         "✅ Bot connected and running!\nSerial: {serial}\nVersion: {version}",
        "mqtt_failed":       "❌ MQTT connection failed: {reason}",
        "disconnected":      "🔴 Bot stopped.",
        "print_start":       "🖨️ Print started!\n📄 File: {filename}\n⚖️ Est. filament: {weight}\n⏱️ ETA: {eta} | Finishes at: {finish}",
        "print_done":        "✅ Print finished!\n📄 File: {filename}\n🧵 Filament used: {weight}\n⏱️ Total time: {duration}",
        "print_failed":      "❌ Print failed.\n📄 File: {filename}",
        "print_paused":      "⏸️ Print paused.\n📄 File: {filename} | {pct}% (Layer {layer}/{total_layers})",
        "print_resumed":     "▶️ Print resumed.\n📄 File: {filename}",
        "progress":          "📊 Progress: {pct}% (Layer {layer}/{total_layers})\n⏱️ Remaining: {remaining} | Finishes at: {finish}",
        "status_printing":   (
            "🖨️ *Currently Printing...*\n"
            "📄 File: `{filename}`\n"
            "⚖️ Est. filament: {weight}\n"
            "📦 Active Slot: {slot_info}\n"
            "🧵 Spool remaining: {spool_rem}\n"
            "📊 Progress: {pct}% (Layer {layer}/{total_layers})\n"
            "⏱️ Remaining: {eta}\n"
            "🏁 Finishes at: {finish}\n"
            "{light}"
        ),
        "status_paused":     "⏸️ *Print is paused*\n📄 File: `{filename}`\n📊 {pct}% (Layer {layer}/{total_layers})\n{light}",
        "status_idle":       "💤 Printer is idle.\n{light}",
        "external_spool":    "External Spool",
        "ams_title":         "📦 <b>AMS Status:</b>\n",
        "ams_slot_spoolman": "Slot {slot}: {emoji} {brand} {material} {filname} {color_name} — {grams}g (Spoolman)\n",
        "ams_slot_native":   "Slot {slot}: {emoji} {ftype} ({brand}) {color_name} — {grams}g\n",
        "ams_slot_empty":    "Slot {slot}: ❌ Empty\n",
        "ams_no_data":       "❌ No AMS data yet.\nSend /debug to check MQTT connection.",
        "ask_spool_id":      "👇 Choose a Spoolman spool to map to Slot {slot}:",
        "ask_slot_to_map":   "👇 Which slot do you want to map?",
        "invalid_number":    "❌ Error: Please type a valid number.",
        "spools_title":      "📦 *Spoolman Inventory:*\n",
        "spools_item":       "#{id} | {emoji} {brand} {material} | {grams}g remaining\n",
        "spools_empty":      "Inventory is empty.",
        "spoolman_mapped":   "✅ Spoolman ID {sid} mapped to Slot {slot}.",
        "spoolman_usage":    "❌ Usage: /map [slot 1-4] [spoolman_id]",
        "spoolman_no_url":   "❌ Spoolman is not configured in Add-on settings.",
        "set_ok":            "✅ New spool created in Spoolman and mapped to Slot {slot}.",
        "set_fail":          "❌ Failed to create spool.",
        "set_usage":         "❌ Usage: /set [slot] [brand] [material]\nExample: /set 1 Bambu PLA",
        "set_usage":         "❌ Usage: /set [slot] [brand] [material]\nExample: /set 1 Bambu PLA",
        "ask_update_spool":  "👇 Choose which spool you want to update:",
        "ask_new_weight":    "⚖️ What is the actual remaining weight (in grams) for Spool #{sid}?\n\n(Send a number, or /cancel to abort)",
        "update_success":    "✅ Spool #{sid} successfully updated! Remaining weight: {weight}g.",
        "update_cancel":     "❌ Update cancelled.",
        "spoolman_deduct_ok": "\n\n✅ <b>Spoolman:</b> Deducted {weight}g from Spool #{sid}",
        "spoolman_deduct_fail": "\n\n❌ <b>Spoolman:</b> Failed to deduct weight from Spool #{sid}",
        "low_stock":         "⚠️ Spool #{sid} ({label}) is running low — only {grams}g left!",
        "history_title":     "🗒️ *Print History:*\n",
        "history_item":      "📅 {date} | {filename} | {duration} | {grams}\n",
        "history_empty":     "No prints saved yet.",
        "cam_ok":            "📸 {time}",
        "cam_no_entity":     "❌ No Bambu camera found in Home Assistant.",
        "cam_error":         "❌ Error fetching snapshot: {error}",
        "ha_no_api":         "❌ Bot is not running with HA API access.",
        "light_on":          "💡 Light is ON",
        "light_off":         "🌑 Light is OFF",
        "light_no_entity":   "❌ No Bambu light entity found in Home Assistant.",
        "light_error":       "❌ Error controlling light: {error}",
        "ctrl_pause":        "⏸️ Sending pause command...",
        "ctrl_resume":       "▶️ Sending resume command...",
        "ctrl_cancel_ask":   "⚠️ Are you sure you want to cancel the print?",
        "ctrl_cancel_yes":   "❌ Cancelling print...",
        "ctrl_cancel_no":    "✅ Cancel aborted — print continues.",
        "ctrl_not_printing": "❌ No print is currently active.",
        "help": (
            "🖨️ <b>Bambu Telegram Monitor — Commands:</b>\n\n"
            "📊 <b>Status</b>\n"
            "/status — Current status + camera snapshot\n"
            "/ams — AMS slot status\n"
            "/history — Last 10 completed prints\n\n"
            "🎥 <b>Camera</b>\n"
            "/cam — Live snapshot (requires HA)\n\n"
            "⚡ <b>Remote Control</b>\n"
            "/pause — Pause current print\n"
            "/resume — Resume paused print\n"
            "/cancel — Cancel print (asks confirmation)\n\n"
            "📦 <b>Spoolman</b>\n"
            "/spools — List inventory\n"
            "/map [slot] [id] — Map slot to existing spool\n"
            "/set [slot] [brand] [material] — Create new spool and map slot\n"
            "/update — Manually update a spool's remaining weight\n\n"
            "🔦 <b>Tools</b>\n"
            "/light — Toggle printer lamp\n"
            "/debug — Raw MQTT data for troubleshooting\n"
            "/help — Show this menu"
        ),
    },
}


def t(key, **kwargs):
    tmpl = STRINGS.get(LANGUAGE, STRINGS["en"]).get(key, key)
    try:
        return tmpl.format(**kwargs)
    except Exception:
        return tmpl


# ── Utility ───────────────────────────────────────────────────────────────────
def color_to_emoji(hexcode: str) -> str:
    if not hexcode or len(hexcode) < 6:
        return "🧵"
    h = hexcode.replace("#", "")[:6]
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        if r > 200 and g > 200 and b > 200: return "⚪"
        if r < 50  and g < 50  and b < 50:  return "⚫"
        if r > 180 and g < 80  and b < 80:  return "🔴"
        if r < 80  and g > 180 and b < 80:  return "🟢"
        if r < 80  and g < 80  and b > 180: return "🔵"
        if r > 180 and g > 180 and b < 80:  return "🟡"
        if r > 180 and g > 100 and b < 80:  return "🟠"
        if r > 100 and g < 80  and b > 100: return "🟣"
    except Exception:
        pass
    return "🧵"


def hex_to_color_name(hexcode: str) -> str:
    if not hexcode or len(hexcode) < 6:
        return ""
    h = hexcode.replace("#", "")[:6]
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        
        palette = {
            "White": (255, 255, 255), "Black": (0, 0, 0), "Gray": (128, 128, 128),
            "Red": (255, 0, 0), "Green": (0, 255, 0), "Blue": (0, 0, 255),
            "Yellow": (255, 255, 0), "Orange": (255, 165, 0), "Purple": (128, 0, 128),
            "Cyan": (0, 255, 255), "Magenta": (255, 0, 255), "Pink": (255, 192, 203),
            "Brown": (165, 42, 42)
        }
        
        best_name = ""
        min_dist = float('inf')
        for name, (pr, pg, pb) in palette.items():
            dist = (r - pr)**2 + (g - pg)**2 + (b - pb)**2
            if dist < min_dist:
                min_dist = dist
                best_name = name
                
        if LANGUAGE == "he":
            he_map = {
                "White": "לבן", "Black": "שחור", "Gray": "אפור", "Red": "אדום",
                "Green": "ירוק", "Blue": "כחול", "Yellow": "צהוב", "Orange": "כתום",
                "Purple": "סגול", "Cyan": "טורקיז", "Magenta": "מגנטה", "Pink": "ורוד",
                "Brown": "חום"
            }
            return he_map.get(best_name, best_name)
        return best_name
    except Exception:
        return ""


def fmt_mins(mins: int) -> str:
    if mins <= 0:
        return "–"
    h, m = divmod(int(mins), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def fmt_duration(secs: float) -> str:
    h, rem = divmod(int(secs), 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


def finish_time(remaining_mins: int) -> str:
    """Returns HH:MM finish time in Jerusalem timezone."""
    # Priority 1: HA end_time sensor
    if HA_AVAILABLE:
        we = _ha_weight_entity()
        if we and "_print_weight" in we:
            prefix = we.replace("_print_weight", "")
            raw = _ha_sensor(f"{prefix}_end_time")
            if raw and "T" in str(raw):
                try:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    return dt.astimezone(JERUSALEM).strftime("%H:%M")
                except Exception:
                    pass
    if remaining_mins <= 0:
        return "–"
    return (datetime.now(JERUSALEM) + timedelta(minutes=remaining_mins)).strftime("%H:%M")


# ── JSON Persistence ──────────────────────────────────────────────────────────
def _load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        log.error(f"Failed to load {path}: {e}")
    return default


def _save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, default=str)
    except Exception as e:
        log.error(f"Failed to save {path}: {e}")


def load_mapping():
    return _load_json(SPOOLMAN_MAPPING_FILE, {})


def save_mapping(m):
    _save_json(SPOOLMAN_MAPPING_FILE, m)


# ── HA Integration ────────────────────────────────────────────────────────────
_discovered = {}  # cache: "camera", "light", "weight"


def _ha_headers():
    return {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}


def _ha_all_states():
    try:
        r = requests.get(f"{HA_API}/states", headers=_ha_headers(), timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error(f"HA states fetch failed: {e}")
    return []


def _ha_sensor(entity_id: str):
    """Returns state string of an HA entity, or None if unavailable."""
    if not HA_AVAILABLE or not entity_id:
        return None
    try:
        r = requests.get(f"{HA_API}/states/{entity_id}", headers=_ha_headers(), timeout=5)
        if r.status_code == 200:
            val = r.json().get("state", "")
            if val not in ("unknown", "unavailable", "None", "none", ""):
                return val
    except Exception:
        pass
    return None


def _ha_discover(domain: str, keywords: list):
    """Find a HA entity matching domain + any keyword in entity name."""
    for state in _ha_all_states():
        eid = state.get("entity_id", "")
        if not eid.startswith(f"{domain}."):
            continue
        name = eid.lower()
        if "bambu" in name and any(k in name for k in keywords):
            log.info(f"Auto-discovered {domain}: {eid}")
            return eid
    return None


def _ha_camera_entity():
    if HA_CAMERA_ENTITY:
        return HA_CAMERA_ENTITY
    if "camera" not in _discovered:
        _discovered["camera"] = _ha_discover("camera", ["bambu", "camera"])
    return _discovered.get("camera")


def _ha_light_entity():
    if HA_LIGHT_ENTITY:
        return HA_LIGHT_ENTITY
    if "light" not in _discovered:
        _discovered["light"] = _ha_discover("light", ["lamp", "light", "chamber"])
    return _discovered.get("light")


def _ha_weight_entity():
    if HA_WEIGHT_ENTITY:
        return HA_WEIGHT_ENTITY
    if "weight" not in _discovered:
        for state in _ha_all_states():
            eid  = state.get("entity_id", "")
            sval = state.get("state", "")
            if (eid.startswith("sensor.")
                    and ("print_weight" in eid.lower() or ("bambu" in eid.lower() and "weight" in eid.lower()))
                    and sval not in ("", "unknown", "unavailable", "None")):
                log.info(f"Auto-discovered weight entity: {eid}")
                _discovered["weight"] = eid
                break
    return _discovered.get("weight")


def ha_snapshot():
    """Returns (bytes_or_None, error_str_or_None)."""
    if not HA_AVAILABLE:
        return None, t("ha_no_api")
    cam = _ha_camera_entity()
    if not cam:
        return None, t("cam_no_entity")
    try:
        r = requests.get(f"{HA_API}/camera_proxy/{cam}", headers=_ha_headers(), timeout=15)
        if r.status_code == 200:
            return r.content, None
        return None, t("cam_error", error=f"HTTP {r.status_code}")
    except Exception as e:
        return None, t("cam_error", error=str(e))


def ha_light_state():
    """Returns (state_str, entity_id)."""
    eid = _ha_light_entity()
    if not eid or not HA_AVAILABLE:
        return None, None
    try:
        r = requests.get(f"{HA_API}/states/{eid}", headers=_ha_headers(), timeout=5)
        if r.status_code == 200:
            return r.json().get("state"), eid
    except Exception:
        pass
    return None, eid


def ha_light_set(entity_id: str, state: str):
    """Returns error string or None on success."""
    service = "turn_on" if state == "on" else "turn_off"
    try:
        r = requests.post(
            f"{HA_API}/services/light/{service}",
            headers=_ha_headers(),
            json={"entity_id": entity_id},
            timeout=10,
        )
        return None if r.status_code in (200, 201) else f"HTTP {r.status_code}"
    except Exception as e:
        return str(e)


# ── Smart Remaining Time ──────────────────────────────────────────────────────
def smart_remaining() -> int:
    """Returns best estimate of remaining minutes."""
    # Priority 1: HA remaining_time sensor
    if HA_AVAILABLE:
        we = _ha_weight_entity()
        if we and "_print_weight" in we:
            prefix = we.replace("_print_weight", "")
            raw = _ha_sensor(f"{prefix}_remaining_time")
            if raw and str(raw).isdigit():
                return int(raw)

    # Priority 2: MQTT reported value
    return max(0, _state.get("mc_remaining_time", 0))


# ── State ─────────────────────────────────────────────────────────────────────
_state = {
    "printing":           False,
    "gcode_state":        "",
    "filename":           "",
    "start_time":         None,
    "mc_percent":         0,
    "mc_remaining_time":  0,
    "layer_num":          0,
    "total_layer_num":    0,
    "last_milestone":     0,
    "print_weight":       0.0,
    "tray_now":           255,
    "spool_start_weight": None,
    "_raw_print":         {},
}
_ams_state     = {}       # slot_id (str "0"–"3") -> {type, color, brand, remain}
_alerted_slots = set()
_lock          = threading.Lock()
_mqtt_client   = None
_status_event  = threading.Event()


def _restore_state():
    saved = _load_json(STATE_FILE, {})
    if not saved:
        return
    for key in ("printing", "gcode_state", "filename", "mc_percent",
                "mc_remaining_time", "last_milestone", "print_weight",
                "tray_now", "spool_start_weight"):
        if key in saved:
            _state[key] = saved[key]
    if saved.get("start_time"):
        try:
            _state["start_time"] = datetime.fromisoformat(saved["start_time"])
        except Exception:
            pass
    log.info(f"Restored state: printing={_state['printing']} gcode={_state['gcode_state']} pct={_state['mc_percent']}")


def _persist_state():
    _save_json(STATE_FILE, {
        "printing":           _state["printing"],
        "gcode_state":        _state["gcode_state"],
        "filename":           _state["filename"],
        "mc_percent":         _state["mc_percent"],
        "mc_remaining_time":  _state["mc_remaining_time"],
        "last_milestone":     _state["last_milestone"],
        "print_weight":       _state["print_weight"],
        "tray_now":           _state["tray_now"],
        "spool_start_weight": _state["spool_start_weight"],
        "start_time":         _state["start_time"].isoformat() if _state["start_time"] else None,
    })


# ── Telegram Helpers ──────────────────────────────────────────────────────────
def tg_send(text: str):
    try:
        bot.send_message(TELEGRAM_CHAT_ID, text)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


def tg_photo(text: str):
    """Send message with HA camera snapshot, falling back to plain text."""
    if HA_AVAILABLE:
        try:
            img, _ = ha_snapshot()
            if img:
                bot.send_photo(TELEGRAM_CHAT_ID, img, caption=text)
                return
        except Exception as e:
            log.warning(f"Photo send failed, falling back to text: {e}")
    tg_send(text)


def chat_ok(message) -> bool:
    return str(message.chat.id) == TELEGRAM_CHAT_ID


# ── Spoolman ──────────────────────────────────────────────────────────────────
def _spoolman_get(path: str, timeout=5):
    if not SPOOLMAN_URL:
        return None
    try:
        r = requests.get(f"{SPOOLMAN_URL}{path}", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error(f"Spoolman GET {path}: {e}")
    return None


def _spoolman_put(path: str, data: dict, timeout=10):
    if not SPOOLMAN_URL:
        return False
    try:
        r = requests.put(f"{SPOOLMAN_URL}{path}", json=data, timeout=timeout)
        return r.status_code in (200, 201)
    except Exception as e:
        log.error(f"Spoolman PUT {path}: {e}")
    return False


def _spoolman_post(path: str, data: dict, timeout=10):
    if not SPOOLMAN_URL:
        return None
    try:
        r = requests.post(f"{SPOOLMAN_URL}{path}", json=data, timeout=timeout)
        if r.status_code in (200, 201):
            return r.json()
    except Exception as e:
        log.error(f"Spoolman POST {path}: {e}")
    return None


def _spoolman_patch(path: str, data: dict, timeout=10):
    if not SPOOLMAN_URL:
        return False
    try:
        r = requests.patch(f"{SPOOLMAN_URL}{path}", json=data, timeout=timeout)
        return r.status_code in (200, 204)
    except Exception as e:
        log.error(f"Spoolman PATCH {path}: {e}")
    return False


def _check_low_stock(spool_id):
    data = _spoolman_get(f"/api/v1/spool/{spool_id}")
    if not data:
        return
    rem = data.get("remaining_weight", 9999)
    if rem < LOW_STOCK_THRESH:
        fil   = data.get("filament", {})
        label = f"{fil.get('vendor', {}).get('name', '')} {fil.get('material', '')}".strip()
        tg_send(t("low_stock", sid=spool_id, label=label, grams=f"{float(rem):.1f}"))


def _capture_spool_start(tray: int):
    mapping  = load_mapping()
    spool_id = mapping.get(str(tray))
    if spool_id:
        data = _spoolman_get(f"/api/v1/spool/{spool_id}")
        if data:
            _state["spool_start_weight"] = data.get("remaining_weight")


def _spool_deduct(weight_g: float, tray: int) -> str:
    if weight_g <= 0 or tray == 255 or not SPOOLMAN_URL:
        return ""
    mapping  = load_mapping()
    spool_id = mapping.get(str(tray))
    if not spool_id:
        return ""
    ok = _spoolman_put(f"/api/v1/spool/{spool_id}/use", {"use_weight": weight_g})
    if ok:
        log.info(f"Deducted {weight_g}g from Spoolman spool {spool_id}")
        return t("spoolman_deduct_ok", weight=weight_g, sid=spool_id)
    else:
        log.error(f"Failed to deduct from Spoolman spool {spool_id}")
        return t("spoolman_deduct_fail", sid=spool_id)


# ── Print History ─────────────────────────────────────────────────────────────
def _add_history(entry: dict):
    history = _load_json(PRINT_HISTORY_FILE, [])
    history.append(entry)
    _save_json(PRINT_HISTORY_FILE, history[-100:])


# ── MQTT Publisher ────────────────────────────────────────────────────────────
def _mqtt_publish(payload: dict) -> bool:
    global _mqtt_client
    if not _mqtt_client:
        return False
    topic = f"device/{PRINTER_SERIAL}/request"
    try:
        _mqtt_client.publish(topic, json.dumps(payload))
        return True
    except Exception as e:
        log.error(f"MQTT publish failed: {e}")
        return False


def request_pushall():
    """Ask printer to push its full current state."""
    _mqtt_publish({"pushing": {"sequence_id": "0", "command": "pushall"}})
    log.info("Sent pushall request")


def printer_cmd(cmd: dict) -> bool:
    return _mqtt_publish({"print": {"sequence_id": "0", **cmd}})


# ── Print State Machine Handlers ──────────────────────────────────────────────
def _on_print_start(mc_percent, mc_remaining, filename, weight):
    _state["printing"]       = True
    _state["last_milestone"] = 0

    # Backdate start_time if we're mid-print on fresh connect
    if 1 < mc_percent < 100 and mc_remaining > 0:
        try:
            total_est = mc_remaining / (1 - mc_percent / 100.0)
            elapsed   = total_est * (mc_percent / 100.0)
            _state["start_time"] = datetime.now() - timedelta(minutes=elapsed)
        except Exception:
            _state["start_time"] = datetime.now()
    else:
        _state["start_time"] = datetime.now()

    _capture_spool_start(_state.get("tray_now", 255))
    
    if weight <= 0:
        if HA_AVAILABLE:
            for _ in range(5):
                we = _ha_weight_entity()
                if we:
                    val = _ha_sensor(we)
                    if val and str(val).lower() not in ("0.0g", "0g", "unknown", "unavailable", "0.0", "0", "none"):
                        try:
                            weight = float(str(val).replace("g", "").strip())
                            break
                        except Exception:
                            pass
                time.sleep(1)
    if weight <= 0 and _state.get("print_weight", 0) > 0:
        weight = _state["print_weight"]

    rem   = smart_remaining()
    w_str = f"{weight:.1f}g" if weight > 0 else "–"
    log.info(f"Print started: {filename}")
    tg_photo(t("print_start", filename=filename or "–", weight=w_str,
               eta=fmt_mins(rem), finish=finish_time(rem)))
    _persist_state()


def _on_reconnect_recovery(mc_percent, mc_remaining):
    """Silently recover printing=True after bot restart mid-print."""
    _state["printing"] = True
    log.info(f"Mid-print reconnect recovery at {mc_percent}%")

    if not _state["start_time"] and mc_percent and mc_remaining:
        try:
            total_est = mc_remaining / (1 - mc_percent / 100.0)
            elapsed   = total_est * (mc_percent / 100.0)
            _state["start_time"] = datetime.now() - timedelta(minutes=elapsed)
        except Exception:
            _state["start_time"] = datetime.now()

    # Mark all already-passed milestones so we don't re-fire them
    for m in (25, 50, 75):
        if mc_percent >= m:
            _state["last_milestone"] = m
    _persist_state()


def _on_print_finish(filename, weight):
    _state["printing"] = False
    
    if weight <= 0:
        if HA_AVAILABLE:
            we = _ha_weight_entity()
            if we:
                val = _ha_sensor(we)
                if val:
                    try:
                        weight = float(str(val).replace("g", "").strip())
                    except Exception:
                        pass
    if weight <= 0 and _state["print_weight"] > 0:
        weight = _state["print_weight"]
        
    dur  = ""
    if _state["start_time"]:
        dur = fmt_duration((datetime.now() - _state["start_time"]).total_seconds())
    tray  = _state.get("tray_now", 255)
    w_str = f"{weight:.1f}g" if weight > 0 else "–"
    log.info(f"Print finished: {filename} in {dur}, {weight}g used")
    
    spool_status = _spool_deduct(weight, tray)
    msg = t("print_done", filename=filename or "–", weight=w_str, duration=dur)
    if spool_status:
        msg += spool_status
        
    tg_photo(msg)

    _state["print_weight"] = 0  # Reset for next print
    
    _add_history({
        "date":     datetime.now(JERUSALEM).strftime("%Y-%m-%d %H:%M"),
        "filename": filename or "Unknown",
        "duration": dur or "–",
        "grams":    weight,
        "slot":     tray,
    })

    if tray != 255:
        mapping  = load_mapping()
        spool_id = mapping.get(str(tray))
        if spool_id:
            threading.Thread(target=_check_low_stock, args=(spool_id,), daemon=True).start()

    _persist_state()


# ── MQTT Callbacks ────────────────────────────────────────────────────────────
_RC_CODES = {
    1: "Incorrect protocol version",
    2: "Invalid client identifier",
    3: "Server unavailable",
    4: "Bad username or password — check your printer Access Code",
    5: "Not authorised",
}


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        topic = f"device/{PRINTER_SERIAL}/report"
        client.subscribe(topic)
        log.info(f"MQTT connected ✓ subscribed to {topic}")
        tg_send(t("connected", serial=PRINTER_SERIAL, version=VERSION))
        # Request full state dump 2s after connect (gives printer time to respond)
        threading.Timer(2.0, request_pushall).start()
    else:
        reason = _RC_CODES.get(rc, f"Unknown error (rc={rc})")
        log.error(f"MQTT connect failed: {reason}")
        tg_send(t("mqtt_failed", reason=reason))


def on_disconnect(client, userdata, rc):
    if rc != 0:
        log.warning(f"MQTT disconnected unexpectedly (rc={rc}), will auto-reconnect…")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return

    print_data = payload.get("print", {})
    if not print_data:
        return

    with _lock:
        _status_event.set()
        _state["_raw_print"] = print_data

        # ── Extract fields, falling back to current state ──────────────────
        gcode_state  = print_data.get("gcode_state",         _state["gcode_state"])
        mc_percent   = print_data.get("mc_percent",          _state["mc_percent"])
        mc_remaining = print_data.get("mc_remaining_time",   _state["mc_remaining_time"])
        layer_num    = print_data.get("layer_num",           _state.get("layer_num", 0))
        total_layers = print_data.get("total_layer_num",     _state.get("total_layer_num", 0))
        filename     = print_data.get("subtask_name",        _state["filename"]) or _state["filename"]

        # AMS active tray
        ams_block = print_data.get("ams", {})
        if "tray_now" in ams_block:
            try:
                _state["tray_now"] = int(ams_block["tray_now"])
            except Exception:
                pass

        # Filament weight — try all known grams fields
        new_weight = _state["print_weight"]
        for field in ("subtask_weight", "print_weight", "total_weight", "weight"):
            val = print_data.get(field)
            if val is not None:
                try:
                    w = float(str(val).lower().replace("g", "").strip())
                    if w > 0:
                        new_weight = round(w, 1)
                        break
                except Exception:
                    pass

        prev_state   = _state["gcode_state"]
        was_printing = _state["printing"]

        _state.update({
            "gcode_state":       gcode_state,
            "mc_percent":        mc_percent,
            "mc_remaining_time": mc_remaining,
            "layer_num":         layer_num,
            "total_layer_num":   total_layers,
        })
        if new_weight > 0:
            _state["print_weight"] = new_weight
        if filename:
            _state["filename"] = filename

        # ── State Machine ──────────────────────────────────────────────────

        if gcode_state == "RUNNING" and prev_state in ("PREPARE", "IDLE", "FINISH", "FAILED", ""):
            # Fresh print start
            _on_print_start(mc_percent, mc_remaining, filename, new_weight)

        elif gcode_state == "RUNNING" and not was_printing:
            # Bot restarted while printer was already printing — recover silently
            _on_reconnect_recovery(mc_percent, mc_remaining)

        elif gcode_state == "RUNNING" and was_printing:
            # Progress milestones: 25%, 50%, 75%
            for milestone in (25, 50, 75):
                if mc_percent >= milestone > _state["last_milestone"]:
                    _state["last_milestone"] = milestone
                    rem = smart_remaining()
                    tg_photo(t("progress", pct=milestone, layer=_state.get("layer_num", 0), total_layers=_state.get("total_layer_num", 0),
                               remaining=fmt_mins(rem), finish=finish_time(rem)))
                    break

        elif gcode_state == "FINISH" and was_printing:
            _on_print_finish(filename, new_weight)

        elif gcode_state == "FAILED" and was_printing:
            _state["printing"] = False
            log.info(f"Print failed: {filename}")
            tg_photo(t("print_failed", filename=filename or "–"))
            _persist_state()

        elif gcode_state == "PAUSE" and was_printing and prev_state == "RUNNING":
            tg_send(t("print_paused", filename=filename or "–", pct=mc_percent, layer=_state.get("layer_num", 0), total_layers=_state.get("total_layer_num", 0)))

        elif gcode_state == "RUNNING" and prev_state == "PAUSE":
            tg_send(t("print_resumed", filename=filename or "–"))

        # ── AMS slot data ──────────────────────────────────────────────────
        ams_data = ams_block.get("ams", [])
        for ams_unit in ams_data:
            for tray_info in ams_unit.get("tray", []):
                sid = tray_info.get("id")
                if sid is None:
                    continue
                remain_pct = tray_info.get("remain", -1)
                _ams_state[str(sid)] = {
                    "type":   tray_info.get("tray_type", "Unknown"),
                    "color":  tray_info.get("tray_color", "FFFFFF"),
                    "brand":  tray_info.get("tray_sub_brands", ""),
                    "remain": remain_pct,
                }
                # Low filament alert (native, no Spoolman mapping)
                mapping = load_mapping()
                if str(sid) not in mapping and 0 <= remain_pct < 10:
                    if sid not in _alerted_slots:
                        tg_send(t("low_filament", slot=int(sid) + 1, grams=remain_pct * 10))
                        _alerted_slots.add(sid)
                elif sid in _alerted_slots and remain_pct >= 10:
                    _alerted_slots.discard(sid)


# ── Bot Commands ──────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    if not chat_ok(message):
        return
    bot.reply_to(message, t("help"), parse_mode="HTML")


@bot.message_handler(commands=["status"])
def cmd_status(message):
    if not chat_ok(message):
        return
    try:
        # Request fresh data from printer and wait up to 3s
        _status_event.clear()
        request_pushall()
        _status_event.wait(timeout=3)

        # Light status string
        light_str = ""
        if HA_AVAILABLE:
            lstate, _ = ha_light_state()
            light_str = "\n" + (t("light_on") if lstate == "on" else t("light_off"))

        with _lock:
            gcode = _state["gcode_state"]
            pct   = _state["mc_percent"]
            fn    = _state["filename"] or "Unknown"
            tray  = _state["tray_now"]

            if _state["printing"] and gcode not in ("IDLE", "FINISH", ""):
                # ── Weight ────────────────────────────────────────────────
                weight_str = "–"
                if HA_AVAILABLE:
                    we = _ha_weight_entity()
                    if we:
                        val = _ha_sensor(we)
                        if val:
                            try:
                                weight_str = f"{float(str(val).replace('g','').strip()):.1f}g"
                            except Exception:
                                pass
                if weight_str == "–" and _state["print_weight"] > 0:
                    weight_str = f"{_state['print_weight']:.1f}g"

                # ── Spool remaining ───────────────────────────────────────
                spool_rem = "–"
                if SPOOLMAN_URL and tray != 255:
                    mapping  = load_mapping()
                    spool_id = mapping.get(str(tray))
                    if spool_id:
                        data = _spoolman_get(f"/api/v1/spool/{spool_id}")
                        if data:
                            rem_w = data.get("remaining_weight")
                            if rem_w is not None:
                                spool_rem = f"{float(rem_w):.1f}g"

                rem_mins = smart_remaining()

                slot_info = t("external_spool") if tray == 255 else str(tray + 1)

                if gcode == "PAUSE":
                    res = t("status_paused", filename=fn, pct=pct, layer=_state.get("layer_num", 0), total_layers=_state.get("total_layer_num", 0), light=light_str)
                else:
                    res = t("status_printing",
                            filename=fn, pct=pct, layer=_state.get("layer_num", 0), total_layers=_state.get("total_layer_num", 0),
                            weight=weight_str, spool_rem=spool_rem, slot_info=slot_info,
                            eta=fmt_mins(rem_mins), finish=finish_time(rem_mins),
                            light=light_str)
            else:
                res = t("status_idle", light=light_str)

        # Send with photo if HA camera is available
        if HA_AVAILABLE:
            img, _ = ha_snapshot()
            if img:
                bot.send_photo(message.chat.id, img, caption=res)
                return

        bot.reply_to(message, res)

    except Exception as e:
        import traceback
        bot.reply_to(message, f"❌ /status error:\n{e}\n{traceback.format_exc()[:400]}")


@bot.message_handler(commands=["ams"])
def cmd_ams(message):
    if not chat_ok(message):
        return
    with _lock:
        if not _ams_state:
            bot.reply_to(message, t("ams_no_data"))
            return

        mapping = load_mapping()
        res = t("ams_title")

        markup = None
        if SPOOLMAN_URL:
            markup = telebot.types.InlineKeyboardMarkup()
            markup.row_width = 4
            btns = []

        for i in range(4):
            sid = str(i)
            slot_num = i + 1

            if sid in mapping and SPOOLMAN_URL:
                # Show Spoolman data
                data = _spoolman_get(f"/api/v1/spool/{mapping[sid]}")
                if data:
                    fil   = data.get("filament", {})
                    color_hex = fil.get("color_hex", "")
                    emoji = color_to_emoji(color_hex)
                    cname = hex_to_color_name(color_hex)
                    color_str = f"({cname})" if cname else ""
                    brand = fil.get("vendor", {}).get("name", "Unknown")
                    mat   = fil.get("material", "")
                    filname = fil.get("name", "")
                    grams = round(data.get("remaining_weight", 0))
                    res  += t("ams_slot_spoolman", slot=slot_num, emoji=emoji,
                              brand=brand, material=mat, filname=filname, color_name=color_str, grams=grams)
                else:
                    res += t("ams_slot_empty", slot=slot_num)

            elif sid in _ams_state:
                # Show native MQTT data
                slot = _ams_state[sid]
                if slot.get("type") in ("Unknown", "", None) or slot.get("remain", -1) < 0:
                    res += t("ams_slot_empty", slot=slot_num)
                else:
                    color_hex = slot.get("color", "")
                    emoji = color_to_emoji(color_hex)
                    cname = hex_to_color_name(color_hex)
                    color_str = f"({cname})" if cname else ""
                    res  += t("ams_slot_native", slot=slot_num, emoji=emoji,
                              ftype=slot.get("type", ""), brand=slot.get("brand", ""), color_name=color_str,
                              grams=slot.get("remain", 0) * 10)
            else:
                res += t("ams_slot_empty", slot=slot_num)
                
            if SPOOLMAN_URL:
                btns.append(telebot.types.InlineKeyboardButton(f"Map {slot_num}", callback_data=f"map_{slot_num}"))

        if SPOOLMAN_URL:
            markup.add(*btns)

    bot.reply_to(message, res, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("map_"))
def cb_map(call):
    if not chat_ok(call.message):
        return
    slot = call.data.split("_")[1]
    
    if not SPOOLMAN_URL:
        bot.answer_callback_query(call.id, "Spoolman not configured.")
        return
        
    spools = _spoolman_get("/api/v1/spool")
    if not spools:
        bot.answer_callback_query(call.id, "No spools found in Spoolman.")
        return

    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    btns = []
    
    # Take up to 80 spools to avoid telegram limits
    for s in spools[:80]:
        rem = round(s.get("remaining_weight", 0))
        if rem <= 0:
            continue
        fil = s.get("filament", {})
        color_hex = fil.get("color_hex", "")
        emoji = color_to_emoji(color_hex)
        brand = fil.get("vendor", {}).get("name", "Unknown")
        mat = fil.get("material", "Unknown")
        name = fil.get("name", "")
        
        # Max button text length
        label = f"#{s.get('id')} {emoji} {brand} {mat} {name} ({rem}g)"
        if len(label) > 60:
            label = label[:57] + "..."
            
        btns.append(telebot.types.InlineKeyboardButton(label, callback_data=f"setslot_{slot}_{s.get('id')}"))

    markup.add(*btns)
    msg = bot.send_message(call.message.chat.id, t("ask_spool_id", slot=slot), parse_mode="HTML", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setslot_"))
def cb_setslot(call):
    if not chat_ok(call.message):
        return
    parts = call.data.split("_")
    slot = parts[1]
    spool_id = int(parts[2])
    
    mapping = load_mapping()
    mapping[str(int(slot)-1)] = spool_id
    save_mapping(mapping)
    
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
        
    bot.send_message(call.message.chat.id, t("spoolman_mapped", sid=spool_id, slot=slot), parse_mode="HTML")
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=["history"])
def cmd_history(message):
    if not chat_ok(message):
        return
    history = _load_json(PRINT_HISTORY_FILE, [])
    if not history:
        bot.reply_to(message, t("history_empty"))
        return
    last10 = history[-10:][::-1]
    res = t("history_title")
    for e in last10:
        grams = f"{float(e.get('grams', 0)):.1f}g" if e.get("grams") else "–"
        res  += t("history_item",
                  date=e.get("date", "?"),
                  filename=(e.get("filename", "?"))[:22],
                  duration=e.get("duration", "?"),
                  grams=grams)
    bot.reply_to(message, res)


@bot.message_handler(commands=["cam", "snapshot"])
def cmd_cam(message):
    if not chat_ok(message):
        return
    if not HA_AVAILABLE:
        bot.reply_to(message, t("ha_no_api"))
        return
    try:
        bot.send_chat_action(message.chat.id, "upload_photo")
    except Exception:
        pass
    img, err = ha_snapshot()
    if err:
        bot.reply_to(message, err)
        return
    try:
        ts = datetime.now(JERUSALEM).strftime("%H:%M:%S")
        bot.send_photo(message.chat.id, img, caption=t("cam_ok", time=ts))
    except Exception as e:
        bot.reply_to(message, t("cam_error", error=str(e)))


@bot.message_handler(commands=["light", "lamp"])
def cmd_light(message):
    if not chat_ok(message):
        return
    if not HA_AVAILABLE:
        bot.reply_to(message, t("ha_no_api"))
        return
    lstate, eid = ha_light_state()
    if not eid:
        bot.reply_to(message, t("light_no_entity"))
        return
    new_state = "off" if lstate == "on" else "on"
    err = ha_light_set(eid, new_state)
    if err:
        bot.reply_to(message, t("light_error", error=err))
    else:
        bot.reply_to(message, t("light_on") if new_state == "on" else t("light_off"))


@bot.message_handler(commands=["pause"])
def cmd_pause(message):
    if not chat_ok(message):
        return
    with _lock:
        if not _state["printing"]:
            bot.reply_to(message, t("ctrl_not_printing"))
            return
    bot.reply_to(message, t("ctrl_pause"))
    printer_cmd({"command": "pause"})


@bot.message_handler(commands=["resume"])
def cmd_resume(message):
    if not chat_ok(message):
        return
    bot.reply_to(message, t("ctrl_resume"))
    printer_cmd({"command": "resume"})


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    if not chat_ok(message):
        return
    with _lock:
        if not _state["printing"]:
            bot.reply_to(message, t("ctrl_not_printing"))
            return
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("✅ Yes, cancel", callback_data="cancel_yes"),
        telebot.types.InlineKeyboardButton("❌ No, keep going", callback_data="cancel_no"),
    )
    bot.reply_to(message, t("ctrl_cancel_ask"), reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data in ("cancel_yes", "cancel_no"))
def cb_cancel(call):
    if str(call.message.chat.id) != TELEGRAM_CHAT_ID:
        return
    bot.answer_callback_query(call.id)
    if call.data == "cancel_yes":
        bot.edit_message_text(t("ctrl_cancel_yes"), call.message.chat.id, call.message.message_id)
        printer_cmd({"command": "stop"})
    else:
        bot.edit_message_text(t("ctrl_cancel_no"), call.message.chat.id, call.message.message_id)


@bot.message_handler(commands=["spools", "inventory"])
def cmd_spools(message):
    if not chat_ok(message):
        return
    if not SPOOLMAN_URL:
        bot.reply_to(message, t("spoolman_no_url"))
        return
    spools = _spoolman_get("/api/v1/spool")
    if spools is None:
        bot.reply_to(message, "❌ Cannot reach Spoolman.")
        return
    if not spools:
        bot.reply_to(message, t("spools_title") + t("spools_empty"))
        return
    res = t("spools_title")
    for s in spools:
        rem = round(s.get("remaining_weight", 0))
        if rem <= 0:
            continue
        fil   = s.get("filament", {})
        emoji = color_to_emoji(fil.get("color_hex", ""))
        brand = fil.get("vendor", {}).get("name", "Unknown")
        mat   = fil.get("material", "Unknown")
        res  += t("spools_item", id=s.get("id"), emoji=emoji, brand=brand, material=mat, grams=rem)
    if len(res) > 4000:
        res = res[:4000] + "\n…"
    bot.reply_to(message, res)


@bot.message_handler(commands=["map"])
def cmd_map(message):
    """/map [slot 1-4] [spoolman_id]"""
    if not chat_ok(message):
        return
    if not SPOOLMAN_URL:
        bot.reply_to(message, t("spoolman_no_url"))
        return
    parts = message.text.strip().split()
    
    # Interactive mode
    if len(parts) == 1:
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("Slot 1", callback_data="map_1"),
            telebot.types.InlineKeyboardButton("Slot 2", callback_data="map_2"),
            telebot.types.InlineKeyboardButton("Slot 3", callback_data="map_3"),
            telebot.types.InlineKeyboardButton("Slot 4", callback_data="map_4"),
        )
        bot.reply_to(message, t("ask_slot_to_map"), reply_markup=markup)
        return

    if len(parts) < 3:
        bot.reply_to(message, t("spoolman_usage"))
        return
    try:
        slot = int(parts[1])
        sid  = int(parts[2])
        if not 1 <= slot <= 4:
            raise ValueError("Slot out of range")
        mapping = load_mapping()
        mapping[str(slot - 1)] = sid
        save_mapping(mapping)
        bot.reply_to(message, t("spoolman_mapped", sid=sid, slot=slot))
    except (ValueError, IndexError):
        bot.reply_to(message, t("spoolman_usage"))


@bot.message_handler(commands=["set"])
def cmd_set(message):
    """/set <slot 1-4> <brand> <material>"""
    if not chat_ok(message):
        return
    if not SPOOLMAN_URL:
        bot.reply_to(message, t("spoolman_no_url"))
        return
    parts = message.text.strip().split(maxsplit=3)
    if len(parts) < 4:
        bot.reply_to(message, t("set_usage"))
        return
    try:
        slot     = int(parts[1])
        brand    = parts[2]
        material = parts[3]
        if not 1 <= slot <= 4:
            raise ValueError("Slot out of range")

        ams_info = _ams_state.get(str(slot - 1), {})
        color    = ams_info.get("color", "FFFFFF")[:6]  # strip alpha if 8-char

        payload = {
            "filament": {
                "name":      f"{brand} {material}",
                "material":  material,
                "color_hex": color,
            },
            "remaining_weight": 1000,
        }
        result = _spoolman_post("/api/v1/spool", payload)
        if not result or not result.get("id"):
            bot.reply_to(message, t("set_fail"))
            return

        spool_id = result["id"]
        mapping  = load_mapping()
        mapping[str(slot - 1)] = spool_id
        save_mapping(mapping)
        log.info(f"Created Spoolman spool {spool_id} ({brand} {material}) → slot {slot - 1}")
        bot.reply_to(message, t("set_ok", slot=slot))

    except (ValueError, IndexError):
        bot.reply_to(message, t("set_usage"))
    except Exception as e:
        log.error(f"/set error: {e}")
        bot.reply_to(message, t("set_fail"))


@bot.message_handler(commands=["update"])
def cmd_update(message):
    if not chat_ok(message):
        return
    if not SPOOLMAN_URL:
        bot.reply_to(message, t("spoolman_no_url"))
        return
        
    spools = _spoolman_get("/api/v1/spool")
    if not spools:
        bot.reply_to(message, "❌ Cannot reach Spoolman or no spools found.")
        return

    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    btns = []
    
    # Take up to 80 spools to avoid telegram limits
    for s in spools[:80]:
        rem = round(s.get("remaining_weight", 0))
        fil = s.get("filament", {})
        color_hex = fil.get("color_hex", "")
        emoji = color_to_emoji(color_hex)
        brand = fil.get("vendor", {}).get("name", "Unknown")
        mat = fil.get("material", "Unknown")
        name = fil.get("name", "")
        
        label = f"#{s.get('id')} {emoji} {brand} {mat} {name} ({rem}g)"
        if len(label) > 60:
            label = label[:57] + "..."
            
        btns.append(telebot.types.InlineKeyboardButton(label, callback_data=f"update_{s.get('id')}"))

    markup.add(*btns)
    bot.send_message(message.chat.id, t("ask_update_spool"), parse_mode="HTML", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("update_"))
def cb_update(call):
    if not chat_ok(call.message):
        return
    spool_id = int(call.data.split("_")[1])
    
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
        
    msg = bot.send_message(call.message.chat.id, t("ask_new_weight", sid=spool_id))
    bot.register_next_step_handler(msg, process_spool_update_weight, spool_id)
    bot.answer_callback_query(call.id)


def process_spool_update_weight(message, spool_id):
    if message.text and message.text.strip().lower() == "/cancel":
        bot.reply_to(message, t("update_cancel"))
        return
        
    try:
        new_weight = float(message.text.strip())
        if new_weight < 0:
            raise ValueError()
            
        payload = {"remaining_weight": new_weight}
        if _spoolman_patch(f"/api/v1/spool/{spool_id}", payload):
            bot.reply_to(message, t("update_success", sid=spool_id, weight=new_weight))
        else:
            bot.reply_to(message, t("set_fail"))
    except ValueError:
        msg = bot.reply_to(message, t("invalid_number"))
        bot.register_next_step_handler(msg, process_spool_update_weight, spool_id)


@bot.message_handler(commands=["debug"])
def cmd_debug(message):
    if not chat_ok(message):
        return
    with _lock:
        state_copy = {k: v for k, v in _state.items() if k != "_raw_print"}
        raw        = _state.get("_raw_print", {})
    state_json = json.dumps(state_copy, indent=2, default=str)
    raw_json   = json.dumps(raw,        indent=2, default=str)
    msg = (
        "<b>🔍 Debug Info</b>\n\n"
        f"<b>Version:</b> {VERSION}\n"
        f"<b>HA API:</b> {'✅ enabled' if HA_AVAILABLE else '❌ disabled'}\n"
        f"<b>Spoolman:</b> {SPOOLMAN_URL or '❌ not configured'}\n\n"
        "<b>State:</b>\n<pre>" + html.escape(state_json) + "</pre>\n\n"
        "<b>Last Raw MQTT:</b>\n<pre>" + html.escape(raw_json) + "</pre>"
    )
    if len(msg) > 4000:
        msg = msg[:4000] + "\n… (truncated)"
    bot.reply_to(message, msg, parse_mode="HTML")


# ── MQTT Connection ───────────────────────────────────────────────────────────
def _make_client() -> mqtt.Client:
    # Handle paho-mqtt v1 vs v2 API differences gracefully
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    return client


def _connect_local():
    log.info(f"Testing local MQTT at {PRINTER_IP}:8883 …")
    try:
        s = socket.create_connection((PRINTER_IP, 8883), timeout=5)
        s.close()
    except Exception as e:
        log.warning(f"Local MQTT port not reachable: {e}")
        return None
    client = _make_client()
    client.username_pw_set("bblp", PRINTER_PASSWORD)
    log.info("Connecting via LOCAL MQTT…")
    client.connect(PRINTER_IP, 8883, keepalive=60)
    return client


def _connect_cloud():
    if not BAMBU_USERNAME or not BAMBU_PASSWORD_ or "example.com" in BAMBU_USERNAME:
        msg = (
            "❌ Local MQTT unreachable and Cloud credentials not configured.\n"
            "Check printer_ip and bambu_username / bambu_password in Add-on settings."
        )
        log.error(msg)
        tg_send(msg)
        return None

    log.info("Fetching Bambu Cloud token…")
    try:
        r = requests.post(
            "https://api.bambulab.com/v1/user-service/user/login",
            json={"account": BAMBU_USERNAME, "password": BAMBU_PASSWORD_},
            timeout=15,
        )
        data  = r.json()
        token = data.get("accessToken") or data.get("token")
        if not token:
            log.error(f"No token in response: {data}")
            tg_send("❌ Failed to get Bambu Cloud token. Check bambu_username and bambu_password.")
            return None
    except Exception as e:
        log.error(f"Cloud token fetch failed: {e}")
        tg_send(f"❌ Bambu Cloud login failed: {e}")
        return None

    client = _make_client()
    client.username_pw_set("bblp", token)
    log.info("Connecting via CLOUD MQTT…")
    client.connect("us.mqtt.bambulab.com", 8883, keepalive=60)
    return client


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info(f"Data dir: {DATA_DIR}")
    _restore_state()

    # Start Telegram polling in background thread
    threading.Thread(target=bot.infinity_polling, daemon=True, name="tg-polling").start()
    log.info("Telegram polling started")

    # Try local MQTT first, fall back to Bambu Cloud
    global _mqtt_client
    client = _connect_local() or _connect_cloud()

    if not client:
        msg = "❌ Could not connect to printer MQTT. Check HA Add-on logs."
        log.error(msg)
        tg_send(msg)
        sys.exit(1)

    _mqtt_client = client
    log.info("Entering MQTT loop…")
    try:
        # reconnect_delay_max added in paho 2.x — use plain loop_forever for compat
        client.loop_forever()
    except KeyboardInterrupt:
        tg_send(t("disconnected"))
        log.info("Stopped by user.")


if __name__ == "__main__":
    main()
