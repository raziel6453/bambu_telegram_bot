"""Authenticated LAN webhooks for selected OctoEverywhere notifications.

User intervention (12), bed cooled (15), and setup tests (16) are forwarded.
An optional Gadget handler receives warnings/pauses (7/8) and job lifecycle (1/2/3).
Without it, warnings are forwarded and Gadget pauses are retained for status only.
MQTT remains the source of ordinary print events.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit


log = logging.getLogger("bambu.octo")
PORT = 8765
WEBHOOK_PATH = "/octoeverywhere"
EVENTS = {7, 12, 15, 16}
MAX_BODY = 65536
MAX_QUEUE = 100
QUEUE_TTL = 15 * 60
REPEAT_WINDOW = 10 * 60
CROSS_SOURCE_WINDOW = 120


def error_code(value):
    """Normalize Bambu's numeric MQTT code or displayed hexadecimal code."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, int):
            number = value
        elif isinstance(value, str):
            value = value.strip().replace("_", "")
            if re.fullmatch(r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}", value):
                value = value.replace("-", "")
            if value.lower().startswith("0x") or re.fullmatch(r"[0-9a-fA-F]{8}", value):
                number = int(value, 16)
            else:
                number = int(value, 10)
        else:
            return None
        return f"{number:08X}" if 0 < number <= 0xFFFFFFFF else None
    except ValueError:
        return None


def safe_url(value, *, quick_view=False):
    """Only HTTPS links; snapshots are passed to Telegram, never fetched locally."""
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or any(c.isspace() for c in value)):
            return None
        if quick_view and not (host == "octoeverywhere.com" or host.endswith(".octoeverywhere.com")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host or host.endswith((".local", ".internal", ".localhost")):
                return None
        return value
    except ValueError:
        return None


def _text(value, limit):
    return str(value or "").replace("\x00", "")[:limit]


def notification_text(event, language, bed_temperature=None):
    he = language == "he"
    titles = {
        7: ("🚨 Gadget detected a possible print failure / spaghetti.", "🚨 Gadget זיהה חשד לכשל בהדפסה / ספגטי."),
        12: ("🙋 The printer needs your attention.", "🙋 המדפסת דורשת התערבות שלך."),
        15: ("❄️ The bed has cooled down.", "❄️ המיטה התקררה."),
        16: ("✅ OctoEverywhere test received by BambuBuddy.", "✅ בדיקת החיבור מ־OctoEverywhere התקבלה ב־BambuBuddy."),
    }
    lines = [titles[event["EventType"]][int(he)]]
    if event["EventType"] == 15:
        if (type(bed_temperature) in (int, float) and math.isfinite(bed_temperature)
                and 0 <= bed_temperature <= 150):
            lines.append(("🌡️ טמפרטורת המיטה: " if he else "🌡️ Bed temperature: ")
                         + f"{bed_temperature:.1f}".rstrip("0").rstrip(".") + "°C")
        else:
            lines.append("🌡️ טמפרטורת המיטה אינה זמינה כרגע." if he
                         else "🌡️ Bed temperature is currently unavailable.")
    if event.get("FileName"):
        lines.append(("📄 קובץ: " if he else "📄 File: ") + _text(event["FileName"], 120))
    if event["EventType"] == 12:
        if event.get("Error"):
            lines.append("🔎 " + _text(event["Error"], 220))
        if event.get("PlatformErrorCode"):
            code = error_code(event["PlatformErrorCode"]) or _text(event["PlatformErrorCode"], 40)
            lines.append(("קוד: " if he else "Code: ") + code)
    return "\n".join(lines)


class NotificationRelay:
    """Persist before acknowledging, serialize deliveries, and suppress repeats."""

    def __init__(self, path, sender, *, clock=time.time, gadget_handler=None):
        self.path = Path(path)
        self.sender = sender
        self.clock = clock
        self.gadget_handler = gadget_handler
        self.lock = threading.RLock()
        self.delivery_lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.server = None
        self.worker = None
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            if (not isinstance(self.state, dict) or not isinstance(self.state.get("secret"), str)
                    or len(self.state["secret"]) < 32
                    or not isinstance(self.state.get("pending"), list)
                    or not isinstance(self.state.get("seen"), dict)
                    or not isinstance(self.state.get("pauses"), dict)):
                raise ValueError("Invalid OctoEverywhere notification state")
        else:
            self.state = {"secret": secrets.token_urlsafe(32), "pending": [], "seen": {}, "pauses": {}}
            self._save()

    def _save(self):
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(self.state, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.path)

    def _prune(self):
        now = self.clock()
        self.state["seen"] = {k: expiry for k, expiry in self.state["seen"].items() if expiry > now}
        self.state["pauses"] = {k: v for k, v in self.state["pauses"].items()
                                if now - v["time"] < CROSS_SOURCE_WINDOW}
        self.state["pending"] = [r for r in self.state["pending"] if now - r["created"] < QUEUE_TTL]

    @staticmethod
    def _key(values):
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()

    def _pause_key(self, code, filename):
        code = error_code(code)
        # A known code AND matching filename are required; never hide an unknown
        # or different issue just because the printer is paused.
        return self._key([code, filename.strip().casefold()]) if code and filename else None

    def _enqueue(self, key, payload, pause_key=None, delay=0):
        with self.lock:
            self._prune()
            if key in self.state["seen"] or any(r["key"] == key for r in self.state["pending"]):
                return 200
            if len(self.state["pending"]) >= MAX_QUEUE:
                return 503
            item = {"key": key, "payload": payload, "pause_key": pause_key,
                    "created": self.clock(), "due": self.clock() + delay, "attempts": 0}
            self.state["pending"].append(item)
            try:
                self._save()
            except OSError:
                self.state["pending"].remove(item)
                return 503
            self.wake.set()
            return 200

    def receive(self, payload):
        if not isinstance(payload, dict):
            return 400
        key = payload.get("SecretKey")
        if not isinstance(key, str) or not hmac.compare_digest(key.encode(errors="replace"), self.state["secret"].encode()):
            return 403
        event_type = payload.get("EventType")
        if type(event_type) is not int:
            return 400
        lifecycle = bool(self.gadget_handler) and event_type in (1, 2, 3)
        if event_type not in EVENTS and event_type != 8 and not lifecycle:
            return 200  # Ordinary lifecycle events are still handled by MQTT.
        printer = payload.get("PrinterId")
        print_id = payload.get("PrintId")
        if not isinstance(printer, str) or not printer or len(printer) > 200:
            return 400
        if print_id is not None and (not isinstance(print_id, str) or len(print_id) > 200):
            return 400
        if (event_type in (7, 8, 15) or lifecycle) and not print_id:
            return 400
        print_id = print_id or ""
        event = {"EventType": event_type, "PrinterId": printer, "PrintId": print_id or "",
                 "FileName": _text(payload.get("FileName"), 500),
                 "Error": _text(payload.get("Error"), 1000),
                 "PlatformErrorCode": _text(payload.get("PlatformErrorCode"), 80),
                 "SnapshotUrl": safe_url(payload.get("SnapshotUrl")),
                 "QuickViewUrl": safe_url(payload.get("QuickViewUrl"), quick_view=True)}
        # Canonicalize before stringifying: numeric MQTT-format codes are decimal.
        code = error_code(payload.get("PlatformErrorCode"))
        if code:
            event["PlatformErrorCode"] = code
        if lifecycle:
            return self.gadget_handler(event)  # Correlation only; no duplicate lifecycle message.
        if event_type in (7, 8):
            # Retain receipt independently of Telegram delivery/dedup. These are
            # historical warnings, never evidence that monitoring is healthy.
            with self.lock:
                previous = self.state.get("last_gadget")
                self.state["last_gadget"] = {"event": event_type, "file": event["FileName"],
                                              "received": self.clock()}
                try:
                    self._save()
                except OSError:
                    if previous is None:
                        self.state.pop("last_gadget", None)
                    else:
                        self.state["last_gadget"] = previous
                    return 503
            if self.gadget_handler:
                return self.gadget_handler(event)
            if event_type == 8:
                return 200  # Status only; existing MQTT pause notices remain unchanged.
        details = (code or event["Error"].strip()) if event_type == 12 else ""
        event_key = self._key([printer, print_id, event_type, details])
        pause_key = self._pause_key(code, event["FileName"]) if event_type == 12 else None
        return self._enqueue(event_key, event, pause_key, delay=3 if event_type == 12 else 0)

    def gadget_status(self, language):
        he = language == "he"
        with self.lock:
            record = dict(self.state.get("last_gadget") or {})
        if not record:
            return ("🤖 Gadget: עדיין לא התקבלה התראה. מצב הניטור אינו ידוע."
                    if he else "🤖 Gadget: no alert received yet. Monitoring status is unknown.")
        elapsed = max(0, int(self.clock() - record["received"]))
        if elapsed < 60:
            age = "לפני פחות מדקה" if he else "less than a minute ago"
        elif elapsed < 3600:
            age = f"לפני {elapsed // 60} דקות" if he else f"{elapsed // 60} min ago"
        elif elapsed < 86400:
            age = f"לפני {elapsed // 3600} שעות" if he else f"{elapsed // 3600} h ago"
        else:
            age = f"לפני {elapsed // 86400} ימים" if he else f"{elapsed // 86400} days ago"
        if record["event"] == 8:
            warning = "Gadget השהה עקב חשד לכשל" if he else "Gadget paused a print due to failure"
        else:
            warning = "חשד לכשל / ספגטי" if he else "Possible failure / spaghetti"
        title = "🤖 Gadget — התראה אחרונה" if he else "🤖 Gadget — last alert"
        lines = [f"{title}: {warning} ({age})."]
        if record.get("file"):
            name = _text(record["file"], 80).replace("\n", " ").replace("\r", " ")
            lines.append(("קובץ: " if he else "File: ") + name)
        lines.append("זהו מידע היסטורי, לא מצב חי." if he else "Historical alert, not live status.")
        return "\n".join(lines)

    def enqueue_pause(self, text, code, filename):
        """Existing MQTT pause notices use the same worker for cross-source dedup."""
        payload = {"text": text}
        return self._enqueue(secrets.token_hex(16), payload, self._pause_key(code, filename)) == 200

    def flush_once(self):
        with self.delivery_lock:
            with self.lock:
                self._prune()
                record = next((r for r in self.state["pending"] if r["due"] <= self.clock()), None)
                if record is None:
                    return False
                source = "mqtt" if "text" in record["payload"] else "octo"
                prior = self.state["pauses"].get(record["pause_key"])
                duplicate = prior is not None and prior["source"] != source
            try:
                if not duplicate:
                    self.sender(record["payload"])
            except Exception as error:
                # Never log payloads, image URLs, tokens, or Telegram exceptions.
                log.warning("OctoEverywhere notification delivery failed (%s); will retry", type(error).__name__)
                with self.lock:
                    record["attempts"] += 1
                    record["due"] = self.clock() + min(60, 2 ** min(record["attempts"], 6))
                    self._save()
                return False
            with self.lock:
                event_type = record["payload"].get("EventType")
                if source == "octo":
                    ttl = 7 * 86400 if event_type == 15 else (30 if event_type == 16 else REPEAT_WINDOW)
                    self.state["seen"][record["key"]] = self.clock() + ttl
                    # Bound the persisted journal even after many distinct events.
                    if len(self.state["seen"]) > 2048:
                        oldest = min(self.state["seen"], key=self.state["seen"].get)
                        del self.state["seen"][oldest]
                if record["pause_key"] and not duplicate:
                    self.state["pauses"][record["pause_key"]] = {"source": source, "time": self.clock()}
                if record in self.state["pending"]:
                    self.state["pending"].remove(record)
                self._save()
            return True

    def _deliver(self):
        while not self.stop.is_set():
            try:
                sent = self.flush_once()
            except Exception as error:
                log.error("OctoEverywhere delivery state error (%s)", type(error).__name__)
                sent = False
            if not sent:
                self.wake.wait(1)
                self.wake.clear()

    def start(self, host="0.0.0.0", port=PORT):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, *args):
                pass  # No request paths, credentials, or payloads in access logs.

            def _reply(self, status):
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                if self.path != WEBHOOK_PATH:
                    self._reply(404)
                    return
                if self.headers.get("Transfer-Encoding"):
                    self._reply(400)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_BODY:
                        self._reply(413)
                        return
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        self._reply(400)
                        return
                    payload = json.loads(raw)
                except (ValueError, UnicodeError, OSError):
                    self._reply(400)
                    return
                try:
                    status = relay.receive(payload)
                except Exception:
                    log.error("OctoEverywhere webhook could not be queued")
                    status = 503
                self._reply(status)

        self.server = HTTPServer((host, port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="octo-webhook").start()
        self.worker = threading.Thread(target=self._deliver, daemon=True, name="octo-alerts")
        self.worker.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.worker:
            self.worker.join(timeout=2)

    def setup_values(self, hostname=None):
        host = (hostname or socket.gethostname()).replace("_", "-")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise ValueError("Invalid webhook hostname")
        return f"http://{host}:{PORT}{WEBHOOK_PATH}", self.state["secret"]


def start_notifications(enabled, data_dir, sender, *, gadget_handler=None):
    if enabled is not True:
        return None
    try:
        relay = NotificationRelay(Path(data_dir) / "octoeverywhere_notifications.json", sender,
                                  gadget_handler=gadget_handler)
        relay.start()
    except Exception as error:
        log.error("OctoEverywhere notifications could not start (%s)", type(error).__name__)
        return None
    log.info("OctoEverywhere notifications enabled (Gadget, intervention, bed cooled)")
    return relay
