"""Offline regressions for AMS messages and their mapping button flow.

Load the production handlers without the module's configuration validation and
Telegram initialization so these tests need no credentials or third-party packages.
"""
import ast
import importlib.util
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "bambu_telegram_bot" / "bambu_monitor.py"
spec = importlib.util.spec_from_file_location("spool_display", SOURCE.with_name("spool_display.py"))
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class Keyboard:
    def __init__(self, **kwargs):
        self.buttons = []
        self.row_width = kwargs.get("row_width", 3)

    def row(self, *buttons):
        self.buttons.extend(buttons)

    add = row


class AmsNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.mapping = {}
        self.bot = Mock()
        self.env = {
            "_diagnostics": Mock(), "_pause_reason": Mock(),
            "_octo_notifications": None,
            "json": json,
            "time": time,
            "_lock": threading.Lock(),
            "_mapping_lock": threading.RLock(),
            "_pending_maps": {},
            "_spool_wizard": SimpleNamespace(lock=threading.RLock(), sessions={}),
            "slot_keyboard": display.slot_keyboard,
            "spool_description": display.spool_description,
            "split_messages": display.split_messages,
            "_status_event": threading.Event(),
            "_ams_state": {},
            "_alerted_slots": set(),
            "_initial_ams_sync_done": False,
            "bot": self.bot,
            "log": Mock(),
            "TELEGRAM_CHAT_ID": "123",
            "SPOOLMAN_URL": "http://spoolman.invalid",
            "telebot": SimpleNamespace(types=SimpleNamespace(
                InlineKeyboardMarkup=Keyboard,
                InlineKeyboardButton=lambda text, **kw: SimpleNamespace(text=text, **kw),
            )),
            "t": lambda key, **kw: f"{key}: {kw}",
            "load_mapping": lambda: dict(self.mapping),
            "save_mapping": lambda value: self.mapping.update(value),
            "_spoolman_get": lambda path: [{"id": 42, "remaining_weight": 500}],
            "color_to_emoji": lambda color: "",
        }
        tree = ast.parse(SOURCE.read_text())
        names = {"tg_send", "_notify_pause", "on_message", "chat_ok", "cb_map", "cb_setslot"}
        names.update({"_mapping_key", "_clear_mapping", "_save_spool_mapping", "_mapping_accepts_text",
                      "process_mapping_reply", "_show_mapping_choices", "cmd_map", "cmd_spools", "cmd_ams"})
        selected = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                node.decorator_list = []
                selected.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_state"
                for target in node.targets
            ):
                selected.append(node)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), self.env)

    def report(self, trays):
        payload = {"print": {"ams": {"ams": [{"id": "0", "tray": trays}]}}}
        self.env["on_message"](None, None, SimpleNamespace(payload=json.dumps(payload).encode()))

    def test_insertion_buttons_map_each_of_the_four_slots(self):
        self.report([{"id": str(i), "remain": -1} for i in range(4)])
        self.bot.send_message.assert_not_called()
        for tray in range(4):
            with self.subTest(tray=tray):
                self.bot.reset_mock()
                self.report([{"id": str(tray), "remain": 100, "tray_type": "PLA"}])
                self.bot.send_message.assert_called_once()
                notification = self.bot.send_message.call_args
                self.assertEqual(notification.args[0], "123")
                buttons = notification.kwargs["reply_markup"].buttons
                self.assertEqual(buttons[0].callback_data, f"map_{tray + 1}")
                self.assertEqual(buttons[1].callback_data, f"create_spool_{tray}")

                message = self.message()
                self.env["cb_map"](self.callback(buttons[0].callback_data, message))
                choice = self.bot.send_message.call_args.kwargs["reply_markup"].buttons[0]
                self.env["cb_setslot"](self.callback(choice.callback_data, message))
                self.assertEqual(self.mapping, {str(i): 42 for i in range(tray + 1)})

    def test_mapping_shows_full_names_and_reaches_spools_past_old_limit(self):
        name = "Matte PLA+ special collection - Very Long Color Name <Blue & Green>"
        spools = [{"id": i, "remaining_weight": 500,
                   "filament": {"name": name, "material": "PLA",
                                "vendor": {"name": "Example"}}} for i in range(1, 86)]
        self.env["_spoolman_get"] = lambda path: spools
        message = self.message()
        self.env["cb_map"](self.callback("map_2", message))
        sent = self.bot.send_message.call_args
        self.assertIn(name, sent.args[1])
        self.assertIsNone(sent.kwargs["parse_mode"])
        self.assertIn("map_2_1", [b.callback_data for b in sent.kwargs["reply_markup"].buttons])
        self.env["cb_map"](self.callback("map_2_14", message))
        edited = self.bot.edit_message_text.call_args
        self.assertIn("#85", edited.args[0])
        self.assertIn("setslot_2_85", [b.callback_data for b in edited.kwargs["reply_markup"].buttons])

    def message(self, text="", user=9, chat=123):
        return SimpleNamespace(chat=SimpleNamespace(id=chat), from_user=SimpleNamespace(id=user),
                               message_id=1, text=text)

    def callback(self, data, message=None, user=9):
        return SimpleNamespace(data=data, message=message or self.message(), id="query",
                               from_user=SimpleNamespace(id=user))

    def test_typed_spool_id_maps_selected_slot_and_consumes_prompt(self):
        self.env["cb_map"](self.callback("map_3"))
        message = self.message("42")
        self.assertTrue(self.env["_mapping_accepts_text"](message))
        self.env["process_mapping_reply"](message)
        self.assertEqual(self.mapping, {"2": 42})
        self.assertFalse(self.env["_mapping_accepts_text"](message))
        self.assertEqual(self.env["_pending_maps"], {})
        self.bot.edit_message_reply_markup.assert_called()

    def test_invalid_or_unavailable_spools_do_not_map_and_allow_retry(self):
        self.env["cb_map"](self.callback("map_1"))
        for value in ("wrong", "0", "-42", "42.5", "999"):
            self.env["process_mapping_reply"](self.message(value))
            self.assertEqual(self.mapping, {})
            self.assertTrue(self.env["_pending_maps"])
        for result in (None, [], [{"id": 42, "remaining_weight": 0}],
                       [{"id": 42, "remaining_weight": 500, "archived": True}]):
            self.env["_spoolman_get"] = lambda path: result
            self.env["process_mapping_reply"](self.message("42"))
            self.assertEqual(self.mapping, {})
            self.assertTrue(self.env["_pending_maps"])
        self.env["_spoolman_get"] = lambda path: [{"id": 42, "remaining_weight": 0.4}]
        self.env["process_mapping_reply"](self.message("#42"))
        self.assertEqual(self.mapping, {"0": 42})

    def test_prompt_is_scoped_to_user_and_chat_and_expires(self):
        self.env["cb_map"](self.callback("map_1"))
        for message in (self.message("42", user=55), self.message("42", chat=999)):
            self.assertFalse(self.env["_mapping_accepts_text"](message))
            self.env["process_mapping_reply"](message)
        self.assertEqual(self.mapping, {})
        self.env["_pending_maps"][("123", 9)]["updated"] -= 901
        self.env["process_mapping_reply"](self.message("42"))
        self.assertEqual(self.mapping, {})
        self.assertEqual(self.env["_pending_maps"], {})
        self.assertIn("map_expired", self.bot.reply_to.call_args.args[1])

    def test_inventory_keeps_prompt_other_commands_and_cancel_exit(self):
        self.env["cb_map"](self.callback("map_1"))
        for command in ("/spools", "/inventory@MyBot"):
            self.assertFalse(self.env["_mapping_accepts_text"](self.message(command)))
            self.assertTrue(self.env["_pending_maps"])
        self.assertTrue(self.env["_mapping_accepts_text"](self.message("/cancel")))
        self.env["process_mapping_reply"](self.message("/cancel"))
        self.assertEqual(self.env["_pending_maps"], {})
        self.assertEqual(self.mapping, {})
        self.env["cb_map"](self.callback("map_1"))
        self.assertFalse(self.env["_mapping_accepts_text"](self.message("/addspool")))
        self.assertEqual(self.env["_pending_maps"], {})

    def test_latest_slot_and_button_selection_clear_numeric_prompt(self):
        self.env["cb_map"](self.callback("map_1"))
        self.env["cb_map"](self.callback("map_4"))
        self.env["process_mapping_reply"](self.message("42"))
        self.assertEqual(self.mapping, {"3": 42})
        self.env["cb_map"](self.callback("map_2"))
        self.env["cb_setslot"](self.callback("setslot_2_42"))
        self.assertEqual(self.mapping, {"3": 42, "1": 42})
        self.assertFalse(self.env["_mapping_accepts_text"](self.message("42")))

    def test_slot_keyboards_use_same_order_for_map_and_ams(self):
        self.env["cmd_map"](self.message("/map"))
        keyboard = self.bot.reply_to.call_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.row_width, 2)
        self.assertEqual([b.text for b in keyboard.buttons], ["1", "3", "2", "4"])
        self.assertEqual([b.callback_data for b in keyboard.buttons], [f"map_{i}" for i in (1, 3, 2, 4)])
        self.env["_ams_state"] = {"0": {"type": "Unknown"}}
        self.env["cmd_ams"](self.message("/ams"))
        keyboard = self.bot.reply_to.call_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.row_width, 2)
        self.assertEqual([b.callback_data for b in keyboard.buttons], [f"map_{i}" for i in (1, 3, 2, 4)])

    def test_map_slot_argument_prompts_and_direct_mapping_validates(self):
        self.env["cmd_map"](self.message("/map 2"))
        self.env["process_mapping_reply"](self.message("42"))
        self.assertEqual(self.mapping, {"1": 42})
        for command in ("/map 5 42", "/map 1 999", "/map 1 -1", "/map x", "/map 1 42 extra"):
            self.env["cmd_map"](self.message(command))
            self.assertEqual(self.mapping, {"1": 42})
        self.env["cmd_map"](self.message("/map 4 42"))
        self.assertEqual(self.mapping, {"1": 42, "3": 42})

    def test_inventory_has_all_full_names_without_cutting_long_lists(self):
        spools = [{"id": i, "remaining_weight": 700,
                   "filament": {"name": f"Full filament {i} <Blue & Green> " + "🌸" * 120,
                                "vendor": {"name": "YOOPAI"}, "material": "PLA"}}
                  for i in range(1, 64)]
        self.env["_spoolman_get"] = lambda path: spools
        self.env["cmd_spools"](self.message("/spools"))
        calls = self.bot.reply_to.call_args_list
        self.assertGreater(len(calls), 1)
        complete = "".join(call.args[1] for call in calls)
        for spool in spools:
            self.assertIn(spool["filament"]["name"], complete)
        for call in calls:
            self.assertLessEqual(len(call.args[1].encode("utf-16-le")) // 2, 3500)
            self.assertIsNone(call.kwargs["parse_mode"])

    def test_split_messages_preserves_even_an_oversized_single_name(self):
        text = "single name: " + "🧵" * 4500
        chunks = list(display.split_messages(text))
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(c.encode("utf-16-le")) // 2 <= 3500 for c in chunks))

    def test_initial_loaded_spools_and_partial_updates_do_not_notify(self):
        self.report([{"id": "0", "remain": 100, "tray_type": "PLA"}])
        self.report([{"id": "0", "tray_color": "FF0000"}])
        self.report([{"id": "0", "remain": 100}])
        self.bot.send_message.assert_not_called()
        self.assertEqual(self.env["_ams_state"]["0"]["remain"], 100)

    def test_repeated_insert_report_sends_only_one_notification(self):
        self.report([{"id": "0", "remain": -1}])
        self.report([{"id": "0", "remain": 100}])
        self.report([{"id": "0", "remain": 100}])
        self.bot.send_message.assert_called_once()

    def test_plain_messages_still_work_and_send_errors_are_caught(self):
        self.env["tg_send"]("hello")
        self.bot.send_message.assert_called_once_with("123", "hello", reply_markup=None)
        self.bot.send_message.side_effect = RuntimeError("offline")
        self.env["tg_send"]("hello", reply_markup=Keyboard())
        self.env["log"].error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
