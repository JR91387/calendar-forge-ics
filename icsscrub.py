import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from ics_processor import ProcessingOptions, merge_files, _get_tz

# ── timezone list (from bundled tzdata / zoneinfo) ───────────────────────────

try:
    from zoneinfo import available_timezones
    _TZ_LIST = sorted(available_timezones())
except ImportError:
    _TZ_LIST = ["UTC"]

def _system_tz() -> str:
    try:
        import datetime
        tz = datetime.datetime.now().astimezone().tzinfo
        if hasattr(tz, "key"):      # zoneinfo.ZoneInfo on Python 3.9+
            return tz.key
    except Exception:
        pass
    return "UTC"

# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_date(s: str):
    s = s.strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"Unrecognised date: {s!r}  (use YYYY-MM-DD)")


def _read_cal_name(path: Path) -> str:
    try:
        for line in path.read_bytes().splitlines():
            if line.upper().startswith(b"X-WR-CALNAME"):
                return line.split(b":", 1)[-1].strip().decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


_APP_DIR = Path(__file__).parent
_INPUT_DIR = _APP_DIR / "input"
_OUTPUT_DIR = _APP_DIR / "output"
_INPUT_DIR.mkdir(exist_ok=True)
_OUTPUT_DIR.mkdir(exist_ok=True)


# ── app ───────────────────────────────────────────────────────────────────────

class ICScrubApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Calendar Forge")
        self.geometry("860x700")
        self.resizable(True, True)
        self._entries: list[dict] = []
        self._build_ui()
        self._load_input_defaults()

    def _load_input_defaults(self):
        for p in sorted(_INPUT_DIR.glob("*.ics")):
            self._add_path(p)

    # ── build ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self._tab_files(nb)
        self._tab_options(nb)
        self._tab_output(nb)

    # ── Files tab ─────────────────────────────────────────────────────────────

    def _tab_files(self, nb):
        outer = tk.Frame(nb)
        nb.add(outer, text="Files")

        btn_row = tk.Frame(outer)
        btn_row.pack(fill="x", pady=4, padx=4)
        tk.Button(btn_row, text="Add File(s)", command=self._add_files).pack(side="left", padx=2)
        tk.Button(btn_row, text="Add Folder",  command=self._add_folder).pack(side="left", padx=2)
        tk.Button(btn_row, text="Clear All",   command=self._clear_all, fg="red").pack(side="right", padx=4)

        hdr = tk.Frame(outer, bg="#ddd")
        hdr.pack(fill="x", padx=4)
        for text, width in [("↕", 4), ("Filename", 28), ("Calendar Name", 18), ("Add Before Title", 14), ("Add After Title", 14)]:
            tk.Label(hdr, text=text, anchor="w", width=width, bg="#ddd",
                     font=("", 9, "bold")).pack(side="left", padx=2)

        self._list_frame = tk.Frame(outer)
        self._list_frame.pack(fill="both", expand=True, padx=4)

        tk.Label(outer, text="Files higher in the list win on duplicate UIDs. Drop .ics files into the input/ folder to auto-load on launch.",
                 fg="#666", font=("", 8)).pack(anchor="w", padx=6, pady=2)

    # ── Options tab ───────────────────────────────────────────────────────────

    def _tab_options(self, nb):
        outer = tk.Frame(nb)
        nb.add(outer, text="Options")

        # Scrollable inner frame
        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-(e.delta // 120), "units"))

        r = 0

        def section(label, row):
            tk.Label(inner, text=label, font=("", 10, "bold"), anchor="w").grid(
                row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(14, 2))
            ttk.Separator(inner, orient="horizontal").grid(
                row=row + 1, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 6))
            return row + 2

        def opt_row(row, widget, name, desc, indent=20):
            widget.grid(row=row, column=0, sticky="nw", padx=(indent, 4), pady=3)
            tk.Label(inner, text=name, font=("", 9, "bold"), anchor="nw").grid(
                row=row, column=1, sticky="nw", padx=(0, 8), pady=3)
            tk.Label(inner, text=desc, fg="#555", font=("", 8), anchor="nw",
                     wraplength=340, justify="left").grid(
                row=row, column=2, sticky="nw", padx=4, pady=3)
            return row + 1

        inner.columnconfigure(2, weight=1)

        # ── Attendees ─────────────────────────────────────────────────────────
        r = section("Attendees", r)
        self._attendee_mode = tk.StringVar(value="remove_append")
        radio_defs = [
            ("keep",          "Keep",           "Keeps the names and emails of everyone who was invited to each event."),
            ("remove",        "Remove",         "Removes all names and contact info for invited people from your events."),
            ("remove_append", "Remove + Append","Removes the invite list but adds it to each event's description, so you can still see who was invited."),
        ]
        for val, name, desc in radio_defs:
            rb = tk.Radiobutton(inner, variable=self._attendee_mode, value=val)
            r = opt_row(r, rb, name, desc)

        self._strip_organizer = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(inner, variable=self._strip_organizer)
        r = opt_row(r, cb, "Also Strip Organizer",
                    "Also removes the name of the person who created or hosted the meeting. "
                    "Only applies when Remove or Remove + Append is selected above.",
                    indent=36)

        # ── Reminders ─────────────────────────────────────────────────────────
        r = section("Reminders (VALARM)", r)
        self._strip_alarms = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(inner, variable=self._strip_alarms)
        r = opt_row(r, cb, "Strip Reminders",
                    "Removes all old alerts and reminders. "
                    "Highly recommended — if you leave them in, they'll go off in your new calendar right after you import.")

        self._append_alarms = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(inner, variable=self._append_alarms)
        r = opt_row(r, cb, "Append Reminder Info to Description",
                    "Saves a quick note inside the event showing when the original reminder was set, "
                    "so you have a record of it.",
                    indent=36)

        # ── Vendor properties ─────────────────────────────────────────────────
        r = section("Vendor Properties", r)
        self._strip_x = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(inner, variable=self._strip_x)
        r = opt_row(r, cb, "Strip X-Properties",
                    "Removes hidden extra data added by Apple, Google, and Microsoft apps "
                    "that doesn't carry over to other calendars anyway.")

        # ── Cancelled events ──────────────────────────────────────────────────
        r = section("Cancelled Events", r)
        self._excl_cancelled = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(inner, variable=self._excl_cancelled)
        r = opt_row(r, cb, "Exclude Cancelled Events",
                    "Skips events that were cancelled or that you declined — "
                    "so they don't show up in your new calendar.")

        # ── Date range ────────────────────────────────────────────────────────
        r = section("Date Range", r)
        dr_frame = tk.Frame(inner)
        dr_frame.grid(row=r, column=0, columnspan=3, sticky="w", padx=20, pady=4)
        tk.Label(dr_frame, text="From:", font=("", 9, "bold")).grid(row=0, column=0, sticky="w")
        self._from_date = tk.StringVar()
        tk.Entry(dr_frame, textvariable=self._from_date, width=12).grid(row=0, column=1, padx=(4, 12))
        tk.Label(dr_frame, text="To:", font=("", 9, "bold")).grid(row=0, column=2, sticky="w")
        self._to_date = tk.StringVar()
        tk.Entry(dr_frame, textvariable=self._to_date, width=12).grid(row=0, column=3, padx=(4, 12))
        tk.Button(dr_frame, text="Include All",
                  command=lambda: (self._from_date.set(""), self._to_date.set(""))).grid(row=0, column=4)
        tk.Label(dr_frame, text="Enter dates as Year-Month-Day (example: 2023-06-15)  ·  Leave both blank to include all events",
                 fg="#555", font=("", 8)).grid(row=1, column=0, columnspan=5, sticky="w", pady=(2, 0))
        r += 1

        # ── Timezone ──────────────────────────────────────────────────────────
        r = section("Timezone", r)
        tz_frame = tk.Frame(inner)
        tz_frame.grid(row=r, column=0, columnspan=3, sticky="w", padx=20, pady=4)

        tk.Label(tz_frame, text="Timezone:", font=("", 9, "bold")).grid(row=0, column=0, sticky="w")

        self._user_tz = tk.StringVar(value=_system_tz())
        combo = ttk.Combobox(tz_frame, textvariable=self._user_tz, width=32)
        combo["values"] = _TZ_LIST
        combo.grid(row=0, column=1, padx=(6, 0))

        # Live filter as user types
        def _filter_tz(*_):
            typed = self._user_tz.get().lower()
            filtered = [z for z in _TZ_LIST if typed in z.lower()] if typed else _TZ_LIST
            combo["values"] = filtered[:120]

        self._user_tz.trace_add("write", _filter_tz)

        tk.Label(tz_frame,
                 text="Your local time zone is set automatically. "
                      "This helps make sure old events show up at the right time.",
                 fg="#555", font=("", 8), justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    # ── Output tab ────────────────────────────────────────────────────────────

    def _tab_output(self, nb):
        frame = tk.Frame(nb)
        nb.add(frame, text="Output")

        tk.Label(frame, text="Output Calendar Name:").grid(row=0, column=0, sticky="w", padx=16, pady=8)
        self._cal_name = tk.StringVar(value="My Calendar")
        tk.Entry(frame, textvariable=self._cal_name, width=38).grid(row=0, column=1, sticky="w", padx=4)

        tk.Label(frame, text="Output File:").grid(row=1, column=0, sticky="w", padx=16, pady=6)
        out_row = tk.Frame(frame)
        out_row.grid(row=1, column=1, sticky="w", padx=4)
        self._output_path = tk.StringVar(value=str(_OUTPUT_DIR / "merged.ics"))
        tk.Entry(out_row, textvariable=self._output_path, width=38).pack(side="left")
        tk.Button(out_row, text="Browse…", command=self._browse_output).pack(side="left", padx=4)

        btn_row = tk.Frame(frame)
        btn_row.grid(row=2, column=0, columnspan=2, pady=12)
        self._gen_btn = tk.Button(
            btn_row, text="Generate", command=lambda: self._generate(stop_on_error=True),
            bg="#1976D2", fg="white", font=("", 11, "bold"), padx=14, pady=6,
        )
        self._gen_btn.pack(side="left", padx=(0, 6))
        self._gen_btn_all = tk.Button(
            btn_row, text="Generate (collect all errors)",
            command=lambda: self._generate(stop_on_error=False),
            bg="#555", fg="white", font=("", 9), padx=10, pady=6,
        )
        self._gen_btn_all.pack(side="left")

        self._progress_bar = ttk.Progressbar(frame, mode="determinate", length=500)
        self._progress_bar.grid(row=3, column=0, columnspan=2, padx=16, pady=(0, 2), sticky="ew")

        self._progress_label = tk.Label(frame, text="", fg="#444", font=("Courier", 9), anchor="w")
        self._progress_label.grid(row=4, column=0, columnspan=2, padx=16, sticky="w")

        self._result = tk.Text(frame, height=12, state="disabled", wrap="word",
                               font=("Courier", 9), bg="#f5f5f5")
        self._result.grid(row=5, column=0, columnspan=2, padx=16, pady=4, sticky="nsew")
        frame.rowconfigure(5, weight=1)
        frame.columnconfigure(1, weight=1)

    # ── file list ─────────────────────────────────────────────────────────────

    def _render_list(self):
        for widget in self._list_frame.winfo_children():
            widget.destroy()
        for i, entry in enumerate(self._entries):
            row_frame = tk.Frame(self._list_frame, bd=1, relief="groove")
            row_frame.pack(fill="x", pady=1)
            entry["frame"] = row_frame

            up_dn = tk.Frame(row_frame)
            up_dn.grid(row=0, column=0, padx=2)
            tk.Button(up_dn, text="▲", command=lambda i=i: self._move(i, -1),
                      bd=0, font=("", 7), pady=0).pack()
            tk.Button(up_dn, text="▼", command=lambda i=i: self._move(i, 1),
                      bd=0, font=("", 7), pady=0).pack()

            tk.Label(row_frame, text=entry["path"].name, anchor="w", width=28).grid(row=0, column=1, padx=4)
            tk.Label(row_frame, text=entry["cal_name"] or "—", anchor="w",
                     width=18, fg="#555").grid(row=0, column=2)
            tk.Label(row_frame, text="Before:").grid(row=0, column=3, padx=(8, 2))
            tk.Entry(row_frame, textvariable=entry["prefix"], width=12).grid(row=0, column=4)
            tk.Label(row_frame, text="After:").grid(row=0, column=5, padx=(8, 2))
            tk.Entry(row_frame, textvariable=entry["postfix"], width=12).grid(row=0, column=6)
            tk.Button(row_frame, text="✕", fg="red", bd=0,
                      command=lambda i=i: self._remove(i)).grid(row=0, column=7, padx=6)

    def _move(self, i: int, direction: int):
        j = i + direction
        if 0 <= j < len(self._entries):
            self._entries[i], self._entries[j] = self._entries[j], self._entries[i]
            self._render_list()

    def _remove(self, i: int):
        self._entries.pop(i)
        self._render_list()

    def _clear_all(self):
        self._entries.clear()
        self._render_list()

    def _add_path(self, path: Path):
        if any(e["path"] == path for e in self._entries):
            return
        self._entries.append({
            "path": path,
            "cal_name": _read_cal_name(path),
            "prefix": tk.StringVar(),
            "postfix": tk.StringVar(),
            "frame": None,
        })
        self._render_list()

    def _add_files(self):
        for p in filedialog.askopenfilenames(
                initialdir=str(_INPUT_DIR),
                filetypes=[("ICS files", "*.ics"), ("All", "*.*")]):
            self._add_path(Path(p))

    def _add_folder(self):
        folder = filedialog.askdirectory(initialdir=str(_INPUT_DIR))
        if folder:
            for p in sorted(Path(folder).glob("*.ics")):
                self._add_path(p)

    def _browse_output(self):
        p = filedialog.asksaveasfilename(
                initialdir=str(_OUTPUT_DIR),
                defaultextension=".ics",
                filetypes=[("ICS files", "*.ics")])
        if p:
            self._output_path.set(p)

    # ── generate (threaded) ───────────────────────────────────────────────────

    def _generate(self, stop_on_error: bool = True):
        if not self._entries:
            messagebox.showerror("No files", "Add at least one .ics file.")
            return
        out_path = self._output_path.get().strip()
        if not out_path:
            messagebox.showerror("No output", "Set an output file path.")
            return

        try:
            from_date = _parse_date(self._from_date.get())
            to_date   = _parse_date(self._to_date.get())
        except ValueError as e:
            messagebox.showerror("Date error", str(e))
            return

        tz_name = self._user_tz.get().strip() or "UTC"
        try:
            _get_tz(tz_name)
        except ValueError as e:
            messagebox.showerror("Timezone error", str(e))
            return

        opts = ProcessingOptions(
            attendee_mode=self._attendee_mode.get(),
            strip_organizer=self._strip_organizer.get(),
            strip_alarms=self._strip_alarms.get(),
            append_alarms=self._append_alarms.get(),
            strip_x_props=self._strip_x.get(),
            exclude_cancelled=self._excl_cancelled.get(),
            from_date=from_date,
            to_date=to_date,
            calendar_name=self._cal_name.get().strip() or "Calendar Forge Export",
            user_tz_name=tz_name,
        )

        file_configs = [
            {"path": e["path"], "prefix": e["prefix"].get(), "postfix": e["postfix"].get()}
            for e in self._entries
        ]

        state = {"count": 0, "file": "", "done": False, "error": None, "result": None}

        def worker():
            def progress_cb(fname, count):
                state["count"] = count
                state["file"] = fname
            try:
                cal, stats = merge_files(file_configs, opts, progress_callback=progress_cb,
                                         stop_on_error=stop_on_error)
                out = Path(out_path)
                tmp = out.with_suffix(".icsscrub.tmp")
                try:
                    tmp.write_bytes(cal.to_ical())
                    tmp.replace(out)
                except Exception:
                    tmp.unlink(missing_ok=True)
                    raise
                state["result"] = stats
            except Exception as e:
                state["error"] = e
            finally:
                state["done"] = True

        self._gen_btn.config(state="disabled", text="Working…")
        self._gen_btn_all.config(state="disabled")
        self._progress_bar.config(mode="indeterminate")
        self._progress_bar.start(10)
        self._progress_label.config(text="Starting…", fg="#444")
        self._log("")

        threading.Thread(target=worker, daemon=True).start()
        t0 = time.monotonic()

        def poll():
            elapsed = time.monotonic() - t0
            count = state["count"]
            fname = state["file"]
            label = (f"[{fname[:26]}] " if fname else "Reading…  ") + \
                    f"{count:,} events examined | {elapsed:.1f}s elapsed"
            self._progress_label.config(text=label)

            if state["done"]:
                self._progress_bar.stop()
                self._progress_bar.config(mode="determinate")
                self._progress_bar["value"] = 100
                self._gen_btn.config(state="normal", text="Generate")
                self._gen_btn_all.config(state="normal")
                if state["error"]:
                    self._progress_label.config(fg="red")
                    self._log(f"ERROR: {state['error']}", error=True)
                else:
                    self._progress_label.config(fg="#226622")
                    self._show_results(state["result"], out_path, elapsed)
            else:
                self.after(150, poll)

        self.after(150, poll)

    def _show_results(self, stats, out_path: str, elapsed: float):
        total = sum(stats.per_file.values())
        lines = [f"✓  Written: {out_path}  ({elapsed:.1f}s)\n",
                 f"{'File':<34} {'Events':>7}", "-" * 43]
        for name, count in stats.per_file.items():
            if name in stats.file_errors:
                lines.append(f"{name:<34} {'ERROR':>7}")
            else:
                lines.append(f"{name:<34} {count:>7}")
        lines += ["-" * 43, f"{'Total events in output':<34} {total:>7}"]
        if stats.duplicates_removed:
            lines.append(f"{'Duplicates removed':<34} {stats.duplicates_removed:>7}")
        if stats.skipped_no_dtstart:
            lines.append(f"{'Skipped (no DTSTART)':<34} {stats.skipped_no_dtstart:>7}")
        if stats.date_filtered:
            lines.append(f"{'Date-filtered':<34} {stats.date_filtered:>7}")
        if stats.cancelled_excluded:
            lines.append(f"{'Cancelled excluded':<34} {stats.cancelled_excluded:>7}")
        if stats.file_errors:
            lines.append(f"\n{'─'*43}\nFiles with errors ({len(stats.file_errors)} skipped):")
            for fname, msg in stats.file_errors.items():
                lines.append(f"  {fname}: {msg}")
        self._log("\n".join(lines))

    def _log(self, text: str, error: bool = False):
        self._result.config(state="normal")
        self._result.delete("1.0", "end")
        self._result.insert("end", text)
        self._result.config(fg="red" if error else "#111", state="disabled")


if __name__ == "__main__":
    ICScrubApp().mainloop()
