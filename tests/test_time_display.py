"""Israel time and finish estimates, including clocks changing mid-print."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo


class TimeDisplayTest(unittest.TestCase):
    def setUp(self):
        self.env = {"datetime": Mock(wraps=datetime), "timedelta": timedelta,
                    "timezone": timezone, "ZoneInfo": ZoneInfo, "HA_AVAILABLE": False,
                    "_ha_weight_entity": lambda: "sensor.a1_print_weight",
                    "_ha_sensor": Mock(return_value=None)}
        source = Path(__file__).resolve().parents[1] / "bambu_telegram_bot/bambu_monitor.py"
        nodes = [node for node in ast.parse(source.read_text()).body
                 if (isinstance(node, ast.FunctionDef) and node.name == "finish_time")
                 or (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "JERUSALEM" for t in node.targets))]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.env)

    def now(self, iso):
        self.env["datetime"].now.return_value = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    def test_display_zone_uses_winter_and_summer_offsets(self):
        zone = self.env["JERUSALEM"]
        self.assertEqual(datetime(2026, 11, 14, tzinfo=zone).utcoffset(), timedelta(hours=2))
        self.assertEqual(datetime(2026, 7, 14, tzinfo=zone).utcoffset(), timedelta(hours=3))

    def test_remaining_time_displays_correct_local_finish_in_each_season(self):
        for date, expected in [("2026-11-14T12:00:00", "15:00"), ("2026-07-14T12:00:00", "16:00")]:
            self.now(date)
            self.assertEqual(self.env["finish_time"](60), expected)
            self.env["datetime"].now.assert_called_with(timezone.utc)

    def test_print_crossing_spring_clock_change_uses_elapsed_minutes(self):
        self.now("2026-03-26T23:30:00")  # 01:30 Israel, before the skipped hour.
        self.assertEqual(self.env["finish_time"](60), "03:30")

    def test_print_crossing_autumn_clock_change_uses_elapsed_minutes(self):
        self.now("2026-10-24T22:30:00")  # 01:30 Israel, before the repeated hour.
        self.assertEqual(self.env["finish_time"](60), "01:30")

    def test_ha_timestamp_is_converted_from_its_explicit_offset(self):
        self.env["HA_AVAILABLE"] = True
        for raw, expected in [("2026-11-14T12:00:00Z", "14:00"),
                              ("2026-07-14T12:00:00Z", "15:00"),
                              ("2026-11-14T15:00:00+03:00", "14:00")]:
            self.env["_ha_sensor"].return_value = raw
            self.assertEqual(self.env["finish_time"](60), expected)
        self.env["_ha_sensor"].assert_called_with("sensor.a1_end_time")

    def test_ambiguous_or_invalid_ha_timestamp_falls_back_to_remaining_time(self):
        self.env["HA_AVAILABLE"] = True
        self.now("2026-11-14T12:00:00")
        for raw in (None, "unknown", "2026-11-14T20:00:00", "invalidTtime"):
            self.env["_ha_sensor"].return_value = raw
            self.assertEqual(self.env["finish_time"](60), "15:00")
        self.env["HA_AVAILABLE"] = False
        self.assertEqual(self.env["finish_time"](0), "–")


if __name__ == "__main__":
    unittest.main()
