import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('printer_diagnostics', Path(__file__).resolve().parents[1] / 'bambu_telegram_bot/printer_diagnostics.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DiagnosticsTest(unittest.TestCase):
    def test_version_only_message_and_privacy(self):
        d = module.PrinterDiagnostics()
        d.ingest({'info': {'command': 'get_version', 'module': [
            {'name': 'ota', 'sw_ver': '01.02.03.04', 'hw_ver': 'A1', 'sn': 'SECRET'}]},
            'ip': 'SECRET', 'access_code': 'SECRET'})
        self.assertTrue(d.ready.is_set())
        self.assertIn('01.02.03.04', d.report(True))
        self.assertNotIn('SECRET', d.report(True))

    def test_partial_reports_merge_and_timeout_is_explicit(self):
        d = module.PrinterDiagnostics()
        d.ingest({'print': {'gcode_state': 'PAUSE', 'ams': {'tray_now': '1'}}})
        d.ingest({'print': {'ams_status': 768}})
        report = d.report(False)
        for text in ['PAUSE', 'tray_now: 1', '768', 'no fresh response', 'not verified']:
            self.assertIn(text, report)

    def test_malformed_payloads_do_not_signal_fresh_data(self):
        d = module.PrinterDiagnostics()
        for value in [None, [], {'info': []}, {'info': {'command': 'get_version', 'module': None}}, {'print': {'ams': []}}]:
            d.ingest(value)
        self.assertFalse(d.ready.is_set())

    def test_command_is_authorized_and_only_requests_reads(self):
        import ast
        from unittest.mock import Mock
        source = Path(__file__).resolve().parents[1] / 'bambu_telegram_bot/bambu_monitor.py'
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'cmd_printerinfo')
        node.decorator_list = []
        env = {'chat_ok': Mock(return_value=False), 'bot': Mock(), '_diagnostics': Mock(),
               '_mqtt_publish': Mock(return_value=True), 'request_pushall': Mock()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), env)
        message = Mock()
        env['cmd_printerinfo'](message)
        env['_mqtt_publish'].assert_not_called()
        env['chat_ok'].return_value = True
        env['cmd_printerinfo'](message)
        env['_mqtt_publish'].assert_called_once_with({'info': {'sequence_id': '0', 'command': 'get_version'}})
        env['request_pushall'].assert_called_once_with()
