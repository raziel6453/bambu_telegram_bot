import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import test_ams_notifications

spec = importlib.util.spec_from_file_location('pause_reason', Path(__file__).resolve().parents[1] / 'bambu_telegram_bot/pause_reason.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PauseReasonTest(unittest.TestCase):
    def test_runout_and_unknown(self):
        reason = module.PauseReason()
        reason.update({'print_error': 302022673}, 'PAUSE', 'RUNNING')
        self.assertIn('active slot 2', reason.describe('en', 1))
        self.assertIn('נגמר', reason.describe('he', 1))
        reason.update({'print_error': 123}, 'PAUSE', 'PAUSE')
        self.assertIn('0000007B', reason.describe('en', 255))
        self.assertNotIn('ran out', reason.describe('en', 255))

    def test_missing_stale_and_cleared_errors_are_not_runout(self):
        reason = module.PauseReason()
        with patch.object(module.time, 'monotonic', return_value=100):
            reason.update({'print_error': 302022673}, 'RUNNING', 'RUNNING')
        with patch.object(module.time, 'monotonic', return_value=120):
            reason.update({}, 'PAUSE', 'RUNNING')
        self.assertIn('did not report', reason.describe('en', 1))
        reason.update({'print_error': 302022673}, 'PAUSE', 'PAUSE')
        reason.update({}, 'RUNNING', 'PAUSE')
        self.assertEqual(reason.code, 0)

    def test_delayed_error_notifies_once_in_production_handler(self):
        fixture = test_ams_notifications.AmsNotificationsTest()
        fixture.setUp()
        env = fixture.env
        env['_pause_reason'] = module.PauseReason()
        env['LANGUAGE'] = 'en'
        env['tg_send'] = Mock()
        env['_state'].update(printing=True, gcode_state='RUNNING', tray_now=1)
        import json
        from types import SimpleNamespace
        def report(data):
            env['on_message'](None, None, SimpleNamespace(payload=json.dumps({'print':data}).encode()))
        report({'gcode_state':'PAUSE', 'print_error':0})
        self.assertIn('did not report', env['tg_send'].call_args.args[0])
        report({'print_error':302022673})
        self.assertIn('active slot 2', env['tg_send'].call_args.args[0])
        count = env['tg_send'].call_count
        report({'print_error':302022673})
        self.assertEqual(count, env['tg_send'].call_count)
