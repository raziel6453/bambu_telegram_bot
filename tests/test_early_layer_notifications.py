"""Offline MQTT lifecycle checks for early layer status notifications."""
import ast
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class EarlyLayerNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.env = {
            'json': json, 'math': math, 'datetime': datetime, 'timedelta': timedelta,
            'time': Mock(), 'log': Mock(), '_diagnostics': Mock(), '_pause_reason': Mock(),
            '_lock': threading.Lock(), '_status_event': threading.Event(),
            'HA_AVAILABLE': False, 'LANGUAGE': 'en', 'STATE_FILE': 'unused',
            '_capture_spool_start': Mock(), '_ha_weight_entity': Mock(return_value='sensor.weight'),
            '_ha_sensor': Mock(return_value='81.49'), 'smart_remaining': lambda: 40,
            'fmt_mins': lambda n: f'{n}m', 'finish_time': lambda n: '12:40', 'tg_photo': Mock(),
            '_save_json': lambda path, value: self.saved.update(value),
            '_load_json': lambda path, default: dict(self.saved),
        }
        source = Path(__file__).resolve().parents[1] / 'bambu_telegram_bot/bambu_monitor.py'
        names = {'t', 'on_message', '_on_print_start', '_on_reconnect_recovery',
                 '_notify_early_layers', '_persist_state', '_restore_state'}
        nodes = []
        for node in ast.parse(source.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                                                       {'_state', 'STRINGS'} for t in node.targets):
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), self.env)
        self.state = self.env['_state']
        self.photo = self.env['tg_photo']

    def report(self, **data):
        self.env['on_message'](None, None, SimpleNamespace(payload=json.dumps({'print': data}).encode()))

    def start(self):
        self.report(gcode_state='RUNNING', layer_num=0, total_layer_num=100, subtask_name='Model')

    def test_start_is_short_and_updates_follow_completed_layers_once(self):
        self.start()
        self.assertEqual(self.photo.call_args.args[0], '🖨️ Print started!\n📄 File: Model')
        self.env['HA_AVAILABLE'] = True
        for layer in (1, 1, 2, 2, 3, 4, 4, 5):
            self.report(layer_num=layer, mc_percent=5)
        messages = [c.args[0] for c in self.photo.call_args_list]
        self.assertEqual(len(messages), 3)
        self.assertIn('Layer 1 completed', messages[1])
        self.assertIn('Layer 3 completed', messages[2])
        self.assertIn('81.5g', messages[1])
        self.assertIn('40m', messages[2])
        self.assertEqual(self.saved['last_layer_notice'], 3)

    def test_existing_percentage_milestones_are_unchanged(self):
        self.start()
        for layer, pct in ((2, 10), (4, 20), (25, 25), (25, 25), (50, 50), (75, 75)):
            self.report(layer_num=layer, mc_percent=pct)
        messages = [c.args[0] for c in self.photo.call_args_list]
        self.assertEqual(len(messages), 6)
        for message, percent in zip(messages[-3:], (25, 50, 75)):
            self.assertTrue(message.startswith(f'📊 Progress: {percent}%'))

    def test_restart_keeps_notice_markers_and_new_print_resets_them(self):
        self.start()
        self.report(layer_num=2)
        self.state['last_layer_notice'] = 0
        self.env['_restore_state']()
        self.report(layer_num=2)
        self.assertEqual(self.photo.call_count, 2)
        self.report(layer_num=4)
        self.assertEqual(self.photo.call_count, 3)
        self.state.update(gcode_state='FINISH', printing=False)
        self.start()
        self.report(layer_num=2)
        self.assertEqual(self.photo.call_count, 5)

    def test_paused_reports_wait_for_running_and_skipped_layers_send_one_status(self):
        self.start()
        self.state['gcode_state'] = 'PAUSE'
        self.env['_pause_reason'].code = None
        self.report(gcode_state='PAUSE', layer_num=4)
        self.assertEqual(self.photo.call_count, 1)
        self.report(gcode_state='RUNNING', layer_num=4)
        self.assertEqual(self.photo.call_count, 2)
        self.assertIn('Layer 3 completed', self.photo.call_args.args[0])

    def test_mid_print_first_connection_does_not_replay_early_notices(self):
        self.report(gcode_state='RUNNING', layer_num=40, mc_percent=10)
        self.report(layer_num=41)
        self.assertEqual(self.photo.call_count, 1)
        self.state.update(printing=False, gcode_state='RUNNING')
        self.report(layer_num=42)
        self.assertEqual(self.photo.call_count, 1)

    def test_hebrew_status_and_unavailable_weight(self):
        self.env['LANGUAGE'] = 'he'
        self.start()
        self.report(layer_num=2)
        self.assertIn('שכבה 1 הסתיימה', self.photo.call_args.args[0])
        self.assertIn('משקל צפוי: –', self.photo.call_args.args[0])

    def test_new_print_without_layer_does_not_inherit_previous_jobs_layer(self):
        self.state.update(gcode_state='FINISH', layer_num=100, last_layer_notice=3)
        self.report(gcode_state='RUNNING', subtask_name='New model')
        self.report(layer_num=1)
        self.report(layer_num=2)
        self.assertEqual(self.photo.call_count, 2)
        self.assertIn('Layer 1 completed', self.photo.call_args.args[0])
