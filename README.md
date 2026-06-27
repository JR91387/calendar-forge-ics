<p align="center">
  <img src="01_App/logo.png" alt="Calendar Forge" width="680">
</p>

# Calendar Forge

Merge, clean, and rename `.ics` calendar files into a single import-ready file. Strip attendees, reminders, and vendor clutter so old events don't trigger notifications. Filter by date, add per-file prefixes or postfixes, and deduplicate across sources.

## Requirements

- Python 3.8+
- `tkinter` — included with most Python installs. Ubuntu: `sudo apt install python3-tk`

All other dependencies are bundled in `01_App/lib/` — no pip, no internet required.

## Run

**Windows** — copy the folder to a local path first (e.g. `C:\CalendarForge\`), then:
```
run_calendarforge.bat
```

**Linux / Mac**
```
bash run_calendarforge.sh
```

## Usage

1. Drop `.ics` files into `03_Input/` — they auto-load on launch
2. Set per-file prefix / postfix on the **Files** tab if needed
3. Configure strip options on the **Options** tab
4. Hit **Generate** on the **Output** tab
5. Import `04_Output/merged.ics` into Google Calendar, Outlook, or Apple Calendar

See `02_user_manual/user_instructions_and_notes.txt` for a full walkthrough.
