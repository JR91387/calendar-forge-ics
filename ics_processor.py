import re
import sys
import copy
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from icalendar import Calendar, Event, vText

log = logging.getLogger(__name__)

# zoneinfo is stdlib in Python 3.9+; tzdata PyPI package supplies data on Windows
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Python < 3.9 — UTC-only mode; _get_tz warns

# Clamp only genuine pre-1900 timestamps (Outlook/KOrganizer bug) before parsing.
# Matches: negative dates (e.g. -4713010T…) or years 0000-1899 (e.g. 16010101T…).
# Does NOT match modern dates (1900+).
_PRE1900 = re.compile(
    rb'((?:DTSTART|DTEND|DTSTAMP|RECURRENCE-ID)[^:]*:[ \t]*)'  # group 1: property + colon
    rb'(-[^\r\n]+|(?:0\d{3}|1[0-8]\d{2})\d{4}[^\r\n]*)',       # group 2: bad date value
    re.IGNORECASE,
)


@dataclass
class ProcessingOptions:
    attendee_mode: str = "keep"       # "keep" | "remove" | "remove_append"
    strip_organizer: bool = False
    strip_alarms: bool = True
    append_alarms: bool = False
    strip_x_props: bool = True
    exclude_cancelled: bool = False
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    calendar_name: str = "ICScrub Export"
    user_tz_name: str = "UTC"         # IANA name; used to interpret naive datetimes


@dataclass
class MergeStats:
    per_file: dict = field(default_factory=dict)
    duplicates_removed: int = 0
    skipped_no_dtstart: int = 0
    date_filtered: int = 0
    cancelled_excluded: int = 0


# ── timezone helpers ─────────────────────────────────────────────────────────

def _get_tz(name: str):
    """Return a tzinfo for the given IANA name. Raises ValueError on bad name.
    Falls back to UTC with a warning if zoneinfo is unavailable (Python < 3.9)."""
    name = (name or "UTC").strip()
    if not name or name.upper() == "UTC":
        return timezone.utc
    if ZoneInfo is None:
        log.warning("Named timezones require Python 3.9+. Defaulting to UTC.")
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception as e:
        raise ValueError(
            f"Unknown timezone {name!r}. Use an IANA name like 'America/Chicago'."
        ) from e


def _to_aware(dt_val, tz) -> datetime:
    """Normalize to a tz-aware datetime for safe comparison.
    All-day date objects become midnight in tz. Naive datetimes are assumed to be in tz."""
    if isinstance(dt_val, date) and not isinstance(dt_val, datetime):
        return datetime(dt_val.year, dt_val.month, dt_val.day, tzinfo=tz)
    if isinstance(dt_val, datetime):
        return dt_val.replace(tzinfo=tz) if dt_val.tzinfo is None else dt_val.astimezone(tz)
    raise TypeError(f"Expected date or datetime, got {type(dt_val)!r}")


# ── ingest (adapted from ics-repair) ─────────────────────────────────────────

def _read_file_chunks(file_path: Path) -> list:
    try:
        raw = file_path.read_bytes()
    except OSError as e:
        raise OSError(f"Cannot read {file_path.name}: {e}") from e

    raw = _PRE1900.sub(lambda m: m.group(1) + b'19000101T000000', raw)

    chunks, start = [], 0
    while True:
        idx = raw.find(b'BEGIN:VCALENDAR', start)
        if idx == -1:
            break
        end = raw.find(b'END:VCALENDAR', idx)
        if end == -1:
            log.warning("%s: VCALENDAR block missing END tag", file_path.name)
            break
        chunks.append(raw[idx:end + len(b'END:VCALENDAR')])
        start = end + len(b'END:VCALENDAR')

    if not chunks:
        raise ValueError(f"{file_path.name}: no VCALENDAR blocks found")
    return chunks


def _parse_chunks(chunks: list, filename: str) -> list:
    calendars = []
    for i, chunk in enumerate(chunks, 1):
        # Try UTF-8 first; fall back to latin-1 for old Outlook exports
        try:
            text = chunk.decode('utf-8')
        except UnicodeDecodeError:
            text = chunk.decode('latin-1')
            log.warning("%s chunk %d: non-UTF-8 content, decoded as latin-1", filename, i)
        try:
            calendars.append(Calendar.from_ical(text))
        except Exception as e:
            raise ValueError(f"{filename} chunk {i}: parse failed: {e}") from e
    return calendars


# ── transforms ───────────────────────────────────────────────────────────────

def _attendee_display(prop) -> str:
    cn = prop.params.get("CN", "")
    email = str(prop).replace("mailto:", "").replace("MAILTO:", "")
    return f"{cn} <{email}>" if cn else email


def _alarm_display(alarm) -> str:
    trigger = alarm.get("TRIGGER")
    action = str(alarm.get("ACTION", "DISPLAY"))
    if trigger is None:
        return f"(unknown trigger) ({action})"
    td = trigger.dt
    if hasattr(td, "total_seconds"):
        minutes = int(abs(td.total_seconds()) // 60)
        if minutes >= 1440:
            return f"{minutes // 1440} day(s) before ({action})"
        return f"{minutes} minute(s) before ({action})"
    return f"{td} ({action})"


def transform_event(event: Event, prefix: str, postfix: str, opts: ProcessingOptions) -> Event:
    ev = copy.deepcopy(event)

    # SUMMARY
    summary = str(ev.get("SUMMARY", ""))
    new_summary = f"{prefix}{summary}{postfix}" if (prefix or postfix) else summary
    if new_summary != summary:
        ev["SUMMARY"] = vText(new_summary)

    # Attendees
    if opts.attendee_mode in ("remove", "remove_append"):
        attendee_lines = []
        for key in list(ev.keys()):
            if key == "ATTENDEE":
                props = ev[key] if isinstance(ev[key], list) else [ev[key]]
                attendee_lines += [_attendee_display(p) for p in props]
                del ev[key]
                break

        organizer_line = ""
        if opts.strip_organizer and "ORGANIZER" in ev:
            organizer_line = _attendee_display(ev["ORGANIZER"])
            del ev["ORGANIZER"]

        if opts.attendee_mode == "remove_append" and (attendee_lines or organizer_line):
            existing_desc = str(ev.get("DESCRIPTION", ""))
            block = "\n--- Original Attendees ---"
            for line in attendee_lines:
                block += f"\n{line}"
            if organizer_line:
                block += f"\nOrganizer: {organizer_line}"
            ev["DESCRIPTION"] = vText(existing_desc + block)

    # VALARM
    if opts.strip_alarms:
        alarms = [c for c in ev.subcomponents if c.name == "VALARM"]
        alarm_lines = [_alarm_display(a) for a in alarms]
        ev.subcomponents = [c for c in ev.subcomponents if c.name != "VALARM"]
        if opts.append_alarms and alarm_lines:
            existing_desc = str(ev.get("DESCRIPTION", ""))
            block = "\n--- Original Reminders ---\n" + "\n".join(alarm_lines)
            ev["DESCRIPTION"] = vText(existing_desc + block)

    # X-properties
    if opts.strip_x_props:
        for key in [k for k in ev.keys() if k.startswith("X-")]:
            del ev[key]

    return ev


# ── merge pipeline ────────────────────────────────────────────────────────────

def merge_files(file_configs: list, opts: ProcessingOptions, progress_callback=None) -> tuple:
    """
    file_configs: [{"path": Path, "prefix": str, "postfix": str}, ...]
    progress_callback(filename: str, total_examined: int) — called per event examined.
    Returns (Calendar, MergeStats). First-file-wins on duplicate (UID, RECURRENCE-ID).
    Raises on file read/parse failure — callers must not swallow.
    """
    user_tz = _get_tz(opts.user_tz_name)
    stats = MergeStats()
    # Key: (uid, recurrence_id_bytes_or_empty) — preserves master + all exception instances
    seen_event_keys: set = set()
    events_out: list = []
    timezones: dict = {}   # tzid -> VTIMEZONE component
    total_examined = 0

    for fc in file_configs:
        path = Path(fc["path"])
        prefix = fc.get("prefix", "")
        postfix = fc.get("postfix", "")
        file_count = 0

        chunks = _read_file_chunks(path)
        calendars = _parse_chunks(chunks, path.name)

        for cal in calendars:
            # Collect VTIMEZONE components; warn on conflicting definitions
            for comp in cal.walk():
                if comp.name == "VTIMEZONE":
                    tzid = str(comp.get("TZID", ""))
                    if not tzid:
                        continue
                    if tzid not in timezones:
                        timezones[tzid] = comp
                    elif comp.to_ical() != timezones[tzid].to_ical():
                        log.warning(
                            "VTIMEZONE conflict: %s defined differently across files "
                            "— using first definition (may affect event times)", tzid
                        )

            for event in cal.walk("VEVENT"):
                if not isinstance(event, Event):
                    continue

                total_examined += 1
                if progress_callback:
                    progress_callback(path.name, total_examined)

                dtstart_prop = event.get("DTSTART")
                if not dtstart_prop:
                    log.warning(
                        "%s: event '%s' has no DTSTART, skipping",
                        path.name, event.get("SUMMARY", "?")
                    )
                    stats.skipped_no_dtstart += 1
                    continue

                # Normalize early — all comparisons use tz-aware datetime
                try:
                    dt_aware = _to_aware(dtstart_prop.dt, user_tz)
                except Exception as e:
                    log.warning(
                        "%s: event '%s' has unparseable DTSTART (%s), skipping",
                        path.name, event.get("SUMMARY", "?"), e
                    )
                    stats.skipped_no_dtstart += 1
                    continue
                ev_date = dt_aware.date()

                if opts.from_date and opts.from_date > ev_date:
                    stats.date_filtered += 1
                    continue
                if opts.to_date and opts.to_date < ev_date:
                    stats.date_filtered += 1
                    continue

                if opts.exclude_cancelled and str(event.get("STATUS", "")).upper() == "CANCELLED":
                    stats.cancelled_excluded += 1
                    continue

                # Dedup: (uid, recurrence_id) — preserves recurring series + exception instances
                uid = str(event.get("UID", ""))
                rec_prop = event.get("RECURRENCE-ID")
                rec_key = rec_prop.to_ical().decode() if rec_prop else ""
                event_key = (uid, rec_key)

                if uid and event_key in seen_event_keys:
                    stats.duplicates_removed += 1
                    continue
                if uid:
                    seen_event_keys.add(event_key)

                events_out.append(transform_event(event, prefix, postfix, opts))
                file_count += 1

        stats.per_file[path.name] = file_count

    out = Calendar()
    out.add("PRODID", "-//ICScrub//EN")
    out.add("VERSION", "2.0")
    out.add("X-WR-CALNAME", opts.calendar_name)
    for tz_comp in timezones.values():
        out.add_component(tz_comp)
    for ev in events_out:
        out.add_component(ev)

    return out, stats


# ── self-check ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import datetime as dt_mod
    from icalendar import vCalAddress, Alarm

    # ── check 1: transform (prefix/postfix, attendee strip+append, alarm strip+append, x-props)
    ev = Event()
    ev.add("SUMMARY", "Test Event")
    ev.add("DTSTART", dt_mod.datetime(2020, 1, 1, 10, 0))
    ev.add("DTEND",   dt_mod.datetime(2020, 1, 1, 11, 0))
    ev.add("UID", "test-uid-001@icsscrub")

    attendee = vCalAddress("mailto:alice@example.com")
    attendee.params["CN"] = "Alice"
    ev.add("ATTENDEE", attendee)

    organizer = vCalAddress("mailto:bob@example.com")
    organizer.params["CN"] = "Bob"
    ev.add("ORGANIZER", organizer)

    alarm = Alarm()
    alarm.add("ACTION", "DISPLAY")
    alarm.add("TRIGGER", dt_mod.timedelta(minutes=-15))
    alarm.add("DESCRIPTION", "Reminder")
    ev.add_component(alarm)
    ev.add("X-CUSTOM-PROP", "vendor-data")

    opts = ProcessingOptions(
        attendee_mode="remove_append",
        strip_organizer=True,
        strip_alarms=True,
        append_alarms=True,
        strip_x_props=True,
    )
    result = transform_event(ev, "[ARCHIVE] ", " [OLD]", opts)

    assert str(result.get("SUMMARY")) == "[ARCHIVE] Test Event [OLD]", "SUMMARY wrong"
    assert result.get("ATTENDEE") is None, "ATTENDEE not stripped"
    assert result.get("ORGANIZER") is None, "ORGANIZER not stripped"
    assert result.get("X-CUSTOM-PROP") is None, "X-prop not stripped"
    assert not any(c.name == "VALARM" for c in result.subcomponents), "VALARM not stripped"
    desc = str(result.get("DESCRIPTION", ""))
    assert "Original Attendees" in desc, "Attendee block missing"
    assert "Alice" in desc, "Alice missing"
    assert "Organizer: Bob" in desc, "Organizer missing"
    assert "Original Reminders" in desc, "Reminder block missing"
    assert "15 minute(s) before" in desc, "Alarm trigger missing"
    print("check 1 passed: transform")

    # ── check 2: timezone-aware date range filter (the bug that caused TypeError)
    tz_utc = timezone.utc
    ev_aware = Event()
    ev_aware.add("SUMMARY", "TZ Event")
    ev_aware.add("DTSTART", dt_mod.datetime(2021, 6, 15, 9, 0, tzinfo=tz_utc))
    ev_aware.add("DTEND",   dt_mod.datetime(2021, 6, 15, 10, 0, tzinfo=tz_utc))
    ev_aware.add("UID", "test-uid-tz@icsscrub")

    cal_tz = Calendar()
    cal_tz.add("PRODID", "-//Test//EN")
    cal_tz.add("VERSION", "2.0")
    cal_tz.add_component(ev_aware)

    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".ics", delete=False) as f:
        f.write(cal_tz.to_ical())
        tmp_path = f.name
    try:
        # from_date after event — should filter it out (no TypeError)
        opts_filter = ProcessingOptions(
            from_date=dt_mod.date(2022, 1, 1),
            user_tz_name="UTC",
        )
        _, stats = merge_files([{"path": tmp_path, "prefix": "", "postfix": ""}], opts_filter)
        assert stats.date_filtered == 1, f"Expected 1 date-filtered, got {stats.date_filtered}"

        # from_date before event — should include it
        opts_include = ProcessingOptions(
            from_date=dt_mod.date(2021, 1, 1),
            user_tz_name="UTC",
        )
        cal_out, stats2 = merge_files([{"path": tmp_path, "prefix": "", "postfix": ""}], opts_include)
        assert stats2.date_filtered == 0, "Event should not be filtered"
        assert sum(stats2.per_file.values()) == 1, "Event should be included"
    finally:
        os.unlink(tmp_path)
    print("check 2 passed: timezone-aware date filter")

    # ── check 3: RECURRENCE-ID — master + exception both preserved (not deduplicated away)
    cal_rec = Calendar()
    cal_rec.add("PRODID", "-//Test//EN")
    cal_rec.add("VERSION", "2.0")

    master = Event()
    master.add("SUMMARY", "Weekly Standup")
    master.add("DTSTART", dt_mod.datetime(2021, 1, 4, 9, 0, tzinfo=tz_utc))
    master.add("DTEND",   dt_mod.datetime(2021, 1, 4, 9, 30, tzinfo=tz_utc))
    master.add("RRULE", {"FREQ": "WEEKLY", "COUNT": 10})
    master.add("UID", "recurring-001@icsscrub")
    cal_rec.add_component(master)

    exception = Event()
    exception.add("SUMMARY", "Weekly Standup (rescheduled)")
    exception.add("DTSTART", dt_mod.datetime(2021, 1, 11, 10, 0, tzinfo=tz_utc))
    exception.add("DTEND",   dt_mod.datetime(2021, 1, 11, 10, 30, tzinfo=tz_utc))
    exception.add("RECURRENCE-ID", dt_mod.datetime(2021, 1, 11, 9, 0, tzinfo=tz_utc))
    exception.add("UID", "recurring-001@icsscrub")
    cal_rec.add_component(exception)

    with tempfile.NamedTemporaryFile(suffix=".ics", delete=False) as f:
        f.write(cal_rec.to_ical())
        tmp_rec = f.name
    try:
        cal_out, stats3 = merge_files(
            [{"path": tmp_rec, "prefix": "", "postfix": ""}],
            ProcessingOptions(user_tz_name="UTC"),
        )
        assert stats3.duplicates_removed == 0, "Master + exception should NOT be deduplicated"
        assert sum(stats3.per_file.values()) == 2, "Both master and exception should be in output"
    finally:
        os.unlink(tmp_rec)
    print("check 3 passed: RECURRENCE-ID dedup preserves recurring series")

    print("\nall self-checks passed")
