"""Shared, unabridged Telegram inventory text and AMS slot buttons."""


def slot_keyboard(types, spool_id=None):
    # Match the user's AMS Lite: top row 1, 3; bottom row 2, 4.
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*(types.InlineKeyboardButton(
        str(slot), callback_data=(f"map_{slot}" if spool_id is None
                                  else f"setslot_{slot}_{spool_id}"))
        for slot in (1, 3, 2, 4)))
    return markup


def spool_description(spool, emoji):
    filament = spool.get("filament") or {}
    brand = (filament.get("vendor") or {}).get("name") or "Unknown"
    material = filament.get("material") or "Unknown"
    name = filament.get("name") or material
    weight = spool.get("remaining_weight")
    grams = f"{weight:g}" if weight is not None else "?"
    return f"#{spool['id']} · {brand} · {material}\n{name}\n{emoji} {grams} g"


def split_messages(text, limit=3500):
    """Split at entry/line boundaries when possible; preserve every character.

    Count UTF-16 units conservatively for Telegram, including emoji.
    """
    while text:
        units = end = 0
        for char in text:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > limit:
                break
            units += size
            end += 1
        if end < len(text):
            boundary = text.rfind("\n\n", 0, end)
            if boundary < 0:
                boundary = text.rfind("\n", 0, end)
            if boundary > 0:
                end = boundary + 1
        yield text[:end]
        text = text[end:]
