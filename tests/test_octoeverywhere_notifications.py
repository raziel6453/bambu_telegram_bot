"""Selected alerts, durable delivery, cross-source dedup and private setup."""
import ast
import html
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


APP = Path(__file__).resolve().parents[1] / "bambu_telegram_bot"
spec = importlib.util.spec_from_file_location("octoeverywhere_notifications", APP / "octoeverywhere_notifications.py")
octo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(octo)


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "notifications.json"
        self.sender = Mock()
        self.now = 10000
        self.relay = octo.NotificationRelay(self.path, self.sender, clock=lambda: self.now)

    def event(self, kind=7, **extra):
        return {"SecretKey": self.relay.state["secret"], "PrinterId": "printer-one",
                "PrintId": "print-one", "EventType": kind, "FileName": "My print", **extra}

    def test_only_selected_alerts_and_setup_test_are_delivered(self):
        for kind in range(1, 19):
            self.assertEqual(self.relay.receive(self.event(kind)), 200)
        self.now += 3
        while self.relay.flush_once():
            pass
        self.assertEqual({call.args[0]["EventType"] for call in self.sender.call_args_list}, {7, 12, 15, 16})
        self.assertEqual(self.sender.call_count, 4)
        for call in self.sender.call_args_list:
            self.assertNotIn("SecretKey", call.args[0])

    def test_requires_secret_and_valid_event_data(self):
        for event in ([], None, {}, self.event(SecretKey="wrong"), self.event(SecretKey="\ud800"),
                      self.event(EventType=True), self.event(EventType="7"), self.event(PrinterId=""),
                      self.event(PrinterId=123), self.event(PrintId=""), self.event(PrintId=[])):
            with self.subTest(event=event):
                self.assertIn(self.relay.receive(event), (400, 403))
        self.assertFalse(self.relay.state["pending"])
        self.sender.assert_not_called()

    def test_pending_and_delivered_duplicates_are_suppressed(self):
        payload = self.event()
        self.relay.receive(payload)
        self.relay.receive({**payload, "SnapshotUrl": "https://cdn.example/new-snapshot.jpg"})
        self.assertEqual(len(self.relay.state["pending"]), 1)
        self.assertTrue(self.relay.flush_once())
        self.relay.receive(payload)
        self.assertFalse(self.relay.flush_once())
        self.sender.assert_called_once()

    def test_recovery_preserves_secret_pending_and_delivered_dedup(self):
        self.relay.receive(self.event(15))
        restarted = octo.NotificationRelay(self.path, self.sender, clock=lambda: self.now)
        self.assertEqual(restarted.state["secret"], self.relay.state["secret"])
        restarted.flush_once()
        restarted = octo.NotificationRelay(self.path, self.sender, clock=lambda: self.now)
        self.now += 3600
        restarted.receive(self.event(15))
        self.assertFalse(restarted.flush_once())
        self.sender.assert_called_once()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_new_print_and_new_error_are_not_suppressed(self):
        for extra in ({"PlatformErrorCode": "12018011"},
                      {"PlatformErrorCode": "12008010"},
                      {"PlatformErrorCode": "12018011", "PrintId": "print-two"}):
            self.relay.receive(self.event(12, **extra))
        self.now += 3
        while self.relay.flush_once():
            pass
        self.assertEqual(self.sender.call_count, 3)

    def test_failure_warning_can_repeat_after_cooldown(self):
        self.relay.receive(self.event())
        self.relay.flush_once()
        self.now += octo.REPEAT_WINDOW + 1
        self.relay.receive(self.event())
        self.relay.flush_once()
        self.assertEqual(self.sender.call_count, 2)

    def test_same_code_and_filename_is_deduped_in_both_source_orders(self):
        for webhook_first in (False, True):
            with self.subTest(webhook_first=webhook_first):
                path = Path(self.directory.name) / f"order-{webhook_first}.json"
                relay = octo.NotificationRelay(path, self.sender, clock=lambda: self.now)
                event = self.event(12, SecretKey=relay.state["secret"], PlatformErrorCode="12018011")
                self.sender.reset_mock()
                if webhook_first:
                    relay.receive(event)
                    self.now += 3
                    relay.flush_once()
                    relay.enqueue_pause("Paused: filament ran out", 302088209, "My print")
                else:
                    relay.enqueue_pause("Paused: filament ran out", 302088209, "My print")
                    relay.flush_once()
                    relay.receive(event)
                    self.now += 3
                relay.flush_once()
                self.sender.assert_called_once()

    def test_queued_webhook_waits_briefly_for_mqtt_reason(self):
        self.relay.receive(self.event(12, PlatformErrorCode="12018011"))
        self.assertFalse(self.relay.flush_once())
        self.relay.enqueue_pause("pause details", 0x12018011, "My print")
        self.assertTrue(self.relay.flush_once())
        self.now += 3
        self.relay.flush_once()
        self.sender.assert_called_once_with({"text": "pause details"})

    def test_unknown_or_different_reason_filename_or_later_issue_is_preserved(self):
        for code, filename, delay in [(0, "My print", 0), (0x12008010, "My print", 0),
                                      (0x12018011, "Another print", 0), (0x12018011, "My print", 121)]:
            with self.subTest(code=code, filename=filename, delay=delay):
                self.relay.enqueue_pause("pause details", code, filename)
                self.relay.flush_once()
                self.now += delay
                self.relay.receive(self.event(12, PrintId=str(self.now), PlatformErrorCode="12018011"))
                self.now += 3
                self.relay.flush_once()
                self.assertEqual(self.sender.call_count, 2)
                self.sender.reset_mock()
                self.now += 1000

    def test_telegram_failure_is_retried_without_acknowledging_delivery(self):
        self.sender.side_effect = RuntimeError("private credentials")
        self.relay.receive(self.event())
        with self.assertLogs("bambu.octo", level="WARNING") as logs:
            self.relay.flush_once()
        self.assertNotIn("private credentials", " ".join(logs.output))
        self.assertEqual(len(self.relay.state["pending"]), 1)
        self.assertFalse(self.relay.state["seen"])
        self.assertFalse(self.relay.flush_once())
        self.sender.side_effect = None
        self.now += 2
        self.assertTrue(self.relay.flush_once())
        self.assertFalse(self.relay.state["pending"])

    def test_old_pending_alerts_expire_and_queue_is_bounded(self):
        with patch.object(octo, "MAX_QUEUE", 1):
            self.assertEqual(self.relay.receive(self.event()), 200)
            self.assertEqual(self.relay.receive(self.event(PrintId="second")), 503)
        self.now += octo.QUEUE_TTL + 1
        self.assertFalse(self.relay.flush_once())
        self.sender.assert_not_called()

    def test_failed_persistence_is_not_acknowledged_or_left_queued(self):
        with patch.object(self.relay, "_save", side_effect=OSError("disk full")):
            self.assertEqual(self.relay.receive(self.event()), 503)
        self.assertFalse(self.relay.state["pending"])

    def test_invalid_saved_state_does_not_regenerate_secret(self):
        self.path.write_text("broken json")
        with self.assertRaises(ValueError):
            octo.NotificationRelay(self.path, self.sender)
        self.assertEqual(self.path.read_text(), "broken json")

    def test_numeric_and_display_error_codes_match(self):
        for value in (0x12018011, "302088209", "12018011", "0x12018011", "1201_8011", "1201-8011"):
            self.assertEqual(octo.error_code(value), "12018011")
        for value in (None, True, 0, -1, "-1", "bad", {}, 2**40):
            self.assertIsNone(octo.error_code(value))

    def test_links_are_validated_and_never_fetched_locally(self):
        for value in ("http://cdn.example/a", "https://user:pass@cdn.example/a", "https://127.0.0.1/a",
                      "https://192.168.1.2/a", "https://camera.local/a", "https://[broken", "file:///etc/passwd"):
            self.assertIsNone(octo.safe_url(value))
        self.assertIsNone(octo.safe_url("https://octoeverywhere.com.evil.example/", quick_view=True))
        self.assertEqual(octo.safe_url("https://octoeverywhere.com/view/printer", quick_view=True),
                         "https://octoeverywhere.com/view/printer")
        self.assertEqual(octo.safe_url("https://images.example/snapshot.jpg"), "https://images.example/snapshot.jpg")

    def test_caption_stays_within_telegram_limit_in_both_languages(self):
        for language in ("en", "he"):
            text = octo.notification_text(self.event(12, FileName="🧵" * 500,
                Error="🚨" * 1000, PlatformErrorCode="x" * 80), language)
            self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 1024)

    def test_disabled_feature_does_not_create_listener_or_state(self):
        with patch.object(octo, "NotificationRelay") as factory:
            self.assertIsNone(octo.start_notifications(False, self.directory.name, self.sender))
            factory.assert_not_called()

    def test_gadget_without_alert_is_unknown_not_healthy(self):
        self.assertIn("unknown", self.relay.gadget_status("en"))
        for event in (12, 15, 16):
            self.relay.receive(self.event(event))
        self.assertIn("unknown", self.relay.gadget_status("en"))
        self.assertIn("אינו ידוע", self.relay.gadget_status("he"))

    def test_gadget_last_alert_survives_delivery_and_restart_with_age(self):
        self.relay.receive(self.event(7, FileName="My print"))
        self.relay.flush_once()
        self.now += 120
        restarted = octo.NotificationRelay(self.path, self.sender, clock=lambda: self.now)
        text = restarted.gadget_status("en")
        self.assertIn("2 min ago", text)
        self.assertIn("My print", text)
        self.assertIn("Historical alert", text)
        self.assertNotIn(self.relay.state["secret"], text)
        self.assertNotIn("PrinterId", text)
        self.assertIn("התראה אחרונה", restarted.gadget_status("he"))

    def test_gadget_smart_pause_is_status_only_and_not_a_duplicate_notification(self):
        self.relay.receive(self.event(8))
        self.assertIn("paused", self.relay.gadget_status("en"))
        self.assertFalse(self.relay.flush_once())
        self.sender.assert_not_called()
        self.relay.receive(self.event(16))
        self.assertIn("paused", self.relay.gadget_status("en"))

    def test_repeated_warning_refreshes_last_receipt_without_telegram_duplicates(self):
        self.relay.receive(self.event())
        self.relay.flush_once()
        self.now += 60
        self.relay.receive(self.event())
        self.assertFalse(self.relay.flush_once())
        self.sender.assert_called_once()
        self.assertIn("less than a minute", self.relay.gadget_status("en"))

    def test_invalid_or_unpersisted_event_does_not_replace_last_gadget_alert(self):
        self.relay.receive(self.event(7))
        previous = dict(self.relay.state["last_gadget"])
        self.assertEqual(self.relay.receive(self.event(8, SecretKey="wrong")), 403)
        self.assertEqual(self.relay.receive(self.event(8, PrintId="")), 400)
        with patch.object(self.relay, "_save", side_effect=OSError("disk full")):
            self.assertEqual(self.relay.receive(self.event(8)), 503)
        self.assertEqual(self.relay.state["last_gadget"], previous)

    def test_old_alert_stays_historical_and_file_name_is_bounded(self):
        self.relay.receive(self.event(FileName="x\n" * 500))
        self.now += 3 * 86400
        text = self.relay.gadget_status("en")
        self.assertIn("3 days ago", text)
        self.assertEqual(len(text.splitlines()), 3)
        self.assertLess(len(text), 250)


class HttpReceiverTest(unittest.TestCase):
    def test_local_http_auth_filter_and_async_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            delivered = threading.Event()
            sender = Mock(side_effect=lambda event: delivered.set())
            relay = octo.NotificationRelay(Path(directory) / "state.json", sender)
            relay.start(host="127.0.0.1", port=0)
            try:
                def post(payload, path=octo.WEBHOOK_PATH):
                    connection = http.client.HTTPConnection("127.0.0.1", relay.server.server_port, timeout=3)
                    connection.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    response.read()
                    status = response.status
                    connection.close()
                    return status
                payload = {"SecretKey": relay.state["secret"], "PrinterId": "one", "PrintId": "print", "EventType": 16}
                self.assertEqual(post(payload, "/wrong"), 404)
                self.assertEqual(post({**payload, "SecretKey": "wrong"}), 403)
                self.assertEqual(post({**payload, "EventType": 1}), 200)
                sender.assert_not_called()
                self.assertEqual(post(payload), 200)
                self.assertTrue(delivered.wait(2))
                self.assertEqual(post(payload), 200)
                self.assertEqual(sender.call_count, 1)
            finally:
                relay.close()

    def test_webhook_ack_does_not_wait_for_telegram(self):
        with tempfile.TemporaryDirectory() as directory:
            entered = threading.Event()
            release = threading.Event()
            def slow_sender(event):
                entered.set()
                release.wait(5)
            relay = octo.NotificationRelay(Path(directory) / "state.json", slow_sender)
            relay.start(host="127.0.0.1", port=0)
            try:
                payload = {"SecretKey": relay.state["secret"], "PrinterId": "one", "PrintId": "print", "EventType": 7}
                for _ in range(2):
                    connection = http.client.HTTPConnection("127.0.0.1", relay.server.server_port, timeout=1)
                    connection.request("POST", octo.WEBHOOK_PATH, json.dumps(payload))
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                    connection.close()
                self.assertTrue(entered.wait(1))
                self.assertFalse(release.is_set())
                saved = json.loads((Path(directory) / "state.json").read_text())
                self.assertEqual(len(saved["pending"]), 1)
            finally:
                release.set()
                relay.close()


class BotHandlersTest(unittest.TestCase):
    def setUp(self):
        self.env = {"bot": Mock(), "log": Mock(), "html": html, "LANGUAGE": "he",
                    "HA_AVAILABLE": False, "requests": Mock(), "_ha_headers": Mock(return_value={}),
                    "TELEGRAM_CHAT_ID": "123", "options": {"octoeverywhere_notifications": True},
                    "_octo_notifications": Mock(), "notification_text": octo.notification_text,
                    "_pause_reason": SimpleNamespace(code=0x12018011), "_state": {"filename": "My print"},
                    "tg_send": Mock(), "telebot": Mock()}
        self.env["time"] = SimpleNamespace(monotonic=lambda: 1000)
        self.env["_octo_notifications"].setup_values.return_value = ("http://addon:8765/octoeverywhere", "test-private-key")
        names = {"t", "chat_ok", "cmd_octo", "_notify_pause", "_send_octo_notification", "cmd_status", "_gadget_status_text"}
        self.env.update({"_lock": threading.Lock(), "_status_event": Mock(), "request_pushall": Mock(),
                         "SPOOLMAN_URL": "", "smart_remaining": lambda: 10, "fmt_mins": str,
                         "finish_time": str, "ha_light_state": lambda: ("off", None),
                         "_ha_weight_entity": lambda: None, "ha_snapshot": lambda: (b"image", None),
                         "split_messages": lambda text: [text]})
        self.env["_state"].update(printing=True, gcode_state="RUNNING", mc_percent=25, tray_now=0,
                                  print_weight=80, layer_num=10, total_layer_num=100)
        self.env["_octo_notifications"].gadget_status.return_value = "🤖 Gadget: historical warning"
        nodes = []
        source = APP / "bambu_monitor.py"
        for node in ast.parse(source.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                node.decorator_list = []
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STRINGS" for t in node.targets):
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.env)

    def test_setup_secret_only_reaches_authorized_private_chat(self):
        for chat_id, chat_type in ((999, "private"), (123, "group"), (123, "supergroup")):
            self.env["cmd_octo"](SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=chat_type)))
        self.env["_octo_notifications"].setup_values.assert_not_called()
        self.env["bot"].send_message.assert_not_called()
        self.env["cmd_octo"](SimpleNamespace(chat=SimpleNamespace(id=123, type="private")))
        sent = self.env["bot"].send_message.call_args_list
        self.assertIn("test-private-key", sent[-1].args[1])

    def test_photo_failure_still_delivers_alert_text(self):
        self.env["bot"].send_photo.side_effect = RuntimeError("private image URL")
        self.env["_send_octo_notification"]({"EventType": 7, "SnapshotUrl": "https://images.example/a"})
        self.env["bot"].send_message.assert_called_once()
        self.assertIn("חשד", self.env["bot"].send_message.call_args.args[1])
        self.assertNotIn("private image URL", str(self.env["log"].method_calls))

    def test_cooled_bed_alert_includes_recent_temperature_in_photo_and_text(self):
        self.env["_state"]["_bed_temperature"] = (34.56, 990)
        event = {"EventType": 15, "SnapshotUrl": "https://images.example/a"}
        self.env["_send_octo_notification"](event)
        caption = self.env["bot"].send_photo.call_args.kwargs["caption"]
        self.assertIn("המיטה התקררה", caption)
        self.assertIn("34.6°C", caption)
        self.env["bot"].send_photo.side_effect = RuntimeError("offline")
        self.env["_send_octo_notification"](event)
        self.assertEqual(self.env["bot"].send_message.call_args.args[1], caption)

    def test_cooled_bed_missing_stale_or_invalid_reading_is_not_a_temperature(self):
        for reading in (None, (35, 819), (35, 1001), (float("nan"), 990), (True, 990)):
            with self.subTest(reading=reading):
                self.env["_state"]["_bed_temperature"] = reading
                self.env["_send_octo_notification"]({"EventType": 15})
                text = self.env["bot"].send_message.call_args.args[1]
                self.assertIn("אינה זמינה", text)
                self.assertNotIn("°C", text)

    def test_cooled_bed_english_and_zero_temperature(self):
        self.env["LANGUAGE"] = "en"
        self.env["_state"]["_bed_temperature"] = (0, 1000)
        self.env["_send_octo_notification"]({"EventType": 15})
        text = self.env["bot"].send_message.call_args.args[1]
        self.assertIn("The bed has cooled down", text)
        self.assertIn("0°C", text)

    def test_setup_uses_actual_addon_identity_from_supervisor(self):
        self.env["HA_AVAILABLE"] = True
        self.env["requests"].get.return_value.status_code = 200
        self.env["requests"].get.return_value.json.return_value = {"data": {"slug": "repo_bambu_telegram_monitor"}}
        self.env["cmd_octo"](SimpleNamespace(chat=SimpleNamespace(id=123, type="private")))
        self.env["_octo_notifications"].setup_values.assert_called_once_with("repo_bambu_telegram_monitor")

    def test_delivery_failure_propagates_to_retry_queue(self):
        self.env["bot"].send_message.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            self.env["_send_octo_notification"]({"EventType": 15})

    def test_mqtt_pause_uses_queue_and_falls_back_if_disabled_or_full(self):
        self.env["_notify_pause"]("pause reason")
        self.env["_octo_notifications"].enqueue_pause.assert_called_once_with("pause reason", 0x12018011, "My print")
        self.env["tg_send"].assert_not_called()
        self.env["_octo_notifications"].enqueue_pause.return_value = False
        self.env["_notify_pause"]("pause reason")
        self.env["tg_send"].assert_called_once_with("pause reason")
        self.env["_octo_notifications"] = None
        self.env["_notify_pause"]("another reason")
        self.assertEqual(self.env["tg_send"].call_count, 2)


    def test_status_includes_gadget_when_printing_paused_or_idle(self):
        for state in ("RUNNING", "PAUSE", "IDLE"):
            self.env["_state"]["gcode_state"] = state
            self.env["cmd_status"](SimpleNamespace(chat=SimpleNamespace(id=123)))
            self.assertIn("Gadget: historical warning", self.env["bot"].reply_to.call_args.args[1])

    def test_gadget_status_distinguishes_disabled_and_failed_receiver(self):
        self.env["_octo_notifications"] = None
        self.assertIn("אינו זמין", self.env["_gadget_status_text"]())
        self.env["options"]["octoeverywhere_notifications"] = False
        self.assertIn("לא הופעל", self.env["_gadget_status_text"]())
        self.env["LANGUAGE"] = "en"
        self.assertIn("not enabled", self.env["_gadget_status_text"]())

    def test_short_photo_caption_contains_gadget_and_long_caption_becomes_text(self):
        self.env["HA_AVAILABLE"] = True
        message = SimpleNamespace(chat=SimpleNamespace(id=123))
        self.env["cmd_status"](message)
        self.assertIn("Gadget: historical warning", self.env["bot"].send_photo.call_args.kwargs["caption"])
        self.env["_state"]["filename"] = "🧵" * 600
        self.env["cmd_status"](message)
        self.assertNotIn("caption", self.env["bot"].send_photo.call_args.kwargs)
        self.assertIn("Gadget: historical warning", self.env["bot"].reply_to.call_args.args[1])

    def test_unauthorized_status_does_not_reveal_gadget_history(self):
        self.env["cmd_status"](SimpleNamespace(chat=SimpleNamespace(id=999)))
        self.env["_octo_notifications"].gadget_status.assert_not_called()
        self.env["bot"].reply_to.assert_not_called()


if __name__ == "__main__":
    unittest.main()
