"""Concise bilingual explanations based on BambuStudio resources/hms/hms_en_*.json.
Source: https://github.com/bambulab/BambuStudio/tree/master/resources/hms
Only exact known codes are mapped. Unknown firmware codes retain the fallback.
"""
AMS_REASONS = {
    0x8001: ('Filament cutting failed; check the cutter', 'חיתוך הפילמנט נכשל; בדוק את הסכין'),
    0x8002: ('Filament cutter is stuck', 'סכין חיתוך הפילמנט תקועה'),
    0x8003: ('Could not withdraw filament from the extruder; possible blockage or broken filament', 'משיכת הפילמנט מהאקסטרודר נכשלה; ייתכנו סתימה או פילמנט שבור'),
    0x8004: ('Could not retract filament from the toolhead; it may be stuck', 'החזרת הפילמנט מראש ההדפסה נכשלה; ייתכן שהוא תקוע'),
    0x8005: ('Filament has not been inserted', 'לא הוכנס פילמנט'),
    0x8006: ('Filament could not reach the extruder; check the spool and feed tube', 'הפילמנט לא הגיע לאקסטרודר; בדוק את הספול וצינור ההזנה'),
    0x8007: ('Filament extrusion failed; possible blockage or stuck filament', 'הוצאת הפילמנט נכשלה; ייתכנו סתימה או פילמנט תקוע'),
    0x8010: ('Filament or spool may be stuck', 'ייתכן שהפילמנט או הספול תקועים'),
    0x8012: ('Printer could not read the AMS slot mapping', 'המדפסת לא הצליחה לקרוא את מיפוי הסלוטים של ה-AMS'),
    0x8013: ('Purging old filament timed out; check for a blockage', 'ניקוי הפילמנט הקודם ארך זמן רב מדי; בדוק אם יש סתימה'),
    0x8014: ('Printer could not locate filament in the toolhead', 'המדפסת לא הצליחה לזהות את מיקום הפילמנט בראש ההדפסה'),
    0x8015: ('Toolhead filament withdrawal failed; filament may be stuck or broken', 'משיכת הפילמנט מראש ההדפסה נכשלה; ייתכן שהוא תקוע או שבור'),
    0x8016: ('Extrusion is abnormal; check the extruder and printed layer', 'הזנת הפילמנט אינה תקינה; בדוק את האקסטרודר והשכבה המודפסת'),
}
REASONS = {
    0x03008000: ('Printer reported an unspecified pause', 'המדפסת דיווחה על השהיה ללא סיבה מפורטת'),
    0x03008001: ('Pause programmed into the print file', 'השהיה שתוכנתה בקובץ ההדפסה'),
    0x03008013: ('Print paused by the user', 'ההדפסה הושהתה על ידי המשתמש'),
    0x03008008: ('Nozzle temperature fault', 'תקלה בטמפרטורת הדיזה'),
    0x03008009: ('Heated bed temperature fault', 'תקלה בטמפרטורת משטח ההדפסה'),
    0x03008016: ('Nozzle blocked by filament', 'הדיזה סתומה בפילמנט'),
    0x03004006: ('Nozzle is clogged', 'הדיזה סתומה'),
    0x0300801C: ('Abnormal extrusion resistance; possible extruder blockage', 'התנגדות חריגה בהזנה; ייתכן שהאקסטרודר סתום'),
    0x0300801E: ('Extruder motor overload; check for stuck filament or a blockage', 'עומס יתר במנוע האקסטרודר; בדוק אם הפילמנט תקוע או קיימת סתימה'),
    0x03008003: ('AI monitoring detected possible spaghetti print failure', 'ניטור ההדפסה זיהה חשד לכשל ספגטי'),
    0x0300800F: ('Printer paused because the door appears open', 'המדפסת הושהתה כי הדלת נראית פתוחה'),
}
# Explicit AMS Lite unit variants present in Bambu's error catalog.
for unit in (0, 1, 2, 3, 255):
    for suffix, reason in AMS_REASONS.items():
        REASONS[0x12000000 | unit << 16 | suffix] = reason

# Variants absent from the source catalog are deliberately excluded.
for code in (318734356, 318734357, 318734358):
    REASONS.pop(code, None)
