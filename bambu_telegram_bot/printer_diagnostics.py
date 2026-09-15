"""Read-only, allowlisted MQTT diagnostics. Never includes device identifiers."""
import threading


class PrinterDiagnostics:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.modules = []
        self.status = {}

    def ingest(self, payload):
        if not isinstance(payload, dict):
            return
        with self.lock:
            info = payload.get('info')
            if isinstance(info, dict) and info.get('command') == 'get_version':
                modules = info.get('module')
                if isinstance(modules, list):
                    self.modules = [
                        {k: str(m[k])[:80] for k in ('name', 'sw_ver', 'hw_ver')
                         if isinstance(m.get(k), (str, int))}
                        for m in modules[:12] if isinstance(m, dict)
                    ]
                    self.ready.set()
            data = payload.get('print')
            if isinstance(data, dict):
                for key in ('gcode_state', 'ams_status', 'print_error'):
                    if isinstance(data.get(key), (str, int)):
                        self.status[key] = str(data[key])[:80]
                ams = data.get('ams')
                if isinstance(ams, dict):
                    for key in ('tray_now', 'tray_tar', 'ams_exist_bits', 'tray_exist_bits'):
                        if isinstance(ams.get(key), (str, int)):
                            self.status[key] = str(ams[key])[:80]

    def report(self, fresh):
        with self.lock:
            lines = ['Printer diagnostics (read only)',
                     'Firmware response: ' + ('received' if fresh else 'no fresh response; cached data below')]
            if not self.modules:
                lines.append('Firmware: not reported')
            for module in self.modules:
                lines.append('{}: firmware {} / hardware {}'.format(
                    module.get('name', 'Module'), module.get('sw_ver', 'not reported'),
                    module.get('hw_ver', 'not reported')))
            lines.extend('{}: {}'.format(k, v) for k, v in self.status.items())
            lines.append('AMS slot values above are raw printer values (0–3 = slots 1–4).')
            lines.append('Choosing another slot after runout: not verified. These readings do not prove support.')
            return '\n'.join(lines)
