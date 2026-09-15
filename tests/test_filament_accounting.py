"""Exercise production accounting handlers offline, without bot startup."""
import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call


class FilamentAccountingTest(unittest.TestCase):
    def setUp(self):
        self.mapping = {"0": 41, "1": 42, "2": 43, "3": 44}
        self.env = {
            "_diagnostics": Mock(),
            "datetime": datetime, "timedelta": timedelta, "JERUSALEM": timezone.utc,
            "json": json, "threading": Mock(), "time": Mock(), "log": Mock(),
            "_lock": threading.Lock(), "_status_event": threading.Event(),
            "HA_AVAILABLE": True, "SPOOLMAN_URL": "http://spoolman.invalid",
            "load_mapping": lambda: self.mapping,
            "_spoolman_put": Mock(return_value=True),
            "_ha_weight_entity": Mock(return_value="sensor.print_weight"),
            "_ha_sensor": Mock(return_value="25.5"),
            "_capture_spool_start": Mock(), "_check_low_stock": Mock(),
            "_persist_state": Mock(), "_add_history": Mock(), "tg_photo": Mock(),
            "t": lambda key, **kwargs: f"{key}: {kwargs}",
            "smart_remaining": lambda: 10, "fmt_mins": str,
            "finish_time": str, "fmt_duration": str,
        }
        path = Path(__file__).resolve().parents[1] / "bambu_telegram_bot" / "bambu_monitor.py"
        selected = []
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in {
                "_on_print_start", "_on_print_finish", "_spool_deduct", "on_message",
            }:
                selected.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_state" for target in node.targets
            ):
                selected.append(node)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), self.env)
        self.state = self.env["_state"]
        self.state.update(printing=True, gcode_state="RUNNING", tray_now=1)

    def finish(self, weight=0):
        self.env["_on_print_finish"]("part.3mf", weight)
        return self.env["_add_history"].call_args.args[0]

    def test_home_assistant_weight_reaches_deduction_message_and_history(self):
        history = self.finish()
        self.env["_spoolman_put"].assert_called_once_with("/api/v1/spool/42/use", {"use_weight": 25.5})
        self.assertEqual(history["grams"], 25.5)
        self.assertIn("25.5g", self.env["tg_photo"].call_args.args[0])
        self.assertEqual(self.state["print_weight"], 0)

    def test_start_weight_is_preserved_when_sensor_resets_before_finish(self):
        self.env["_on_print_start"](0, 10, "part.3mf", 0)
        self.assertEqual(self.state["print_weight"], 25.5)
        self.env["_ha_sensor"].return_value = "0"
        self.assertEqual(self.finish()["grams"], 25.5)
        self.env["_spoolman_put"].assert_called_once_with("/api/v1/spool/42/use", {"use_weight": 25.5})

    def test_reported_finish_weight_takes_priority_over_cached_weight(self):
        self.state["print_weight"] = 10
        self.assertEqual(self.finish(30)["grams"], 30)
        self.env["_ha_sensor"].assert_not_called()

    def test_single_spool_length_is_converted_to_mm_without_weight_deduction(self):
        self.state["filament_used"] = "1.25"
        history = self.finish(50)
        self.env["_spoolman_put"].assert_called_once_with("/api/v1/spool/42/use", {"use_length": 1250.0})
        self.assertEqual(history["spools_used"], [{"slot": 1, "spool_id": 42, "amount": "1250.0mm"}])
        self.assertIsNone(self.state["filament_used"])

    def test_multiple_spools_deduct_only_their_own_lengths(self):
        self.state["filament_used"] = [1.5, 0, "2.25", None]
        history = self.finish(50)
        self.assertEqual(self.env["_spoolman_put"].call_args_list, [
            call("/api/v1/spool/41/use", {"use_length": 1500.0}),
            call("/api/v1/spool/43/use", {"use_length": 2250.0}),
        ])
        self.assertEqual([entry["slot"] for entry in history["spools_used"]], [0, 2])

    def test_unmapped_lengths_do_not_charge_total_weight_to_active_spool(self):
        del self.mapping["0"]
        self.state["filament_used"] = [1.5, 0, 0, 0]
        self.finish(50)
        self.env["_spoolman_put"].assert_not_called()

    def test_failed_length_request_does_not_retry_as_total_weight(self):
        self.state["filament_used"] = [1, 2]
        self.env["_spoolman_put"].side_effect = [True, False]
        self.finish(50)
        self.assertEqual(self.env["_spoolman_put"].call_args_list, [
            call("/api/v1/spool/41/use", {"use_length": 1000.0}),
            call("/api/v1/spool/42/use", {"use_length": 2000.0}),
        ])
        self.assertIn("spoolman_deduct_fail", self.env["tg_photo"].call_args.args[0])

    def test_invalid_or_zero_lengths_fall_back_to_weight(self):
        for lengths in (None, "bad", [0, None, "bad", -1]):
            with self.subTest(lengths=lengths):
                self.env["_spoolman_put"].reset_mock()
                self.state["filament_used"] = lengths
                self.finish(50)
                self.env["_spoolman_put"].assert_called_once_with("/api/v1/spool/42/use", {"use_weight": 50})

    def test_missing_weight_does_not_deduct(self):
        self.env["_ha_sensor"].return_value = "unavailable"
        self.assertEqual(self.finish()["grams"], 0)
        self.env["_spoolman_put"].assert_not_called()

    def test_duplicate_finish_reports_do_not_deduct_twice(self):
        payload = {"print": {"gcode_state": "FINISH", "print_weight": 50}}
        message = SimpleNamespace(payload=json.dumps(payload).encode())
        self.env["on_message"](None, None, message)
        self.env["on_message"](None, None, message)
        self.env["_spoolman_put"].assert_called_once()
        self.env["_add_history"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
