"""Conservative pause explanations from reported printer error codes."""
import time
from error_reasons import REASONS


class PauseReason:
    def __init__(self):
        self.code = 0
        self.updated = 0
        self.announced = None

    def update(self, data, state, previous):
        if previous == 'PAUSE' and state != 'PAUSE':
            self.code = 0
            self.updated = 0
            self.announced = None
        if 'print_error' in data:
            try:
                raw = data['print_error']
                code = int(raw, 0) if isinstance(raw, str) and raw.lower().startswith('0x') else int(raw)
                self.code = code if 0 <= code <= 0xffffffff else 0
            except (ValueError, TypeError, OverflowError):
                self.code = 0
            self.updated = time.monotonic()
        if state == 'PAUSE' and previous != 'PAUSE' and time.monotonic() - self.updated > 15:
            self.code = 0
        if state != 'PAUSE':
            self.announced = None

    def describe(self, language, tray):
        he = language == 'he'
        if not self.code:
            return 'המדפסת לא דיווחה על סיבת ההשהיה.' if he else 'The printer did not report a pause reason.'
        code = f'{self.code:08X}'
        # Bambu runout codes: 12008011 through 12038011 and 12FF8011.
        if self.code in {0x12008011, 0x12018011, 0x12028011, 0x12038011, 0x12FF8011}:
            reason = 'הפילמנט ב-AMS נגמר' if he else 'AMS filament ran out'
            if isinstance(tray, int) and 0 <= tray <= 3:
                reason += f' — סלוט פעיל {tray + 1}' if he else f' — active slot {tray + 1}'
        elif self.code in REASONS:
            reason = REASONS[self.code][1 if he else 0]
        else:
            reason = 'שגיאת מדפסת; בדוק את ההודעה במדפסת או ב-Bambu Handy' if he else 'Printer error; check the message on the printer or in Bambu Handy'
        return f'{reason} ({code})'
