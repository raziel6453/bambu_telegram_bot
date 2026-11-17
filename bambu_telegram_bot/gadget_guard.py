"""Durable, acknowledged Gadget incidents. Only authenticated relay events enter here."""
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time

log = logging.getLogger('bambu.gadget')
REMINDER_SECONDS = 60
PAUSE_RETRY_SECONDS = 10
REPEAT_WINDOW = 600


def same_file(first, second):
    def normalize(value):
        name = str(value or '').strip().casefold()
        for suffix in ('.gcode.3mf', '.3mf', '.gcode'):
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return name
    return bool(normalize(first)) and normalize(first) == normalize(second)


class GadgetGuard:
    def __init__(self, path, snapshot, pause, notify, *, clock=time.time):
        self.path = Path(path)
        self.snapshot, self.pause, self.notify, self.clock = snapshot, pause, notify, clock
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.worker = None
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.incidents = saved.get('incidents', {})
        self.active_print = saved.get('active_print')
        if not isinstance(self.incidents, dict):
            raise ValueError('Invalid Gadget incident state')

    def _save(self):
        temporary = self.path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump({'incidents': self.incidents, 'active_print': self.active_print},
                      output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.path)

    def receive(self, event):
        if event.get('EventType') not in (1, 2, 3, 7, 8):
            return 400
        source = hashlib.sha256(json.dumps([event['PrinterId'], event['PrintId']]).encode()).hexdigest()
        with self.lock:
            now = self.clock()
            if event['EventType'] in (1, 2, 3):
                before = copy.deepcopy(self.active_print)
                if event['EventType'] == 1:
                    if self.active_print and self.active_print['source'] == source:
                        return 200
                    self.active_print = {'source': source, 'created': now,
                                         'filename': event.get('FileName'), 'job': None}
                    self._bind_print(self.snapshot())
                elif self.active_print and self.active_print['source'] == source:
                    self.active_print = None
                try:
                    self._save()
                except OSError:
                    self.active_print = before
                    return 503
                return 200
            previous = [r for r in self.incidents.values() if r['source'] == source]
            if previous:
                latest = max(previous, key=lambda r: r['created'])
                # Duplicate warnings never restart the reminder timer. A later
                # warning may open another incident after the existing dedup window.
                if not latest['acked'] or now - latest['created'] < REPEAT_WINDOW:
                    return 200
            before = copy.deepcopy(self.incidents)
            if len(self.incidents) >= 128:
                acknowledged = [r for r in self.incidents.values() if r['acked']]
                if not acknowledged:
                    return 503
                del self.incidents[min(acknowledged, key=lambda r: r['created'])['id']]
            current = self.snapshot()
            self._bind_print(current)
            matches = (self.active_print and self.active_print['source'] == source
                       and self.active_print['job'] == current.get('job')
                       and current.get('fresh') and current.get('job')
                       and current.get('state') in ('RUNNING', 'PAUSE')
                       and same_file(event.get('FileName'), current.get('filename')))
            incident_id = secrets.token_hex(8)
            self.incidents[incident_id] = {
                'id': incident_id, 'source': source, 'event': dict(event),
                'job': current['job'] if matches else None,
                'created': now, 'acked': False, 'next_notice': now,
                'next_pause': now, 'status': None,
            }
            try:
                self._save()
            except OSError:
                self.incidents = before
                return 503
            self.wake.set()
            return 200

    def _bind_print(self, current):
        active = self.active_print
        if (active and not active['job'] and current.get('fresh') and current.get('job')
                and current.get('state') in ('RUNNING', 'PAUSE')
                and same_file(active.get('filename'), current.get('filename'))
                and active['created'] - 120 <= current.get('started', 0) <= active['created'] + 3600):
            active['job'] = current['job']

    def acknowledge(self, incident_id):
        with self.lock:
            record = self.incidents.get(incident_id)
            if not record:
                return False
            previous = record['acked']
            record['acked'] = True
            try:
                self._save()
            except OSError:
                record['acked'] = previous
                raise
            return True

    def tick(self):
        # Pause processing is independent of Telegram success. Never claim the
        # machine is paused merely because MQTT accepted a publish.
        notices = []
        with self.lock:
            now = self.clock()
            current = self.snapshot()
            before = copy.deepcopy((self.incidents, self.active_print))
            self._bind_print(current)
            for record in self.incidents.values():
                if record['acked']:
                    continue
                matches = (bool(record['job']) and record['job'] == current.get('job')
                           and self.active_print is not None
                           and self.active_print['source'] == record['source'])
                if not matches:
                    status = 'unmatched'
                elif not current.get('fresh'):
                    status = 'unavailable'
                elif current.get('state') == 'PAUSE':
                    status = 'paused'
                elif current.get('state') == 'RUNNING':
                    status = record['status'] if record['status'] in ('requested', 'failed') else 'requested'
                    if record['next_pause'] <= now:
                        record['next_pause'] = now + PAUSE_RETRY_SECONDS
                        self._save()
                        try:
                            status = 'requested' if self.pause(record['job']) else 'failed'
                        except Exception:
                            status = 'failed'
                else:
                    status = 'inactive'
                # Confirmed pause gets one prompt update; other status changes
                # cannot cause a rapid stream of Telegram messages.
                confirmed = status == 'paused' and record['status'] != 'paused'
                record['status'] = status
                if now >= record['next_notice'] or confirmed:
                    record['next_notice'] = now + REMINDER_SECONDS
                    notices.append(copy.deepcopy(record))
            if (self.incidents, self.active_print) != before:
                self._save()
        for record in notices:
            with self.lock:
                if self.incidents[record['id']]['acked']:
                    continue
            try:
                self.notify(record)
            except Exception as error:
                log.warning('Gadget urgent notification failed (%s)', type(error).__name__)
                with self.lock:
                    self.incidents[record['id']]['next_notice'] = self.clock() + 10
                    self._save()

    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception as error:
                    log.error('Gadget incident processing failed (%s)', type(error).__name__)
                self.wake.wait(1)
                self.wake.clear()
        self.worker = threading.Thread(target=run, daemon=True, name='gadget-guard')
        self.worker.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=2)


def urgent_text(record, language):
    he = language == 'he'
    status = {
        'paused': ('⏸️ Printer confirmed paused.', '⏸️ המדפסת אישרה שההדפסה מושהית.'),
        'requested': ('⏳ Pause requested — not yet confirmed. Check the printer now.',
                      '⏳ נשלחה פקודת Pause — העצירה עדיין לא אושרה. בדוק את המדפסת עכשיו.'),
        'failed': ('❌ Could not send Pause. Pause the printer manually now.',
                   '❌ שליחת Pause נכשלה. עצור את ההדפסה ידנית עכשיו.'),
        'unavailable': ('⚠️ Printer connection/state unavailable. Pause manually if needed.',
                        '⚠️ החיבור או מצב המדפסת אינם עדכניים. עצור ידנית במידת הצורך.'),
        'unmatched': ('⚠️ Could not match this alert to the current print. Check and pause manually.',
                      '⚠️ לא ניתן לשייך את ההתראה להדפסה הנוכחית. בדוק ועצור ידנית.'),
        'inactive': ('ℹ️ The printer is no longer reporting an active print.',
                     'ℹ️ המדפסת כבר אינה מדווחת על הדפסה פעילה.'),
    }
    name = str(record['event'].get('FileName') or '–').replace('\n', ' ')[:120]
    return '\n'.join([
        '🚨 דחוף: Gadget זיהה חשד לכשל / ספגטי!' if he else '🚨 URGENT: Gadget reported a possible failure / spaghetti!',
        ('📄 קובץ: ' if he else '📄 File: ') + name,
        status[record['status']][int(he)],
        ('🔔 אשלח תזכורת בכל דקה עד לאישור שלך. האישור משתיק את התזכורות ואינו מחדש את ההדפסה.'
         if he else '🔔 Reminders every minute until acknowledged. Acknowledging silences reminders; it does not resume printing.'),
    ])
