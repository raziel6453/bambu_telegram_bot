"""Offline connection lifecycle tests using the production MQTT callbacks."""
import ast
from pathlib import Path
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class MqttConnectionTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.mqtt = SimpleNamespace(Client=Mock(return_value=self.client),
                                    CallbackAPIVersion=SimpleNamespace(VERSION1=1),
                                    MQTT_ERR_SUCCESS=0)
        self.env = {
            "mqtt": self.mqtt, "ssl": ssl, "time": Mock(), "threading": Mock(),
            "log": Mock(), "tg_send": Mock(), "on_message": Mock(),
            "request_pushall": Mock(), "_mqtt_client": None,
            "PRINTER_SERIAL": "private-serial", "PRINTER_IP": "printer.invalid",
            "PRINTER_PASSWORD": "access-code", "VERSION": "test",
            "BAMBU_USERNAME": "user@bambu.invalid", "BAMBU_PASSWORD_": "password",
            "requests": Mock(), "t": lambda key, **kw: f"{key}: {kw}",
            "DATA_DIR": ".", "_restore_state": Mock(), "bot": Mock(),
        }
        self.env["time"].monotonic.return_value = 0
        path = Path(__file__).resolve().parents[1] / "bambu_telegram_bot" / "bambu_monitor.py"
        nodes = []
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in {
                "on_connect", "on_disconnect", "_make_client", "_connect_mqtt",
                "_connect_local", "_connect_cloud", "main",
            }:
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_RC_CODES" for t in node.targets
            ):
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), self.env)

    def acknowledge(self, rc):
        def loop(**kwargs):
            data = self.client.user_data_set.call_args.args[0]
            self.env["on_connect"](self.client, data, {}, rc)
            return 0
        self.client.loop.side_effect = loop

    def test_local_success_waits_for_ack_and_subscribes(self):
        self.acknowledge(0)
        self.assertIs(self.env["_connect_local"](), self.client)
        self.client.loop.assert_called_once()
        self.client.subscribe.assert_called_once_with("device/private-serial/report")
        self.assertIs(self.env["_mqtt_client"], self.client)
        message = self.env["tg_send"].call_args.args[0]
        self.assertIn("Local", message)
        self.assertNotIn("private-serial", message)
        self.client.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=30)

    def test_local_rejection_allows_cloud_fallback(self):
        self.acknowledge(5)
        cloud = Mock(return_value="cloud-client")
        result = self.env["_connect_local"]() or cloud()
        self.assertEqual(result, "cloud-client")
        cloud.assert_called_once()
        self.client.disconnect.assert_called()
        self.client.subscribe.assert_not_called()
        self.assertIn("Access Code", self.env["tg_send"].call_args.args[0])

    def test_socket_failure_is_reported_and_returns_none(self):
        self.client.connect.side_effect = OSError("unreachable")
        self.assertIsNone(self.env["_connect_local"]())
        self.assertIn("unreachable", self.env["tg_send"].call_args.args[0])

    def test_handshake_timeout_cleans_up(self):
        self.env["time"].monotonic.side_effect = [0, 11]
        self.assertIsNone(self.env["_connect_local"]())
        self.client.disconnect.assert_called_once()
        self.assertIn("10 seconds", self.env["tg_send"].call_args.args[0])

    def test_cloud_uses_account_uid_and_verified_tls(self):
        self.env["requests"].post.return_value.json.return_value = {"accessToken": "token"}
        self.env["requests"].get.return_value.json.return_value = {"uid": 123}
        self.acknowledge(0)
        self.assertIs(self.env["_connect_cloud"](), self.client)
        self.client.username_pw_set.assert_called_once_with("u_123", "token")
        self.client.tls_set.assert_called_once_with()
        self.client.tls_insecure_set.assert_not_called()
        self.assertIn("Cloud", self.env["tg_send"].call_args.args[0])

    def test_cloud_rejection_does_not_blame_local_access_code(self):
        self.env["on_connect"](self.client, {"mode": "Cloud"}, {}, 5)
        message = self.env["tg_send"].call_args.args[0]
        self.assertIn("Cloud authentication", message)
        self.assertNotIn("Access Code", message)

    def test_no_cloud_credentials_skips_login(self):
        self.env["BAMBU_USERNAME"] = ""
        self.assertIsNone(self.env["_connect_cloud"]())
        self.env["requests"].post.assert_not_called()

    def test_missing_token_stops_before_mqtt(self):
        self.env["requests"].post.return_value.json.return_value = {"loginType": "verifyCode"}
        self.assertIsNone(self.env["_connect_cloud"]())
        self.mqtt.Client.assert_not_called()

    def test_reconnect_resubscribes_and_refreshes_state(self):
        self.env["on_disconnect"](self.client, {"mode": "Local"}, 1)
        self.env["on_connect"](self.client, {"mode": "Local"}, {}, 0)
        self.client.subscribe.assert_called_once()
        self.env["threading"].Timer.assert_called_once_with(2.0, self.env["request_pushall"])

    def test_main_retries_both_routes_after_connection_loop_stops(self):
        self.env["_connect_local"] = Mock(side_effect=[self.client, None])
        cloud = Mock()
        cloud.loop_forever.side_effect = KeyboardInterrupt
        self.env["_connect_cloud"] = Mock(return_value=cloud)
        self.env["main"]()
        self.assertEqual(self.env["_connect_local"].call_count, 2)
        self.env["_connect_cloud"].assert_called_once()
        cloud.disconnect.assert_called_once()
        self.env["time"].sleep.assert_called_once_with(30)


if __name__ == "__main__":
    unittest.main()
