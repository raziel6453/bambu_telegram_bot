"""Offline AI requests, privacy, quotas, conversation lifecycle and bot routing."""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1] / "bambu_telegram_bot"
sys.path.insert(0, str(APP))
import ai_chat as ai


def options(**overrides):
    return {"ai_enabled": True, "openai_api_key": "test-openai-key",
            "octoeverywhere_mcp_token": "test-octo-key", "telegram_chat_id": "123",
            "language": "en", **overrides}


def response(text="Your print is running.", **overrides):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text}]}], **overrides}


def message(text="/ai", chat=123, kind="private", user=123):
    return SimpleNamespace(chat=SimpleNamespace(id=chat, type=kind),
                           from_user=SimpleNamespace(id=user), text=text)


class AiClientTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = ai.AiClient(options(), self.directory.name)
        self.http = Mock(status_code=200)
        self.http.json.return_value = response()
        self.post = self.enterContext(patch.object(ai.requests, "post", return_value=self.http))

    def test_request_limits_tools_to_reads_and_keeps_secrets_out_of_context_and_disk(self):
        self.assertEqual(self.client.answer("How is it?", [], {"printer": {"age_seconds": 2}}),
                         "Your print is running.")
        args, kwargs = self.post.call_args
        self.assertEqual(args, (ai.RESPONSES_URL,))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (5, 90))
        payload = kwargs["json"]
        self.assertFalse(payload["store"])
        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertEqual(payload["max_output_tokens"], 1200)
        self.assertEqual(payload["max_tool_calls"], 4)
        tool = payload["tools"][0]
        self.assertEqual(tool["server_url"], ai.MCP_URL)
        self.assertEqual(tool["allowed_tools"], {"read_only": True})
        self.assertEqual(tool["require_approval"], {"never": {"read_only": True}})
        self.assertEqual(tool["authorization"], "test-octo-key")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-openai-key")
        for secret in ("test-octo-key", "test-openai-key"):
            self.assertNotIn(secret, json.dumps(payload["input"]))
            self.assertNotIn(secret, self.client.usage_file.read_text())
        self.assertEqual(self.client.usage_file.stat().st_mode & 0o777, 0o600)

    def test_disabled_missing_key_and_invalid_config_never_call_api(self):
        for changes, reason in [({"ai_enabled": False}, "disabled"),
                                ({"ai_enabled": "true"}, "disabled"),
                                ({"openai_api_key": ""}, "key"),
                                ({"ai_daily_limit": True}, "config"),
                                ({"ai_daily_limit": 0}, "config"),
                                ({"ai_model": "bad model"}, "config"),
                                ({"openai_api_key": "key\nheader"}, "config")]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ai.AiError, f"^{reason}$"):
                ai.AiClient(options(**changes), self.directory.name).answer("Hello", [], {})
        self.post.assert_not_called()
        self.assertFalse(self.client.usage_file.exists())

    def test_no_mcp_token_still_supports_context_chat(self):
        client = ai.AiClient(options(octoeverywhere_mcp_token=""), self.directory.name)
        client.answer("What PLA is available?", [], {"inventory": {"available": True}})
        self.assertNotIn("tools", self.post.call_args.kwargs["json"])

    def test_recent_text_history_and_fresh_context_are_sent_without_response_ids(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(20)]
        self.client.answer("And now?", history, {"current": 95})
        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["input"][:-2], history[-ai.MAX_HISTORY:])
        self.assertIn('"current": 95', payload["input"][-2]["content"])
        self.assertNotIn("previous_response_id", payload)

    def test_daily_limit_survives_restart_and_rolls_over(self):
        self.client.limit = 1
        self.client.answer("First", [], {})
        restarted = ai.AiClient(options(ai_daily_limit=1), self.directory.name)
        with self.assertRaisesRegex(ai.AiError, "^limit$"):
            restarted.answer("Second", [], {})
        self.post.assert_called_once()
        restarted.usage_file.write_text(json.dumps({"day": "2000-01-01", "count": 100}))
        restarted.answer("New day", [], {})
        saved = json.loads(restarted.usage_file.read_text())
        self.assertEqual(saved, {"day": datetime.now(timezone.utc).date().isoformat(), "count": 1})

    def test_bad_or_unwritable_usage_file_stops_before_api(self):
        for content in ("broken", "[]", json.dumps({"day": datetime.now(timezone.utc).date().isoformat(), "count": -1})):
            self.client.usage_file.write_text(content)
            with self.assertRaisesRegex(ai.AiError, "^storage$"):
                self.client.answer("Hello", [], {})
        self.client.usage_file.unlink()
        with patch.object(ai.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(ai.AiError, "^storage$"):
                self.client.answer("Hello", [], {})
        self.post.assert_not_called()

    def test_http_errors_are_safe_and_requests_are_not_retried(self):
        for status, code, reason in [(401, None, "auth"), (429, "insufficient_quota", "quota"),
                                     (429, None, "rate"), (404, "model_not_found", "model"),
                                     (500, None, "service"), (302, None, "service")]:
            self.http.status_code = status
            self.http.json.return_value = {"error": {"code": code, "message": "private server detail"}}
            with self.subTest(status=status, code=code), self.assertRaisesRegex(ai.AiError, f"^{reason}$"):
                self.client.answer("Hello", [], {})
        self.assertEqual(self.post.call_count, 6)
        self.assertEqual(json.loads(self.client.usage_file.read_text())["count"], 6)

    def test_transport_timeout_and_invalid_json_are_safe(self):
        for error, reason in [(ai.requests.Timeout("secret"), "timeout"),
                              (ai.requests.ConnectionError("secret"), "network"),
                              (ValueError("secret"), "service")]:
            self.post.side_effect = error
            with self.assertRaisesRegex(ai.AiError, f"^{reason}$"):
                self.client.answer("Hello", [], {})

    def test_mcp_failure_or_empty_tools_is_explained(self):
        for item in [{"type": "mcp_call", "error": "private token"},
                     {"type": "mcp_list_tools", "tools": []}]:
            self.http.json.return_value = response(output=[item])
            answer = self.client.answer("Read printer", [], {})
            self.assertEqual(answer, self.client.text("mcp"))
            self.assertNotIn("private token", answer)

    def test_approval_requests_are_never_approved(self):
        self.http.json.return_value = response(output=[{"type": "mcp_approval_request", "name": "pause"}])
        self.assertEqual(self.client.answer("Pause", [], {}), self.client.text("approval"))
        self.post.assert_called_once()

    def test_output_redacts_credentials_and_handles_refusal_and_truncation(self):
        self.http.json.return_value = response("test-openai-key test-octo-key", status="incomplete")
        answer = self.client.answer("Hi", [], {})
        self.assertNotIn("test-openai-key", answer)
        self.assertNotIn("test-octo-key", answer)
        self.assertIn(self.client.text("incomplete"), answer)
        self.http.json.return_value = response(output=[{"type": "message", "content": [
            {"type": "refusal", "refusal": "I cannot do that."}]}])
        self.assertEqual(self.client.answer("Hi", [], {}), "I cannot do that.")


class ContextTest(unittest.TestCase):
    def spool(self, sid=1, **extra):
        return {"id": sid, "remaining_weight": 700, "location": "private-location", "comment": "private-note",
                "filament": {"name": "Ice Blue", "material": "PLA", "color_hex": "#123456",
                             "vendor": {"name": "YOOPAI"}, "price": "private-price"}, **extra}

    def test_context_excludes_private_data_and_expresses_unknown_and_unavailable_values(self):
        state = {"filename": "my-print", "gcode_state": "PAUSE", "tray_now": 1, "print_weight": 0,
                 "_raw_print": {"serial": "private-serial"}, "password": "private-password"}
        context = ai.bot_context(state, 4, [self.spool()], {"1": 1, "private": "private-mapping"}, "Filament ran out")
        encoded = json.dumps(context)
        self.assertNotIn("private-", encoded)
        self.assertEqual(context["printer"]["active_slot"], 2)
        self.assertEqual(context["printer"]["pause_reason"], "Filament ran out")
        self.assertIsNone(context["printer"]["estimated_filament_grams"])
        self.assertEqual(context["bot_slot_to_spool"], {"2": 1})
        self.assertEqual(context["inventory"]["spools"][0]["remainingGrams"], 700)
        no_status = ai.bot_context(state, None, None, {})
        self.assertNotIn("state", no_status["printer"])
        self.assertFalse(no_status["inventory"]["available"])
        self.assertTrue(ai.bot_context({}, 200, [], {})["inventory"]["available"])

    def test_large_inventory_is_bounded_with_full_totals_and_unknown_weights(self):
        spools = [self.spool(i) for i in range(1, 151)] + [self.spool(151, remaining_weight=None)]
        inventory = ai.bot_context({}, 0, spools, {})["inventory"]
        self.assertTrue(inventory["truncated"])
        self.assertLessEqual(len(inventory["spools"]), 100)
        self.assertEqual(inventory["total_spools"], 151)
        self.assertEqual(inventory["known_remaining_grams"], 105000)
        self.assertEqual(inventory["unknown_weights"], 1)
        self.assertLess(len(json.dumps(inventory["spools"])), 24500)


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bot = Mock()
        self.context = Mock(return_value={"current": 1})
        self.form = Mock(return_value=False)
        self.chat = ai.AiChat(self.bot, options(), self.directory.name, self.context, self.form)
        self.chat.client.answer = Mock(return_value="Answer")
        # Control completion without sleeps; the production thread target is unchanged.
        self.thread = self.enterContext(patch.object(ai.threading, "Thread"))

    def complete(self):
        call = self.thread.call_args.kwargs
        call["target"](*call["args"])

    def test_explicit_private_chat_only_and_form_priority(self):
        for msg in (message(chat=999), message(kind="group")):
            self.chat.start(msg)
            self.assertFalse(self.chat.accepts_text(msg))
        self.assertFalse(self.chat.sessions)
        self.bot.reply_to.assert_not_called()
        self.chat.start(message())
        self.assertTrue(self.chat.accepts_text(message("Hello")))
        self.assertFalse(self.chat.accepts_text(message("/status")))
        self.form.return_value = True
        self.assertFalse(self.chat.accepts_text(message("123")))
        self.chat.start(message("/ai question"))
        self.assertEqual(self.bot.reply_to.call_args.args[1], self.chat.client.text("form"))
        self.chat.client.answer.assert_not_called()

    def test_followups_have_recent_history_and_new_context(self):
        self.chat.start(message("/ai First question"))
        self.complete()
        self.context.return_value = {"current": 2}
        self.chat.text(message("Second question"))
        self.complete()
        args = self.chat.client.answer.call_args.args
        self.assertEqual(args[0], "Second question")
        self.assertEqual(args[1], [{"role": "user", "content": "First question"}, {"role": "assistant", "content": "Answer"}])
        self.assertEqual(args[2], {"current": 2})
        self.assertIsNone(self.bot.reply_to.call_args.kwargs["parse_mode"])

    def test_busy_rejects_duplicate_and_stop_discards_pending_reply(self):
        self.chat.start(message("/ai Hello"))
        self.chat.text(message("Again"))
        self.assertEqual(self.thread.call_count, 1)
        self.assertEqual(self.bot.reply_to.call_args.args[1], self.chat.client.text("busy"))
        self.chat.end(message("/aistop"))
        before = self.bot.reply_to.call_count
        self.complete()
        self.assertEqual(self.bot.reply_to.call_count, before)
        self.assertFalse(self.chat.sessions)
        self.assertFalse(self.chat.busy)
        self.assertFalse(self.chat.accepts_text(message("Hello")))

    def test_reset_and_new_session_do_not_receive_previous_answer(self):
        self.chat.start(message("/ai Old question"))
        self.chat.end(message("/aireset"))
        self.chat.start(message())
        before = self.bot.reply_to.call_count
        self.complete()
        self.assertEqual(self.bot.reply_to.call_count, before)
        self.assertEqual(self.chat.sessions[self.chat.key(message())]["history"], [])

    def test_expired_session_long_question_and_provider_error(self):
        self.chat.start(message())
        self.chat.text(message("x" * 4001))
        self.thread.assert_not_called()
        session = self.chat.sessions[self.chat.key(message())]
        session["updated"] -= ai.SESSION_TTL + 1
        self.chat.text(message("Hello"))
        self.assertEqual(self.bot.reply_to.call_args.args[1], self.chat.client.text("expired"))
        self.thread.assert_not_called()
        self.chat.start(message("/ai Hello"))
        self.chat.client.answer.side_effect = ai.AiError("auth")
        self.complete()
        self.assertEqual(self.bot.reply_to.call_args.args[1], self.chat.client.text("auth"))
        self.assertFalse(self.chat.busy)


class BackgroundTest(unittest.TestCase):
    def test_slow_context_or_api_does_not_block_stop_or_deliver_a_cancelled_reply(self):
        real_thread = threading.Thread
        for slow_context in (True, False):
            with self.subTest(slow_context=slow_context), tempfile.TemporaryDirectory() as directory:
                entered, release = threading.Event(), threading.Event()
                def wait_for_release():
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError("Stop was blocked by the AI worker")
                    return {} if slow_context else "Late answer"
                context = Mock(side_effect=wait_for_release) if slow_context else Mock(return_value={})
                bot = Mock()
                chat = ai.AiChat(bot, options(), directory, context, lambda msg: False)
                chat.client.answer = Mock(return_value="Answer") if slow_context else Mock(side_effect=lambda *args: wait_for_release())
                threads = []
                def start_thread(**kwargs):
                    worker = real_thread(**kwargs)
                    threads.append(worker)
                    return worker
                with patch.object(ai.threading, "Thread", side_effect=start_thread):
                    try:
                        chat.start(message("/ai Question"))
                        self.assertTrue(entered.wait(1))
                        chat.end(message("/aistop"))
                        self.assertEqual(bot.reply_to.call_args.args[1], chat.client.text("stop"))
                    finally:
                        release.set()
                        for worker in threads:
                            worker.join(2)
                            self.assertFalse(worker.is_alive())
                bot.reply_to.assert_called_once()
                self.assertFalse(chat.busy)
                if slow_context:
                    chat.client.answer.assert_not_called()


class BotIntegrationTest(unittest.TestCase):
    """Use TeleBot's real dispatcher and the production registration order offline."""
    def setUp(self):
        import telebot
        self.telebot = telebot
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bot = telebot.TeleBot("123:offline-test", threaded=False)
        for name in ("set_my_commands", "reply_to", "send_message", "send_chat_action", "edit_message_reply_markup"):
            setattr(self.bot, name, Mock(return_value=SimpleNamespace(message_id=1)))
        self.enterContext(patch.object(telebot, "TeleBot", return_value=self.bot))
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected live HTTP")))
        self.enterContext(patch("logging.basicConfig"))
        self.enterContext(patch.dict("os.environ", {"SUPERVISOR_TOKEN": ""}))
        config = options(printer_ip="printer.invalid", printer_serial="test-serial",
                         printer_password="test-access-code", telegram_token="123:offline-test",
                         spoolman_url="http://spoolman.invalid")
        self.env = {"__name__": "offline_ai_monitor", "_test_config": lambda: (config, self.directory.name)}
        path = APP / "bambu_monitor.py"
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "load_config":
                node.value.func.id = "_test_config"  # Avoid reading any real options/secrets.
        exec(compile(tree, str(path), "exec"), self.env)
        self.chat = self.env["_ai_chat"]
        self.chat.client.answer = Mock(return_value="AI answer")
        self.env["_spoolman_get"] = Mock(return_value=[{"id": 42, "remaining_weight": 700,
                                                       "filament": {"name": "Ice Blue", "material": "PLA"}}])
        self.thread = self.enterContext(patch.object(ai.threading, "Thread"))

    def send(self, text):
        msg = self.telebot.types.Message.de_json({"message_id": 1, "date": 0,
            "chat": {"id": 123, "type": "private"}, "from": {"id": 123, "is_bot": False, "first_name": "Tester"},
            "text": text})
        self.bot.process_new_messages([msg])
        return msg

    def test_help_and_pause_keep_existing_behavior_inside_ai_chat(self):
        self.send("/ai")
        self.send("/help")
        self.assertIn("/aireset", self.bot.reply_to.call_args.args[1])
        self.env["_state"]["printing"] = True
        self.env["send_gcode_command"] = Mock()
        self.send("/pause")
        self.assertIn("pause", self.bot.reply_to.call_args.args[1].lower())
        self.thread.assert_not_called()
        self.send("How much PLA is left?")
        self.thread.assert_called_once()

    def test_mapping_numeric_reply_goes_to_mapping_and_next_text_to_ai(self):
        self.send("/ai")
        self.send("/map 2")
        self.send("42")
        self.assertEqual(self.env["load_mapping"](), {"1": 42})
        self.thread.assert_not_called()
        self.send("What color is that spool?")
        self.thread.assert_called_once()

    def test_spool_wizard_and_weight_step_take_priority(self):
        self.send("/ai")
        self.send("/addspool")
        self.send("PLA")
        self.thread.assert_not_called()
        self.send("/ai New question")
        self.assertEqual(self.bot.reply_to.call_args.args[1], self.chat.client.text("form"))
        self.send("/cancel")
        prompt = self.send("/update")
        weight_handler = Mock()
        self.bot.register_next_step_handler(prompt, weight_handler)
        self.send("700")
        weight_handler.assert_called_once()
        self.thread.assert_not_called()

    def test_context_reads_do_not_block_mqtt_lock_or_expose_restored_status(self):
        self.env["_state"].update(gcode_state="RUNNING", filename="restored print", print_weight=23)
        def inventory(path):
            self.assertTrue(self.env["_lock"].acquire(blocking=False))
            self.env["_lock"].release()
            return []
        self.env["_spoolman_get"].side_effect = inventory
        context = self.env["_ai_context"]()
        self.assertIsNone(context["printer"]["age_seconds"])
        self.assertNotIn("filename", context["printer"])
        # Non-status traffic must not make an old saved status look current.
        self.env["on_message"](None, None, SimpleNamespace(payload=b'{"print":{"nozzle_temper":210}}'))
        self.assertIsNone(self.env["_ai_context"]()["printer"]["age_seconds"])
        self.env["_state"].update(printing=True)
        self.env["on_message"](None, None, SimpleNamespace(payload=b'{"print":{"gcode_state":"RUNNING"}}'))
        self.assertLess(self.env["_ai_context"]()["printer"]["age_seconds"], 1)


if __name__ == "__main__":
    unittest.main()
