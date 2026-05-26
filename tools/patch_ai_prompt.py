"""Patch _generate_ai_prompt in telemetry_analyzer.py to include car tuning block."""
import re

path = r'c:\Storage\my documents\sim-laps-client\src\core\telemetry_analyzer.py'

with open(path, 'rb') as f:
    raw = f.read()

content = raw.decode('utf-8')

# ------------------------------------------------------------------
# Patch 1: replace the car_known preamble to use tuning_block
# ------------------------------------------------------------------
OLD_PREAMBLE = (
    '        if car_known:\r\n'
    '            lines.append(\r\n'
    '                f"If you have knowledge of the {car_model} setup parameters in AC Evo, "\r\n'
    '                f"use it. Otherwise limit setup advice to parameters confirmed by the telemetry "\r\n'
    '                f"signals (brake bias, tyre pressure, balance)."\r\n'
    '            )\r\n'
    '        else:\r\n'
    '            lines.append(\r\n'
    '                "Car identity was not captured from shared memory. "\r\n'
    '                "Do NOT guess the car or fabricate setup parameters. "\r\n'
    '                "Limit advice to driving technique and parameters visible in the data "\r\n'
    '                "(brake bias, tyre pressure, balance hints)."\r\n'
    '            )\r\n'
    '        lines.append("")'
)

NEW_PREAMBLE = (
    '        tuning_block = format_tuning_block(car_model) if car_known else ""\r\n'
    '        if car_known and tuning_block:\r\n'
    '            lines.append(\r\n'
    '                f"The available setup parameters for the {car_model} in AC Evo are listed "\r\n'
    '                f"in the CAR SETUP PARAMETERS section below. "\r\n'
    '                f"Only recommend changes from that list."\r\n'
    '            )\r\n'
    '        elif car_known:\r\n'
    '            lines.append(\r\n'
    '                f"If you have knowledge of the {car_model} setup parameters in AC Evo, "\r\n'
    '                f"use it. Otherwise limit setup advice to parameters confirmed by the telemetry "\r\n'
    '                f"signals (brake bias, tyre pressure, balance)."\r\n'
    '            )\r\n'
    '        else:\r\n'
    '            lines.append(\r\n'
    '                "Car identity was not captured from shared memory. "\r\n'
    '                "Do NOT guess the car or fabricate setup parameters. "\r\n'
    '                "Limit advice to driving technique and parameters visible in the data "\r\n'
    '                "(brake bias, tyre pressure, balance hints)."\r\n'
    '            )\r\n'
    '        lines.append("")'
)

if OLD_PREAMBLE not in content:
    print("ERROR: preamble block not found")
    exit(1)

content = content.replace(OLD_PREAMBLE, NEW_PREAMBLE, 1)
print("Patch 1 applied: preamble block updated")

# ------------------------------------------------------------------
# Patch 2: inject tuning_block section after ANALYSIS NOTES block
# ------------------------------------------------------------------
OLD_NOTES_TAIL = (
    '        lines.append("")\r\n'
    '\r\n'
    '        # \u2500\u2500 Session overview\r\n'
    '        lines.append("SESSION OVERVIEW:")'
)

NEW_NOTES_TAIL = (
    '        lines.append("")\r\n'
    '\r\n'
    '        # \u2500\u2500 Car setup parameters\r\n'
    '        if tuning_block:\r\n'
    '            lines.append(tuning_block)\r\n'
    '            lines.append("")\r\n'
    '\r\n'
    '        # \u2500\u2500 Session overview\r\n'
    '        lines.append("SESSION OVERVIEW:")'
)

if OLD_NOTES_TAIL not in content:
    print("ERROR: session overview anchor not found")
    exit(1)

content = content.replace(OLD_NOTES_TAIL, NEW_NOTES_TAIL, 1)
print("Patch 2 applied: tuning_block section inserted")

with open(path, 'wb') as f:
    f.write(content.encode('utf-8'))

print("File written successfully")
