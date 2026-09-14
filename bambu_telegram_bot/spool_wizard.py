"""Button-based inventory creation, isolated from printer control handlers."""
import math
import re
import threading
import time
import uuid

# Typical densities for generic 1.75 mm filament; brands/blends may differ.
MATERIALS = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21}
COLORS = [
    ("⚫ Black", "⚫ שחור", "000000"), ("⚪ White", "⚪ לבן", "FFFFFF"),
    ("🔴 Red", "🔴 אדום", "FF0000"), ("🔵 Blue", "🔵 כחול", "0000FF"),
    ("🟢 Green", "🟢 ירוק", "008000"), ("🟡 Yellow", "🟡 צהוב", "FFFF00"),
    ("🟠 Orange", "🟠 כתום", "FF8000"), ("🟣 Purple", "🟣 סגול", "800080"),
    ("🌸 Pink", "🌸 ורוד", "FF69B4"), ("🩶 Gray", "🩶 אפור", "808080"),
    ("🟤 Brown", "🟤 חום", "8B4513"),
]


class SpoolWizard:
    def __init__(self, bot, types, chat_id, language, enabled, post):
        self.bot, self.types = bot, types
        self.chat_id, self.he = str(chat_id), language == "he"
        self.enabled, self.post = enabled, post
        self.sessions = {}
        self.lock = threading.RLock()

    def tr(self, en, he):
        return he if self.he else en

    def key(self, message):
        return (str(message.chat.id), message.from_user.id)

    def allowed(self, message):
        return str(message.chat.id) == self.chat_id

    def register(self):
        # Register before print /cancel and other commands. Only the owner of an
        # active draft is intercepted; /cancel then cancels that draft, not a print.
        self.bot.message_handler(commands=["addspool"])(self.start)
        self.bot.callback_query_handler(func=lambda c: c.data.startswith("sw:"))(self.callback)
        self.bot.message_handler(func=self.accepts_text, content_types=["text"])(self.text)

    def accepts_text(self, message):
        with self.lock:
            return self.allowed(message) and self.key(message) in self.sessions and (
                not message.text.startswith("/") or message.text.split()[0] == "/cancel")

    def start(self, message):
        if not self.allowed(message):
            return
        if not self.enabled:
            self.bot.reply_to(message, self.tr("Configure Spoolman first.", "יש להגדיר Spoolman תחילה."))
            return
        with self.lock:
            state = {"step": "material", "token": uuid.uuid4().hex[:12], "updated": time.monotonic()}
            self.sessions[self.key(message)] = state
            self.show(message.chat.id, state)

    def show(self, chat, state):
        state["updated"] = time.monotonic()
        step = state["step"]
        if step == "material":
            title = self.tr("🧵 Add spool: choose material", "🧵 הוספת ספול: בחירת חומר")
            choices = [(m, m) for m in MATERIALS]
        elif step == "color":
            title = self.tr("Choose a color, or type a hex color such as #12ABEF.",
                            "בחר צבע, או הקלד קוד צבע כגון #12ABEF.")
            choices = [(c[1] if self.he else c[0], c[2]) for c in COLORS]
        elif step == "weight":
            title = self.tr("Choose weight or type grams (e.g. 750). Filament only, excluding the empty spool.",
                            "בחר משקל או הקלד גרמים (למשל 750). חוט בלבד, ללא משקל הספול הריק.")
            choices = [(f"{w} g", str(w)) for w in (250, 500, 1000)]
        else:
            title = self.tr("Save this spool?", "לשמור את הספול?") + (
                f"\n{state['material']} · {state['color_name']}\n{state['weight']:g} g")
            choices = [(self.tr("✅ Save", "✅ שמירה"), "save"),
                       (self.tr("↩ Start over", "↩ התחלה מחדש"), "restart")]
        keyboard = self.types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(*(self.types.InlineKeyboardButton(label, callback_data=f"sw:{state['token']}:{step}:{value}")
                       for label, value in choices))
        keyboard.add(self.types.InlineKeyboardButton(self.tr("✖ Cancel", "✖ ביטול"),
                     callback_data=f"sw:{state['token']}:{step}:cancel"))
        self.bot.send_message(chat, title, reply_markup=keyboard)

    def callback(self, call):
        # A callback's message belongs to the bot, so use the clicking user's ID.
        if str(call.message.chat.id) != self.chat_id:
            return
        key = (str(call.message.chat.id), call.from_user.id)
        with self.lock:
            state = self.sessions.get(key)
            parts = call.data.split(":", 3)
            if (not state or len(parts) != 4 or parts[1] != state["token"]
                    or parts[2] != state["step"] or time.monotonic() - state["updated"] > 900):
                self.bot.answer_callback_query(call.id, self.tr("Use /addspool to start a new draft.",
                                                               "התחל מחדש עם /addspool."))
                return
            self.bot.answer_callback_query(call.id)
            self.advance(call.message.chat.id, key, state, parts[3])

    def text(self, message):
        if not self.allowed(message):
            return
        with self.lock:
            key = self.key(message)
            state = self.sessions.get(key)
            if not state:
                return
            if time.monotonic() - state["updated"] > 900:
                self.sessions.pop(key, None)
                self.bot.reply_to(message, self.tr("Draft expired. Start again with /addspool.",
                                                  "הטיוטה פגה. התחל מחדש עם /addspool."))
                return
            value = (message.text or "").strip()
            if value == "/cancel":
                self.advance(message.chat.id, key, state, "cancel")
            elif state["step"] in ("color", "weight"):
                self.advance(message.chat.id, key, state, value)
            else:
                self.bot.reply_to(message, self.tr("Use the buttons below, or /cancel.",
                                                  "השתמש בכפתורים או ב־/cancel."))

    def advance(self, chat, key, state, value):
        if value == "cancel":
            self.sessions.pop(key, None)
            self.bot.send_message(chat, self.tr("Spool draft cancelled.", "הוספת הספול בוטלה."))
            return
        step = state["step"]
        if step == "material" and value in MATERIALS:
            state.update(material=value, step="color")
        elif step == "color":
            color = value.lstrip("#").upper()
            if not re.fullmatch(r"[0-9A-F]{6}", color):
                self.bot.send_message(chat, self.tr("Choose a color or enter six hex digits, e.g. #12ABEF.",
                                                   "בחר צבע או הקלד קוד בן שש ספרות כגון #12ABEF."))
                return
            name = next((c[1] if self.he else c[0] for c in COLORS if c[2] == color), f"#{color}")
            state.update(color=color, color_name=name, step="weight")
        elif step == "weight":
            try:
                weight = float(value.lower().removesuffix("g").strip())
                if not math.isfinite(weight) or not 0 < weight <= 10000:
                    raise ValueError()
            except ValueError:
                self.bot.send_message(chat, self.tr("Enter a weight above 0 and up to 10000 grams.",
                                                   "הקלד משקל גדול מאפס ועד 10000 גרם."))
                return
            state.update(weight=weight, step="confirm")
        elif step == "confirm" and value == "restart":
            state.clear()
            state.update(step="material", token=uuid.uuid4().hex[:12])
        elif step == "confirm" and value == "save":
            # Consume before making requests: repeated taps cannot create duplicates.
            self.sessions.pop(key, None)
            self.save(chat, state)
            return
        else:
            return
        self.show(chat, state)

    def save(self, chat, state):
        # Explicit diameter/density are required by Spoolman's filament API.
        try:
            filament = self.post("/api/v1/filament", {
                "name": f"{state['material']} #{state['color']}",
                "material": state["material"], "color_hex": state["color"],
                "diameter": 1.75, "density": MATERIALS[state["material"]],
            })
            if not filament or not filament.get("id"):
                raise ValueError("Filament creation did not return an ID")
            spool = self.post("/api/v1/spool", {
                "filament_id": filament["id"], "initial_weight": state["weight"], "used_weight": 0,
            })
            if not spool or not spool.get("id"):
                raise ValueError("Spool creation did not return an ID")
        except Exception:
            self.bot.send_message(chat, self.tr(
                "Could not confirm the save. Check /spools and Spoolman before trying again; part of the request may have succeeded.",
                "לא ניתן לאשר שהשמירה הצליחה. בדוק /spools ואת Spoolman לפני ניסיון נוסף; ייתכן שחלק מהבקשה נשמר."))
            return
        keyboard = self.types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(*(self.types.InlineKeyboardButton(
            self.tr(f"Map to slot {slot}", f"שיוך לסלוט {slot}"), callback_data=f"setslot_{slot}_{spool['id']}")
            for slot in range(1, 5)))
        self.bot.send_message(chat, self.tr(
            f"✅ Spool #{spool['id']} added to inventory.\n{state['material']} · {state['color_name']} · {state['weight']:g} g\nOptional: map it to an AMS slot below. Otherwise, you're done.",
            f"✅ ספול #{spool['id']} נוסף למלאי.\n{state['material']} · {state['color_name']} · {state['weight']:g} גרם\nאפשר לשייך לסלוט AMS בכפתורים. ללא שיוך, ההוספה הסתיימה."), reply_markup=keyboard)
