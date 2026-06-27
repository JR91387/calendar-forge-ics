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


class ICScrubApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Calendar Forge")
        self.geometry("800x660")
        self.resizable(True, True)
        self._entries: list[dict] = []
        self._build_ui()

    # ── build ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self._tab_files(nb)
        self._tab_options(nb)
        self._tab_output(nb)

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
        for text, width in [("↕", 4), ("Filename", 28), ("Calendar Name", 18), ("Prefix", 12), ("Postfix", 12)]:
            tk.Label(hdr, text=text, anchor="w", width=width, bg="#ddd",
                     font=("", 9, "bold")).pack(side="left", padx=2)

        self._list_frame = tk.Frame(outer)
        self._list_frame.pack(fill="both", expand=True, padx=4)

        tk.Label(outer, text="Files higher in the list win on duplicate UIDs.",
                 fg="#666", font=("", 8)).pack(anchor="w", padx=6)

    def _tab_options(self, nb):
        frame = tk.Frame(nb)
        nb.add(frame, text="Options")
        r = 0

        def section(text, row):
            tk.Label(frame, text=text, font=("", 10, "bold")).grid(
                row=row, column=0, sticky="w", padx=16, pady=(10, 2))
            return row + 1

        def sep(row):
            ttk.Separator(frame, orient="horizontal").grid(
                row=row, column=0, columnspan=2, sticky="ew", padx=16, pady=4)
            return row + 1

        def opt(text, var, row, indent=32):
            tk.Checkbutton(frame, text=text, variable=var).grid(
                row=row, column=0, sticky="w", padx=indent)
            return row + 1

        # Attendees
        r = section("Attendees", r)
        self._attendee_mode = tk.StringVar(value="remove_append")
        for label, val in [("Keep", "keep"), ("Remove", "remove"),
                            ("Remove + Append to Description", "remove_append")]:
            tk.Radiobutton(frame, text=label, variable=self._attendee_mode,
                           value=val).grid(row=r, column=0, sticky="w", padx=32)
            r += 1
        self._strip_organizer = tk.BooleanVar(value=True)
        r = opt("Also strip Organizer field", self._strip_organizer, r, indent=48)

        r = sep(r)

        # Reminders
        r = section("Reminders (VALARM)", r)
        self._strip_alarms = tk.BooleanVar(value=True)
        r = opt("Strip reminders  ⚠ old alarms will fire on Google Calendar import if kept",
                self._strip_alarms, r)
        self._append_alarms = tk.BooleanVar(value=False)
        r = opt("Append reminder details to description", self._append_alarms, r, indent=48)

        r = sep(r)

        # Other
        r = section("Other", r)
        self._strip_x = tk.BooleanVar(value=True)
        r = opt("Strip vendor X-properties  (X-APPLE-*, X-GOOGLE-*, X-MICROSOFT-*, …)",
                self._strip_x, r)
        self._excl_cancelled = tk.BooleanVar(value=False)
        r = opt("Exclude STATUS:CANCELLED events", self._excl_cancelled, r)

        r = sep(r)

        # Date range
        r = section("Date Range  (YYYY-MM-DD, leave blank = include all)", r)
        dr = tk.Frame(frame)
        dr.grid(row=r, column=0, sticky="w", padx=32)
        tk.Label(dr, text="From:").pack(side="left")
        self._from_date = tk.StringVar()
        tk.Entry(dr, textvariable=self._from_date, width=12).pack(side="left", padx=4)
        tk.Label(dr, text="To:").pack(side="left", padx=(10, 0))
        self._to_date = tk.StringVar()
        tk.Entry(dr, textvariable=self._to_date, width=12).pack(side="left", padx=4)
        tk.Button(dr, text="Include All",
                  command=lambda: (self._from_date.set(""), self._to_date.set(""))).pack(side="left", padx=8)
        r += 1

        r = sep(r)

        # Timezone
        r = section("Timezone", r)
        tz_frame = tk.Frame(frame)
        tz_frame.grid(row=r, column=0, sticky="w", padx=32)
        tk.Label(tz_frame, text="Timezone:").pack(side="left")
        self._user_tz = tk.StringVar(value="UTC")
        tk.Entry(tz_frame, textvariable=self._user_tz, width=24).pack(side="left", padx=4)
        tk.Label(tz_frame, text="(IANA name, e.g. America/Chicago)", fg="#666",
                 font=("", 8)).pack(side="left")
        r += 1
        tk.Label(frame,
                 text="Used to interpret naive datetimes. Named zones require Python 3.9+.",
                 fg="#666", font=("", 8)).grid(row=r, column=0, sticky="w", padx=48)

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

        self._gen_btn = tk.Button(
            frame, text="  Generate  ", command=self._generate,
            bg="#1976D2", fg="white", font=("", 11, "bold"), padx=12, pady=6,
        )
        self._gen_btn.grid(row=2, column=0, columnspan=2, pady=12)

        # Progress bar + label (always visible; indeterminate during processing)
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
            tk.Label(row_frame, text="Prefix:").grid(row=0, column=3, padx=(8, 2))
            tk.Entry(row_frame, textvariable=entry["prefix"], width=12).grid(row=0, column=4)
            tk.Label(row_frame, text="Postfix:").grid(row=0, column=5, padx=(8, 2))
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
        for p in filedialog.askopenfilenames(filetypes=[("ICS files", "*.ics"), ("All", "*.*")]):
            self._add_path(Path(p))

    def _add_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            for p in sorted(Path(folder).glob("*.ics")):
                self._add_path(p)

    def _browse_output(self):
        p = filedialog.asksaveasfilename(defaultextension=".ics",
                                          filetypes=[("ICS files", "*.ics")])
        if p:
            self._output_path.set(p)

    # ── generate (threaded) ───────────────────────────────────────────────────

    def _generate(self):
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
            _get_tz(tz_name)   # validate before handing to thread
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
            calendar_name=self._cal_name.get().strip() or "ICScrub Export",
            user_tz_name=tz_name,
        )

        file_configs = [
            {"path": e["path"], "prefix": e["prefix"].get(), "postfix": e["postfix"].get()}
            for e in self._entries
        ]

        # Shared state between worker thread and GUI poll
        state = {"count": 0, "file": "", "done": False, "error": None, "result": None}

        def worker():
            def progress_cb(fname, count):
                state["count"] = count
                state["file"] = fname
            try:
                cal, stats = merge_files(file_configs, opts, progress_callback=progress_cb)
                # Atomic write — temp file in same dir, then replace
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
            label = (
                f"[{fname[:26]}] " if fname else "Reading…  "
            ) + f"{count:,} events examined | {elapsed:.1f}s elapsed"
            self._progress_label.config(text=label)

            if state["done"]:
                self._progress_bar.stop()
                self._progress_bar.config(mode="determinate")
                self._progress_bar["value"] = 100
                self._gen_btn.config(state="normal", text="  Generate  ")
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
        self._log("\n".join(lines))

    def _log(self, text: str, error: bool = False):
        self._result.config(state="normal")
        self._result.delete("1.0", "end")
        self._result.insert("end", text)
        self._result.config(fg="red" if error else "#111", state="disabled")


if __name__ == "__main__":
    ICScrubApp().mainloop()
