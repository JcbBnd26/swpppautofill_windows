# ============================================================
#  SWPPP AutoFill – Tkinter GUI
#
#  This GUI lets the user:
#    - Choose an output folder
#    - Enter project-level fields (from YAML "fields:")
#    - Check YES / NO / N/A for inspection checklist items
#    - Generate a batch of PDFs + JSON sidecars for each date
#
#  WIRING:
#    - Loads mapping from app/core/config_example.yaml as TemplateMap
#    - Builds ProjectInfo from text fields
#    - Builds checkbox_states dict from GUI toggles
#    - Calls generate_batch(...) from app.core.fill
# ============================================================

from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dateutil.parser import parse as dtparse

from app.core.config_manager import build_project_info, build_run_options, load_mapping
from app.core.dates import weekly_dates
from app.core.fill import generate_batch
from app.core.mesonet import (
    RainDay,
    fetch_rainfall,
    filter_rain_events,
    parse_rainfall_csv_file,
)
from app.core.mesonet_stations import parse_station_code, station_display_list
from app.core.model import TemplateMap
from app.core.rain_fill import generate_rain_batch

# ============================================================
#  Helper: bundled path (for PyInstaller, etc.)
# ============================================================


def _bundle_path(relative: str) -> Path:
    """
    Resolve a path that works both in a normal source checkout and
    in a PyInstaller-style bundled app (where sys._MEIPASS is set).
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative


DEFAULT_DATE_FORMAT = "%m/%d/%Y"
DEFAULT_TEMPLATE = _bundle_path("assets/template.pdf")
DEFAULT_MAPPING = _bundle_path("app/core/config_example.yaml")


# ============================================================
#  ScrollableFrame – used only for the Checklist area
# ============================================================


class ScrollableFrame(ttk.Frame):
    """
    Simple scrollable frame: a Canvas with a vertical scrollbar and
    an inner Frame that actually holds the widgets.
    """

    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview
        )
        self.inner = ttk.Frame(self.canvas)

        # When inner changes size, update scrollregion
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )

        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")

    def _on_mousewheel(self, event):
        # Windows typically uses event.delta in multiples of 120
        delta = -1 * (event.delta // 120)
        self.canvas.yview_scroll(delta, "units")


# ============================================================
#  Main Application Class
# ============================================================


class App(tk.Tk):
    """
    Main Tkinter application window.

    Layout (two-column):
        - Header title (spanning full width)
        - Left column: Output folder, dates, Project Fields, Rain Days, buttons
        - Right column: Checklist (scrollable, uses full height)
    """

    def __init__(self):
        super().__init__()

        self.title("SWPPP AutoFill")
        self.minsize(1100, 700)

        # --- STATE VARIABLES ---
        self.output_dir = tk.StringVar()
        self.start_date = tk.StringVar()
        self.end_date = tk.StringVar()

        # Project text entries: model_key -> tk.StringVar
        self.project_entries: dict[str, tk.StringVar] = {}

        # Checklists: group_key -> {label_text: tk.StringVar}
        self.checkbox_vars: dict[str, dict[str, tk.StringVar]] = {}

        # Mapping loaded from YAML
        self._current_mapping: TemplateMap | None = None

        # --- RAIN DAYS STATE ---
        self.rain_start_date = tk.StringVar()
        self.rain_end_date = tk.StringVar()
        self.rain_station = tk.StringVar()
        self.rain_status = tk.StringVar(value="")
        self._rain_days: list[RainDay] = []

        # Build UI and load YAML
        self._build_ui()
        self._configure_grid()
        self._load_fields_from_yaml()

        # Size window to fit content (capped to screen)
        self._fit_to_content()

        # Warn if template is missing
        if not DEFAULT_TEMPLATE.exists():
            messagebox.showerror(
                "Template Missing",
                f"Expected template not found:\n{DEFAULT_TEMPLATE}\n\n"
                "Place your template there or update DEFAULT_TEMPLATE in ui_gui.main.",
            )

    # --------------------------------------------------------
    #  UI Construction
    # --------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}
        root = ttk.Frame(self)
        root.grid(row=0, column=0, sticky="nsew")

        # ---- Header (spans full width) ----
        title = ttk.Label(
            root,
            text="OKLAHOMA DOT CLEAN WATER INSPECTION FORM",
            anchor="center",
            font=("Segoe UI", 14, "bold"),
        )
        title.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(12, 4))
        ttk.Separator(root).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10)
        )

        # ====================================================
        #  LEFT COLUMN — settings, project fields, rain, btns
        # ====================================================
        left = ttk.Frame(root)
        left.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left.columnconfigure(1, weight=1)

        # -- Output + Dates --
        lr = 0
        ttk.Label(left, text="Output Folder:").grid(row=lr, column=0, sticky="e", **pad)
        out_row = ttk.Frame(left)
        out_row.grid(row=lr, column=1, sticky="ew", **pad)
        out_row.columnconfigure(0, weight=1)
        ttk.Entry(out_row, textvariable=self.output_dir).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(out_row, text="Browse", command=self._pick_output).grid(
            row=0, column=1, padx=(6, 0)
        )
        lr += 1

        ttk.Label(left, text="Start Date (MM/DD/YYYY):").grid(
            row=lr, column=0, sticky="e", **pad
        )
        ttk.Entry(left, textvariable=self.start_date, width=18).grid(
            row=lr, column=1, sticky="w", **pad
        )
        lr += 1

        ttk.Label(left, text="End Date (MM/DD/YYYY):").grid(
            row=lr, column=0, sticky="e", **pad
        )
        ttk.Entry(left, textvariable=self.end_date, width=18).grid(
            row=lr, column=1, sticky="w", **pad
        )
        lr += 1

        ttk.Separator(left).grid(
            row=lr, column=0, columnspan=2, sticky="ew", pady=(4, 8)
        )
        lr += 1

        # -- Project Fields --
        ttk.Label(left, text="Project Fields", font=("Segoe UI", 10, "bold")).grid(
            row=lr, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4)
        )
        lr += 1

        self.fields_inner = ttk.Frame(left, borderwidth=1, relief="groove")
        self.fields_inner.grid(
            row=lr, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 10)
        )
        self.fields_inner.columnconfigure(1, weight=1)
        lr += 1

        ttk.Separator(left).grid(
            row=lr, column=0, columnspan=2, sticky="ew", pady=(8, 8)
        )
        lr += 1

        # -- Rain Days --
        rain_frame = ttk.LabelFrame(left, text="Rain Days", padding=10)
        rain_frame.grid(
            row=lr, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8)
        )
        rain_frame.columnconfigure(1, weight=1)
        lr += 1

        ttk.Label(rain_frame, text="Start Date (MM/DD/YYYY):").grid(
            row=0, column=0, sticky="e", padx=6, pady=3
        )
        ttk.Entry(rain_frame, textvariable=self.rain_start_date, width=18).grid(
            row=0, column=1, sticky="w", padx=6, pady=3
        )

        ttk.Label(rain_frame, text="End Date (MM/DD/YYYY):").grid(
            row=1, column=0, sticky="e", padx=6, pady=3
        )
        ttk.Entry(rain_frame, textvariable=self.rain_end_date, width=18).grid(
            row=1, column=1, sticky="w", padx=6, pady=3
        )

        ttk.Label(rain_frame, text="Station:").grid(
            row=2, column=0, sticky="e", padx=6, pady=3
        )
        self.rain_station_combo = ttk.Combobox(
            rain_frame,
            textvariable=self.rain_station,
            values=station_display_list(),
            state="readonly",
            width=36,
        )
        self.rain_station_combo.grid(row=2, column=1, sticky="w", padx=6, pady=3)

        rain_btn_frame = ttk.Frame(rain_frame)
        rain_btn_frame.grid(row=3, column=0, columnspan=2, pady=(6, 0))

        ttk.Button(
            rain_btn_frame, text="Fetch Rain Data", command=self._on_fetch_rain
        ).grid(row=0, column=0, padx=6)
        ttk.Button(
            rain_btn_frame, text="Browse CSV", command=self._on_browse_rain_csv
        ).grid(row=0, column=1, padx=6)
        self._rain_generate_btn = ttk.Button(
            rain_btn_frame,
            text="Generate Rain PDFs",
            command=self._on_generate_rain,
            state="disabled",
        )
        self._rain_generate_btn.grid(row=0, column=2, padx=6)

        self._rain_progress = ttk.Progressbar(
            rain_frame, orient="horizontal", mode="determinate", length=300
        )
        self._rain_progress.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 0)
        )
        self._rain_progress.grid_remove()  # hidden until fetch starts

        ttk.Label(
            rain_frame,
            textvariable=self.rain_status,
            foreground="#336699",
            wraplength=400,
            anchor="w",
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 0))

        # -- Generate / Quit Buttons --
        btns = ttk.Frame(left)
        btns.grid(row=lr, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="Generate", command=self._on_generate).grid(
            row=0, column=0, padx=6
        )
        ttk.Button(btns, text="Quit", command=self.destroy).grid(
            row=0, column=1, padx=6
        )

        # ====================================================
        #  RIGHT COLUMN — Checklist (scrollable)
        # ====================================================
        right = ttk.Frame(root)
        right.grid(row=2, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        ttk.Label(right, text="Checklist", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(0, 4)
        )
        self.checks_area = ScrollableFrame(right)
        self.checks_area.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 10))

    def _configure_grid(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root = self.children[list(self.children.keys())[0]]
        root.columnconfigure(0, weight=0, minsize=350)  # left column (fixed width)
        root.columnconfigure(
            1, weight=1, minsize=500
        )  # right column (checklist, stretches)
        root.rowconfigure(2, weight=1)  # content row stretches

    def _fit_to_content(self):
        """Size the window to fit all content, capped to 95% of screen."""
        self.update_idletasks()
        req_w = self.winfo_reqwidth()
        req_h = self.winfo_reqheight()
        scr_w = self.winfo_screenwidth()
        scr_h = self.winfo_screenheight()
        w = min(max(req_w + 40, 1500), int(scr_w * 0.95))
        h = min(req_h + 40, int(scr_h * 0.90))
        x = (scr_w - w) // 2
        y = max((scr_h - h) // 2 - 30, 0)
        self.geometry(f"{w}x{h}+{x}+{y}")

    # --------------------------------------------------------
    #  Output folder selection
    # --------------------------------------------------------
    def _pick_output(self):
        base = filedialog.askdirectory(title="Choose output folder")
        if base:
            self.output_dir.set(str(Path(base)))

    # --------------------------------------------------------
    #  Rain Days handlers
    # --------------------------------------------------------
    def _on_fetch_rain(self):
        """Fetch rainfall data from Mesonet in a background thread."""
        try:
            station_display = self.rain_station.get()
            if not station_display:
                raise ValueError("Select a Mesonet station.")
            station_code = parse_station_code(station_display)

            start_iso = self._parse_user_date_mdy(self.rain_start_date.get())
            end_iso = self._parse_user_date_mdy(self.rain_end_date.get())

            from datetime import date as date_type

            start_dt = date_type.fromisoformat(start_iso)
            end_dt = date_type.fromisoformat(end_iso)
        except ValueError as exc:
            self.rain_status.set(f"Error: {exc}")
            return

        self.rain_status.set("Fetching rainfall data from Mesonet...")
        self._rain_generate_btn.config(state="disabled")
        self._rain_progress["value"] = 0
        self._rain_progress.grid()  # show progress bar

        def _on_progress(day_num: int, total_days: int):
            self.after(
                0, lambda d=day_num, t=total_days: self._rain_update_progress(d, t)
            )

        def _worker():
            try:
                all_days = fetch_rainfall(
                    station_code, start_dt, end_dt, progress=_on_progress
                )
                events = filter_rain_events(all_days)
                self.after(0, lambda: self._rain_fetch_done(all_days, events))
            except Exception as exc:
                self.after(0, lambda e=exc: self._rain_fetch_error(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _rain_update_progress(self, day_num: int, total_days: int):
        pct = int(day_num / total_days * 100) if total_days else 100
        self._rain_progress["value"] = pct
        self.rain_status.set(f"Fetching day {day_num} of {total_days}  ({pct}%)")

    def _rain_fetch_done(self, all_days: list[RainDay], events: list[RainDay]):
        self._rain_progress.grid_remove()  # hide progress bar
        self._rain_days = events
        self.rain_status.set(
            f"Found {len(events)} rain day(s) exceeding 0.5 inches "
            f"out of {len(all_days)} total day(s)."
        )
        if events:
            self._rain_generate_btn.config(state="normal")
        else:
            self._rain_generate_btn.config(state="disabled")

    def _rain_fetch_error(self, exc: Exception):
        self._rain_progress.grid_remove()  # hide progress bar
        self.rain_status.set(
            f"Fetch failed: {exc}\nUse 'Browse CSV' to load data manually."
        )
        self._rain_generate_btn.config(state="disabled")

    def _on_browse_rain_csv(self):
        """Load rainfall CSV from a local file."""
        path = filedialog.askopenfilename(
            title="Select Mesonet rainfall CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            all_days = parse_rainfall_csv_file(path)
            events = filter_rain_events(all_days)
            self._rain_days = events
            self.rain_status.set(
                f"Loaded {len(all_days)} day(s) from file. "
                f"{len(events)} rain day(s) exceed 0.5 inches."
            )
            if events:
                self._rain_generate_btn.config(state="normal")
            else:
                self._rain_generate_btn.config(state="disabled")
        except Exception as exc:
            self.rain_status.set(f"CSV load error: {exc}")
            self._rain_generate_btn.config(state="disabled")

    def _on_generate_rain(self):
        """Generate rain event PDFs using the loaded rain days."""
        if not self._rain_days:
            self.rain_status.set("No rain days loaded. Fetch or browse CSV first.")
            return

        try:
            template = DEFAULT_TEMPLATE
            raw_output_dir = self.output_dir.get().strip()
            if not raw_output_dir:
                raise ValueError("Choose an output folder before generating files.")
            outdir = Path(raw_output_dir)
            outdir.mkdir(parents=True, exist_ok=True)

            if not template.exists():
                messagebox.showerror(
                    "Template Missing", f"Template PDF not found:\n{template}"
                )
                return

            mapping = self._current_mapping
            if mapping is None:
                mapping = load_mapping(DEFAULT_MAPPING)
                self._current_mapping = mapping

            proj_dict = {k: v.get().strip() for k, v in self.project_entries.items()}
            project = build_project_info(proj_dict)
            check_states = self._collect_checkbox_states()

            original_type = (
                self.project_entries.get("inspection_type", tk.StringVar())
                .get()
                .strip()
            )

            # Use a dummy date range for RunOptions (rain days have their own dates)
            options = build_run_options(
                output_dir=str(outdir),
                start_date="2000-01-01",
                end_date="2000-01-01",
                date_format=DEFAULT_DATE_FORMAT,
                make_zip=True,
            )

            written = generate_rain_batch(
                template_path=str(template),
                project=project,
                options=options,
                rain_days=self._rain_days,
                mapping=mapping,
                checkbox_states=check_states,
                original_inspection_type=original_type,
            )

            pdf_count = sum(1 for p in written if p.lower().endswith(".pdf"))
            self.rain_status.set(f"Generated {pdf_count} rain event PDF(s).")
            messagebox.showinfo(
                "Rain Event Generation Complete",
                self._build_success_message(written),
            )

        except ValueError as exc:
            messagebox.showerror("Input Error", str(exc))
        except Exception as exc:
            messagebox.showerror(
                "Generation Failed",
                f"Rain event PDFs could not be generated.\n\n{exc}",
            )

    # --------------------------------------------------------
    #  YAML loading and form building
    # --------------------------------------------------------
    def _load_fields_from_yaml(self):
        # Clear old widgets
        for c in self.fields_inner.winfo_children():
            c.destroy()
        for c in self.checks_area.inner.winfo_children():
            c.destroy()
        self.project_entries.clear()
        self.checkbox_vars.clear()

        # Load mapping
        try:
            mapping = load_mapping(DEFAULT_MAPPING)
            self._current_mapping = mapping
        except Exception as e:
            messagebox.showerror(
                "Configuration Error",
                f"Failed to read the mapping file:\n{DEFAULT_MAPPING}\n\n{e}",
            )
            return

        # --- Project Fields ---
        r = 0
        for model_key, field_mapping in mapping.fields.items():
            label_text = field_mapping.label

            var = tk.StringVar()
            self.project_entries[model_key] = var

            ttk.Label(self.fields_inner, text=label_text).grid(
                row=r, column=0, sticky="e", padx=8, pady=4
            )
            ttk.Entry(self.fields_inner, textvariable=var, width=60).grid(
                row=r, column=1, sticky="ew", padx=8, pady=4
            )
            r += 1

        # --- Checklist (toggle buttons) ---
        cr = 0
        mapping_checks = getattr(mapping, "checkboxes", None)
        if not mapping_checks:
            ttk.Label(
                self.checks_area.inner, text="No checkboxes defined in mapping."
            ).grid(row=0, column=0, sticky="w", padx=8, pady=4)
            return

        for group_key, group in mapping_checks.items():
            grp_frame = ttk.LabelFrame(
                self.checks_area.inner, text=group_key.replace("_", " ").title()
            )
            grp_frame.grid(row=cr, column=0, sticky="ew", padx=8, pady=6)
            grp_frame.columnconfigure(0, weight=1)
            row_vars: dict[str, tk.StringVar] = {}

            # Header row: blank, YES, NO, N/A
            headers = ["", "YES", "NO", "N/A"]
            for j, head in enumerate(headers):
                ttk.Label(grp_frame, text=head, font=("Segoe UI", 9, "bold")).grid(
                    row=0, column=j, padx=6, pady=(0, 4)
                )

            pdf_list = getattr(group, "pdf_fields", []) or []
            if not isinstance(pdf_list, list):
                pdf_list = []

            for i, item in enumerate(pdf_list, start=1):
                text = item.text
                allow_na = item.allow_na

                ttk.Label(grp_frame, text=text, anchor="w").grid(
                    row=i, column=0, sticky="w", padx=6, pady=2
                )
                var = tk.StringVar(value="")
                row_vars[text] = var

                # YES / NO always; N/A only if allowed
                choices = ["YES", "NO"]
                if allow_na:
                    choices.append("N/A")

                # Create toggle-style buttons
                for j, choice in enumerate(choices, start=1):
                    btn = tk.Button(
                        grp_frame,
                        text=choice,
                        width=6,
                        relief="raised",
                        bg="#f0f0f0",
                    )

                    def make_toggle(v=var, c=choice, b=btn, row=i):
                        def _cmd():
                            current = v.get()
                            if current == c:
                                # Turn off
                                v.set("")
                                b.config(relief="raised", bg="#f0f0f0")
                            else:
                                # Turn on this choice, turn off siblings in same row
                                v.set(c)
                                for sib in grp_frame.grid_slaves(row=row):
                                    if isinstance(sib, tk.Button) and sib is not b:
                                        sib.config(relief="raised", bg="#f0f0f0")
                                b.config(relief="sunken", bg="#cde9ff")

                        return _cmd

                    btn.config(command=make_toggle())
                    btn.grid(row=i, column=j, padx=4, pady=2)

            self.checkbox_vars[group_key] = row_vars
            cr += 1

    # --------------------------------------------------------
    #  Date parsing
    # --------------------------------------------------------
    def _parse_user_date_mdy(self, s: str) -> str:
        """
        Parse a user-entered date (MM/DD/YYYY or similar) and return ISO (YYYY-MM-DD).
        """
        s = (s or "").strip()
        if not s:
            raise ValueError("Date is required. Use MM/DD/YYYY.")
        try:
            dt = dtparse(s, dayfirst=False, yearfirst=False)
        except Exception as exc:
            raise ValueError(f"Invalid date '{s}'. Use MM/DD/YYYY.") from exc
        return dt.strftime("%Y-%m-%d")

    def _collect_checkbox_states(self) -> dict[str, dict[str, str]]:
        return {
            group_key: {item: var.get() for item, var in group.items()}
            for group_key, group in self.checkbox_vars.items()
        }

    def _build_success_message(self, written: list[str]) -> str:
        pdf_count = sum(1 for path in written if path.lower().endswith(".pdf"))
        zip_count = sum(1 for path in written if path.lower().endswith(".zip"))
        lines = [f"Created {pdf_count} PDF file(s)."]
        if zip_count:
            lines.append(f"Created {zip_count} ZIP bundle.")
        lines.append("")
        lines.extend(f"- {path}" for path in written)
        return "\n".join(lines)

    # --------------------------------------------------------
    #  Generate button handler
    # --------------------------------------------------------
    def _on_generate(self):
        try:
            # Template / output dir
            template = DEFAULT_TEMPLATE
            raw_output_dir = self.output_dir.get().strip()
            if not raw_output_dir:
                raise ValueError("Choose an output folder before generating files.")
            outdir = Path(raw_output_dir)
            outdir.mkdir(parents=True, exist_ok=True)

            if not template.exists():
                messagebox.showerror(
                    "Template Missing", f"Template PDF not found:\n{template}"
                )
                return

            # Parse dates
            iso_start = self._parse_user_date_mdy(self.start_date.get())
            iso_end = self._parse_user_date_mdy(self.end_date.get())

            # Ensure mapping loaded
            mapping = self._current_mapping
            if mapping is None:
                mapping = load_mapping(DEFAULT_MAPPING)
                self._current_mapping = mapping

            # Project fields -> ProjectInfo
            proj_dict = {k: v.get().strip() for k, v in self.project_entries.items()}
            project = build_project_info(proj_dict)

            # Checklist states
            check_states = self._collect_checkbox_states()

            # RunOptions
            options = build_run_options(
                output_dir=str(outdir),
                start_date=iso_start,
                end_date=iso_end,
                date_format=DEFAULT_DATE_FORMAT,
                make_zip=True,
            )

            # Build list of dates for weekly inspections
            dates = list(weekly_dates(options.start_date, options.end_date))

            # Call core generator
            written = generate_batch(
                template_path=str(template),
                project=project,
                options=options,
                dates=dates,
                mapping=mapping,
                checkbox_states=check_states,
            )
            messagebox.showinfo(
                "Generation Complete", self._build_success_message(written)
            )

        except ValueError as exc:
            messagebox.showerror("Input Error", str(exc))
        except Exception as exc:
            messagebox.showerror(
                "Generation Failed",
                f"The PDF batch could not be generated.\n\n{exc}",
            )


# ============================================================
#  Entry Point
# ============================================================


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
