"""Test the complete guided inventory flow without Telegram or Spoolman."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("spool_wizard", Path(__file__).resolve().parents[1] / "bambu_telegram_bot" / "spool_wizard.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Keyboard:
    def __init__(self, **kwargs):
        self.buttons = []

    def add(self, *buttons):
        self.buttons.extend(buttons)


class SpoolWizardTest(unittest.TestCase):
    def setUp(self):
        self.bot = Mock()
        self.post = Mock(side_effect=[{"id": 17}, {"id": 42}])
        types = SimpleNamespace(InlineKeyboardMarkup=Keyboard,
                                InlineKeyboardButton=lambda text, **kw: SimpleNamespace(text=text, **kw))
        self.wizard = module.SpoolWizard(self.bot, types, "123", "en", True, self.post)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=123), from_user=SimpleNamespace(id=9), text="/addspool")

    def button(self, suffix):
        return next(b.callback_data for b in self.bot.send_message.call_args.kwargs["reply_markup"].buttons
                    if b.callback_data.endswith(":" + suffix))

    def click(self, data, user=9):
        self.wizard.callback(SimpleNamespace(id="query", data=data, message=self.message,
                                             from_user=SimpleNamespace(id=user)))

    def enter(self, value):
        self.message.text = value
        self.wizard.text(self.message)

    def review(self):
        self.wizard.start(self.message)
        self.click(self.button("PLA"))
        self.click(self.button("FF0000"))
        self.click(self.button("1000"))
        return self.button("save")

    def test_complete_flow_creates_inventory_then_offers_optional_mapping(self):
        save = self.review()
        self.post.assert_not_called()
        self.click(save)
        self.assertEqual(self.post.call_args_list[0].args, ("/api/v1/filament", {
            "name": "PLA #FF0000", "material": "PLA", "color_hex": "FF0000",
            "diameter": 1.75, "density": 1.24,
        }))
        self.assertEqual(self.post.call_args_list[1].args, ("/api/v1/spool", {
            "filament_id": 17, "initial_weight": 1000.0, "used_weight": 0,
        }))
        buttons = self.bot.send_message.call_args.kwargs["reply_markup"].buttons
        self.assertEqual([b.callback_data for b in buttons], [f"setslot_{i}_42" for i in range(1, 5)])
        self.assertEqual(self.wizard.sessions, {})
        self.click(save)
        self.assertEqual(self.post.call_count, 2)

    def test_custom_color_and_fractional_grams(self):
        self.wizard.start(self.message)
        self.click(self.button("PETG"))
        self.enter("#12abef")
        self.enter("735.5g")
        self.click(self.button("save"))
        self.assertEqual(self.post.call_args_list[0].args[1]["color_hex"], "12ABEF")
        self.assertEqual(self.post.call_args_list[1].args[1]["initial_weight"], 735.5)

    def test_invalid_values_stay_in_current_step_without_writing(self):
        self.wizard.start(self.message)
        self.click(self.button("PLA"))
        key = self.wizard.key(self.message)
        self.enter("not a color")
        self.assertEqual(self.wizard.sessions[key]["step"], "color")
        self.enter("#abcdef")
        for value in ("0", "-10", "nan", "inf", "10001", "a lot"):
            self.enter(value)
            self.assertEqual(self.wizard.sessions[key]["step"], "weight")
        self.post.assert_not_called()

    def test_cancel_does_not_write(self):
        self.review()
        self.assertTrue(self.wizard.accepts_text(SimpleNamespace(
            chat=self.message.chat, from_user=self.message.from_user, text="/cancel")))
        self.enter("/cancel")
        self.assertEqual(self.wizard.sessions, {})
        self.post.assert_not_called()

    def test_restart_and_stale_buttons_do_not_advance_new_draft(self):
        old_save = self.review()
        self.click(self.button("restart"))
        self.click(old_save)
        self.assertEqual(self.wizard.sessions[self.wizard.key(self.message)]["step"], "material")
        self.post.assert_not_called()

    def test_another_group_member_cannot_save_owners_draft(self):
        save = self.review()
        self.click(save, user=55)
        self.post.assert_not_called()
        self.click(save)
        self.assertEqual(self.post.call_count, 2)

    def test_other_chats_cannot_start_or_enter_values(self):
        self.message.chat.id = 999
        self.wizard.start(self.message)
        self.assertFalse(self.wizard.accepts_text(self.message))
        self.assertEqual(self.wizard.sessions, {})
        self.bot.send_message.assert_not_called()

    def test_expired_draft_cannot_save(self):
        save = self.review()
        self.wizard.sessions[self.wizard.key(self.message)]["updated"] -= 901
        self.click(save)
        self.post.assert_not_called()

    def test_failure_consumes_save_and_warns_to_check_inventory(self):
        for responses in ([None], [{"id": 17}, None]):
            with self.subTest(responses=responses):
                self.post.reset_mock(side_effect=True)
                self.post.side_effect = responses
                save = self.review()
                self.click(save)
                self.assertIn("Check /spools", self.bot.send_message.call_args.args[1])
                count = self.post.call_count
                self.click(save)
                self.assertEqual(self.post.call_count, count)

    def test_hebrew_flow(self):
        self.wizard.he = True
        self.wizard.start(self.message)
        self.assertIn("הוספת ספול", self.bot.send_message.call_args.args[1])
        self.click(self.button("PLA"))
        self.click(self.button("FF0000"))
        self.click(self.button("500"))
        self.assertIn("אדום", self.bot.send_message.call_args.args[1])
        self.click(self.button("save"))
        self.assertIn("נוסף למלאי", self.bot.send_message.call_args.args[1])

    def test_disabled_spoolman_and_other_commands(self):
        self.wizard.enabled = False
        self.wizard.start(self.message)
        self.assertEqual(self.wizard.sessions, {})
        self.wizard.enabled = True
        self.wizard.start(self.message)
        self.message.text = "/status"
        self.assertFalse(self.wizard.accepts_text(self.message))


if __name__ == "__main__":
    unittest.main()
