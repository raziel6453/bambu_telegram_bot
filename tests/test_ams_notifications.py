"""Offline regressions for AMS messages and their mapping button flow.

Load the production handlers without the module's configuration validation and
Telegram initialization so these tests need no credentials or third-party packages.
"""
import ast
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "bambu_telegram_bot" / "bambu_monitor.py"


class Keyboard:
    def __init__(self, **kwargs):
        self.buttons = []

    def row(self, *buttons):
        self.buttons.extend(buttons)

    add = row


class AmsNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.mapping = {}
        self.bot = Mock()
        self.env = {
            "json": json,
            "_lock": threading.Lock(),
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
        names = {"tg_send", "on_message", "chat_ok", "cb_map", "cb_setslot"}
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

                message = SimpleNamespace(chat=SimpleNamespace(id=123), message_id=1)
                self.env["cb_map"](SimpleNamespace(data=buttons[0].callback_data, message=message, id="map"))
                choice = self.bot.send_message.call_args.kwargs["reply_markup"].buttons[0]
                self.env["cb_setslot"](SimpleNamespace(data=choice.callback_data, message=message, id="save"))
                self.assertEqual(self.mapping, {str(i): 42 for i in range(tray + 1)})

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
