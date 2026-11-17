"""Gadget control, durable reminders, auth routing and acknowledgment, entirely offline."""
import ast
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1] / 'bambu_telegram_bot'
def module(name):
    spec = importlib.util.spec_from_file_location(name, APP / (name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded

gadget = module('gadget_guard')
octo = module('octoeverywhere_notifications')


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'incidents.json'
        self.now = 10000
        self.current = {'job': 'job-one', 'started': 10000, 'filename': 'Model',
                        'fresh': True, 'state': 'RUNNING'}
        self.pause = Mock(return_value=True)
        self.notify = Mock()
        self.guard = gadget.GadgetGuard(self.path, lambda: dict(self.current), self.pause,
                                       self.notify, clock=lambda: self.now)
        self.start()

    def event(self, kind=7, **extra):
        return {'EventType': kind, 'PrinterId': 'printer', 'PrintId': 'print-one',
                'FileName': 'Model.gcode.3mf', **extra}

    def start(self):
        self.assertEqual(self.guard.receive(self.event(1)), 200)

    def receive(self, **extra):
        self.assertEqual(self.guard.receive(self.event(**extra)), 200)
        return next(reversed(self.guard.incidents))

    def test_pause_request_is_not_confirmation_and_reminders_repeat_until_ack(self):
        key = self.receive()
        self.guard.tick()
        self.pause.assert_called_once_with('job-one')
        self.assertEqual(self.notify.call_args.args[0]['status'], 'requested')
        self.now += 5
        self.guard.tick()
        self.assertEqual(self.pause.call_count, 1)
        self.current['state'] = 'PAUSE'
        self.guard.tick()
        self.assertEqual(self.notify.call_args.args[0]['status'], 'paused')
        self.now += 60
        self.guard.tick()
        self.assertEqual(self.notify.call_count, 3)
        self.assertTrue(self.guard.acknowledge(key))
        self.current['state'] = 'RUNNING'
        self.now += 60
        self.guard.tick()
        self.assertEqual(self.notify.call_count, 3)
        self.assertEqual(self.pause.call_count, 1)

    def test_pause_failure_retries_and_telegram_failure_does_not_block_pause(self):
        self.pause.return_value = False
        self.notify.side_effect = RuntimeError('secret Telegram URL')
        self.receive()
        with self.assertLogs('bambu.gadget', level='WARNING') as logs:
            self.guard.tick()
        self.assertNotIn('secret Telegram URL', str(logs.output))
        self.assertEqual(self.notify.call_args.args[0]['status'], 'failed')
        self.now += 10
        self.notify.side_effect = None
        self.pause.return_value = True
        self.guard.tick()
        self.assertEqual(self.pause.call_count, 2)
        self.assertEqual(self.notify.call_args.args[0]['status'], 'requested')

    def test_pending_and_ack_survive_restart(self):
        key = self.receive()
        self.guard = gadget.GadgetGuard(self.path, lambda: dict(self.current), self.pause,
                                       self.notify, clock=lambda: self.now)
        self.guard.tick()
        self.pause.assert_called_once()
        self.guard.acknowledge(key)
        restarted = gadget.GadgetGuard(self.path, lambda: dict(self.current), self.pause,
                                        self.notify, clock=lambda: self.now + 600)
        restarted.tick()
        self.pause.assert_called_once()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_old_print_same_filename_does_not_pause_new_job(self):
        self.receive()
        self.current['job'] = 'job-two'
        self.current['started'] = self.now + 50
        self.guard.receive(self.event(1, PrintId='print-two'))
        self.guard.tick()
        self.pause.assert_not_called()
        self.assertEqual(self.notify.call_args.args[0]['status'], 'unmatched')
        # Even a delayed old webhook arriving after the new job has started is harmless.
        self.guard.receive(self.event(7, PrintId='print-old'))
        self.guard.tick()
        self.pause.assert_not_called()

    def test_missing_start_stale_state_or_mismatched_file_requires_manual_action(self):
        for change in ({'fresh': False}, {'filename': 'Other'}, {'job': None}):
            with self.subTest(change=change):
                self.current.update(change)
                self.receive(PrintId=str(change))
                self.guard.tick()
                self.pause.assert_not_called()
        self.current.update(fresh=True, filename='Model', job='job-one')
        self.guard.active_print = None
        self.receive(PrintId='missing-start')
        self.guard.tick()
        self.pause.assert_not_called()

    def test_disconnect_after_receipt_recovers_only_for_same_job(self):
        self.receive()
        self.current['fresh'] = False
        self.guard.tick()
        self.pause.assert_not_called()
        self.assertEqual(self.notify.call_args.args[0]['status'], 'unavailable')
        self.current['fresh'] = True
        self.now += 60
        self.guard.tick()
        self.pause.assert_called_once_with('job-one')

    def test_completed_webhook_blocks_pause_even_before_mqtt_finish_arrives(self):
        self.receive()
        self.guard.receive(self.event(2))
        self.guard.tick()
        self.pause.assert_not_called()
        self.notify.assert_called_once()

    def test_authenticated_lifecycle_and_warning_drive_guard_end_to_end(self):
        self.guard.active_print = None
        relay = octo.NotificationRelay(Path(self.directory.name) / 'full-relay.json', Mock(),
                                       gadget_handler=self.guard.receive)
        for kind in (1, 7, 8):
            self.assertEqual(relay.receive({**self.event(kind), 'SecretKey': relay.state['secret']}), 200)
        self.guard.tick()
        self.pause.assert_called_once_with('job-one')
        self.notify.assert_called_once()
        self.assertEqual(len(self.guard.incidents), 1)
        self.assertFalse(relay.state['pending'])

    def test_pause_already_confirmed_does_not_publish(self):
        self.current['state'] = 'PAUSE'
        self.guard.receive(self.event(8))
        self.guard.tick()
        self.pause.assert_not_called()
        self.assertEqual(self.notify.call_args.args[0]['status'], 'paused')

    def test_duplicate_events_merge_but_later_warning_rearms_after_ack(self):
        key = self.receive()
        self.guard.receive(self.event(8))
        self.guard.tick()
        self.assertEqual(len(self.guard.incidents), 1)
        self.guard.acknowledge(key)
        self.receive()
        self.assertEqual(len(self.guard.incidents), 1)
        self.now += 601
        self.receive()
        self.assertEqual(len(self.guard.incidents), 2)
        self.guard.tick()
        self.assertEqual(self.pause.call_count, 2)

    def test_storage_failure_is_not_acknowledged(self):
        with patch.object(self.guard, '_save', side_effect=OSError('full')):
            self.assertEqual(self.guard.receive(self.event()), 503)
        self.assertFalse(self.guard.incidents)
        key = self.receive()
        with patch.object(self.guard, '_save', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                self.guard.acknowledge(key)
        self.assertFalse(self.guard.incidents[key]['acked'])

    def test_start_during_preparation_binds_when_real_print_starts(self):
        self.guard.active_print = None
        self.current.update(state='PREPARE', started=9000, job='old-job')
        self.start()
        self.assertIsNone(self.guard.active_print['job'])
        self.now += 200
        self.current.update(state='RUNNING', started=self.now, job='new-job')
        self.guard.tick()
        self.receive()
        self.guard.tick()
        self.pause.assert_called_once_with('new-job')

    def test_unknown_ack_does_not_silence_another_incident(self):
        key = self.receive()
        self.assertFalse(self.guard.acknowledge('bad-id'))
        self.assertFalse(self.guard.incidents[key]['acked'])

    def test_no_idle_disk_writes_and_localized_text(self):
        with patch.object(self.guard, '_save') as save:
            self.guard.tick()
            save.assert_not_called()
        self.receive()
        self.guard.tick()
        record = self.notify.call_args.args[0]
        self.assertIn('not yet confirmed', gadget.urgent_text(record, 'en'))
        self.assertIn('עדיין לא אושרה', gadget.urgent_text(record, 'he'))
        with patch.object(self.guard, '_save') as save:
            self.guard.tick()
            save.assert_not_called()

    def test_webhook_requires_secret_and_routes_only_expected_events(self):
        handler = Mock(return_value=200)
        relay = octo.NotificationRelay(Path(self.directory.name) / 'relay.json', Mock(), gadget_handler=handler)
        event = {**self.event(), 'SecretKey': 'wrong'}
        self.assertEqual(relay.receive(event), 403)
        handler.assert_not_called()
        for kind in (1, 2, 3, 7, 8):
            self.assertEqual(relay.receive({**event, 'EventType': kind, 'SecretKey': relay.state['secret']}), 200)
        self.assertEqual(handler.call_count, 5)
        self.assertFalse(relay.state['pending'])
        relay.receive({**event, 'EventType': 16, 'SecretKey': relay.state['secret']})
        self.assertEqual(handler.call_count, 5)
        self.assertEqual(len(relay.state['pending']), 1)
        self.assertNotIn('SecretKey', handler.call_args.args[0])


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.env = {'_lock': threading.Lock(), '_state': {'start_time': datetime(2026, 11, 17),
                    'filename': 'Model', 'gcode_state': 'RUNNING', '_last_print_report': 100,
                    '_live_gcode': True}, '_mqtt_client': Mock(), 'time': Mock(), 'json': json,
                    'PRINTER_SERIAL': 'test', '_gadget_guard': Mock(), 'bot': Mock(),
                    'LANGUAGE': 'he', 'TELEGRAM_CHAT_ID': '123', 'telebot': Mock(),
                    'urgent_text': gadget.urgent_text}
        self.env['time'].monotonic.return_value = 105
        self.env['_mqtt_client'].publish.return_value.rc = 0
        source = APP / 'bambu_monitor.py'
        names = {'_gadget_printer_state', '_gadget_snapshot', '_gadget_pause', 'cb_gadget_ack',
                 'chat_ok', '_send_gadget_urgent'}
        nodes = []
        for node in ast.parse(source.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                node.decorator_list = []
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), self.env)

    def test_control_rechecks_job_connection_freshness_and_publish_result(self):
        job = self.env['_gadget_snapshot']()['job']
        self.assertTrue(self.env['_gadget_pause'](job))
        payload = json.loads(self.env['_mqtt_client'].publish.call_args.args[1])
        self.assertEqual(payload['print']['command'], 'pause')
        self.assertFalse(self.env['_gadget_pause']('different-job'))
        self.env['_mqtt_client'].publish.return_value.rc = 4
        self.assertFalse(self.env['_gadget_pause'](job))
        self.env['_mqtt_client'].publish.reset_mock()
        self.env['time'].monotonic.return_value = 131
        self.assertFalse(self.env['_gadget_pause'](job))
        self.env['_mqtt_client'].publish.assert_not_called()

    def test_callback_checks_chat_and_acknowledges_without_resuming(self):
        for chat in ('999', '123'):
            call = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=chat)),
                                   data='gadget_ack_incident', id='callback')
            self.env['cb_gadget_ack'](call)
            if chat == '999':
                self.env['_gadget_guard'].acknowledge.assert_not_called()
        self.env['_gadget_guard'].acknowledge.assert_called_once_with('incident')
        self.env['_mqtt_client'].publish.assert_not_called()
        self.assertIn('לא חודשה', self.env['bot'].answer_callback_query.call_args.kwargs['text'])

    def test_urgent_telegram_message_requests_sound_and_includes_ack_button(self):
        self.env['_send_gadget_urgent']({'id': 'abc', 'status': 'requested', 'event': {'FileName': 'Model'}})
        self.assertFalse(self.env['bot'].send_message.call_args.kwargs['disable_notification'])
        self.assertEqual(self.env['telebot'].types.InlineKeyboardButton.call_args.kwargs['callback_data'], 'gadget_ack_abc')
