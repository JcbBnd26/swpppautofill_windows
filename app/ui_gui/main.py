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

import calendar as cal_mod
import random
import sys
import threading
import tkinter as tk
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dateutil.parser import parse as dtparse
from tkcalendar import Calendar

from app.core.config_manager import build_project_info, build_run_options, load_mapping
from app.core.dates import weekly_dates
from app.core.fill import generate_batch
from app.core.mesonet import (
    FetchResult,
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

_LOREM_WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat duis aute irure dolor in reprehenderit voluptate velit "
    "esse cillum fugiat nulla pariatur excepteur sint occaecat cupidatat non "
    "proident sunt in culpa qui officia deserunt mollit anim id est laborum"
).split()


def _random_lorem(word_count: int = 4) -> str:
    return " ".join(random.choice(_LOREM_WORDS) for _ in range(word_count)).title()


# ============================================================
#  DatePickerEntry – auto-formatting date field + calendar popup
# ============================================================


class DatePickerEntry(ttk.Frame):
    """Composite widget: an auto-formatting MM/DD/YYYY entry with a
    calendar popup button."""

    def __init__(self, parent, textvariable: tk.StringVar, **kwargs):
        super().__init__(parent, **kwargs)
        self._var = textvariable
        self._suppress_trace = False
        self._popup: tk.Toplevel | None = None
        self._dismiss_binding: str | None = None

        self._entry = ttk.Entry(self, textvariable=self._var, width=14)
        self._entry.grid(row=0, column=0, sticky="w")

        self._btn = ttk.Button(
            self, text="\U0001f4c5", width=3, command=self._open_calendar
        )
        self._btn.grid(row=0, column=1, padx=(2, 0))

        self._var.trace_add("write", self._on_text_change)

    # --- Auto-format: insert slashes as user types digits ---
    def _on_text_change(self, *_args):
        if self._suppress_trace:
            return
        self._suppress_trace = True
        try:
            raw = self._var.get()
            digits = "".join(c for c in raw if c.isdigit())
            # Build formatted string from digits only
            parts = []
            if len(digits) >= 2:
                parts.append(digits[:2])
                if len(digits) >= 4:
                    parts.append(digits[2:4])
                    if len(digits) >= 5:
                        parts.append(digits[4:8])
                else:
                    parts.append(digits[2:])
            else:
                parts.append(digits)
            formatted = "/".join(parts)
            if formatted != raw:
                self._var.set(formatted)
                self._entry.icursor(len(formatted))
        finally:
            self._suppress_trace = False

    # --- Calendar popup ---
    def _open_calendar(self):
        if self._popup is not None:
            self._close_popup()
            return

        # Determine initial date for the calendar
        today = date_cls.today()
        try:
            dt = dtparse(self._var.get(), dayfirst=False, yearfirst=False)
            init_year, init_month, init_day = dt.year, dt.month, dt.day
        except Exception:
            init_year, init_month, init_day = today.year, today.month, today.day

        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.wm_attributes("-topmost", True)

        # Position below the entry
        x = self._entry.winfo_rootx()
        y = self._entry.winfo_rooty() + self._entry.winfo_height() + 2
        popup.geometry(f"+{x}+{y}")

        cal = Calendar(
            popup,
            selectmode="day",
            year=init_year,
            month=init_month,
            day=init_day,
            date_pattern="mm/dd/yyyy",
        )
        cal.pack(padx=4, pady=4)

        def _on_select(_event=None):
            self._suppress_trace = True
            try:
                self._var.set(cal.get_date())
            finally:
                self._suppress_trace = False
            self._close_popup()

        cal.bind("<<CalendarSelected>>", _on_select)
        popup.bind("<Escape>", lambda _e: self._close_popup())

        # Dismiss on any click in the main window
        root = self.winfo_toplevel()
        self._dismiss_binding = root.bind(
            "<Button-1>", lambda _e: self._close_popup(), add="+"
        )

        self._popup = popup

    def _close_popup(self):
        if self._popup is not None:
            try:
                root = self.winfo_toplevel()
                if self._dismiss_binding:
                    root.unbind("<Button-1>", self._dismiss_binding)
                    self._dismiss_binding = None
            except Exception:
                pass
            self._popup.destroy()
            self._popup = None


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
        self.start_date = tk.StringVar()
        self.end_date = tk.StringVar()

        # Project text entries: model_key -> tk.StringVar
        self.project_entries: dict[str, tk.StringVar] = {}

        # Checklists: group_key -> {label_text: tk.StringVar}
        self.checkbox_vars: dict[str, dict[str, tk.StringVar]] = {}

        # Notes: group_key -> tk.Text widget
        self.notes_vars: dict[str, tk.Text] = {}

        # Mapping loaded from YAML
        self._current_mapping: TemplateMap | None = None

        # --- CUSTOM DATE OVERRIDE ---
        self._custom_dates_enabled = tk.BooleanVar(value=False)
        self._rain_enabled = tk.BooleanVar(value=True)

        # --- QUICK MONTH STATE ---
        self._quick_year = tk.StringVar(value=str(date_cls.today().year))
        self._month_vars: list[tk.BooleanVar] = []
        self._month_checkbuttons: list[ttk.Checkbutton] = []
        for i in range(12):
            var = tk.BooleanVar(value=(i == date_cls.today().month - 1))
            self._month_vars.append(var)

        # --- RAIN DAYS STATE ---
        self.rain_station = tk.StringVar()
        self.rain_status = tk.StringVar(value="")
        self._rain_days_detail = tk.StringVar(value="")
        self._rain_days: list[RainDay] = []
        self._rain_data_ready = False

        # Build UI and load YAML
        self._build_ui()
        self._bind_generator_input_traces()
        self._update_generate_button_state()
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

        lr = 0

        # -- Project Fields --
        proj_header = ttk.Frame(left)
        proj_header.grid(
            row=lr, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 4)
        )
        ttk.Label(proj_header, text="Project Info", font=("Segoe UI", 10, "bold")).pack(
            side="left"
        )
        tk.Button(
            proj_header,
            text="T",
            width=2,
            height=1,
            font=("Segoe UI", 7, "bold"),
            relief="raised",
            bg="#f0f0f0",
            command=self._fill_test_fields,
        ).pack(side="right", padx=(2, 0))
        lr += 1

        self.fields_inner = ttk.Frame(left, borderwidth=1, relief="groove")
        self.fields_inner.grid(
            row=lr, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 10)
        )
        self.fields_inner.columnconfigure(1, weight=1)
        lr += 1

        # ── Generator Settings LabelFrame ──
        settings_frame = ttk.LabelFrame(left, text="Generator Settings", padding=10)
        settings_frame.grid(
            row=lr, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 8)
        )
        settings_frame.columnconfigure(1, weight=1)
        lr += 1
        sr = 0  # row counter inside settings_frame

        # Year selector
        ttk.Label(settings_frame, text="Year:").grid(
            row=sr, column=0, sticky="e", padx=6, pady=3
        )
        current_year = date_cls.today().year
        years = [str(y) for y in range(current_year - 5, current_year + 1)]
        self._year_combo = ttk.Combobox(
            settings_frame,
            textvariable=self._quick_year,
            values=years,
            state="readonly",
            width=6,
        )
        self._year_combo.grid(row=sr, column=1, sticky="w", padx=6, pady=3)
        sr += 1

        # Month checkboxes
        ttk.Label(settings_frame, text="Month(s):").grid(
            row=sr, column=0, sticky="ne", padx=6, pady=3
        )
        qm_frame = ttk.Frame(settings_frame)
        qm_frame.grid(row=sr, column=1, sticky="w", padx=6, pady=3)

        months = [cal_mod.month_abbr[m] for m in range(1, 13)]
        for i, m in enumerate(months):
            r, c = divmod(i, 6)
            checkbutton = ttk.Checkbutton(
                qm_frame, text=m, variable=self._month_vars[i]
            )
            checkbutton.grid(row=r, column=c, sticky="w", padx=2)
            self._month_checkbuttons.append(checkbutton)
        sr += 1

        # Generate + custom date toggle
        ctrl_frame = ttk.Frame(settings_frame)
        ctrl_frame.grid(
            row=sr, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 4)
        )
        ctrl_frame.columnconfigure(0, weight=1)
        ttk.Checkbutton(
            ctrl_frame,
            text="Custom date range",
            variable=self._custom_dates_enabled,
            command=self._toggle_custom_dates,
        ).grid(row=0, column=0, sticky="e")
        sr += 1

        # Custom date fields (hidden by default)
        self._custom_dates_frame = ttk.Frame(settings_frame)
        self._custom_dates_frame.grid(
            row=sr, column=0, columnspan=2, sticky="w", padx=6
        )
        ttk.Label(self._custom_dates_frame, text="Start:").grid(
            row=0, column=0, sticky="e", padx=(0, 4)
        )
        self._start_picker = DatePickerEntry(
            self._custom_dates_frame, textvariable=self.start_date
        )
        self._start_picker.grid(row=0, column=1, sticky="w")
        ttk.Label(self._custom_dates_frame, text="End:").grid(
            row=0, column=2, sticky="e", padx=(12, 4)
        )
        self._end_picker = DatePickerEntry(
            self._custom_dates_frame, textvariable=self.end_date
        )
        self._end_picker.grid(row=0, column=3, sticky="w")
        self._custom_dates_frame.grid_remove()
        sr += 1

        ttk.Checkbutton(
            settings_frame,
            text="Rain Days",
            variable=self._rain_enabled,
            command=self._toggle_rain_section,
        ).grid(row=sr, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 2))
        sr += 1

        self._rain_section_frame = ttk.LabelFrame(
            settings_frame, text="Rain Days", padding=10
        )
        self._rain_section_frame.grid(
            row=sr, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 0)
        )
        self._rain_section_frame.columnconfigure(0, weight=1)
        rr = 0

        # Rain buttons
        rain_btn_frame = ttk.Frame(self._rain_section_frame)
        rain_btn_frame.grid(row=rr, column=0, sticky="w")
        ttk.Button(
            rain_btn_frame, text="Fetch Rain Data", command=self._on_fetch_rain
        ).grid(row=0, column=0, padx=(0, 6))
        ttk.Label(rain_btn_frame, text="or").grid(row=0, column=1, padx=(0, 6))
        ttk.Button(
            rain_btn_frame, text="Load CSV", command=self._on_browse_rain_csv
        ).grid(row=0, column=2)
        rr += 1

        # Station selector
        ttk.Label(self._rain_section_frame, text="Station:").grid(
            row=rr, column=0, sticky="w", pady=(3, 0)
        )
        rr += 1
        self.rain_station_combo = ttk.Combobox(
            self._rain_section_frame,
            textvariable=self.rain_station,
            values=station_display_list(),
            state="readonly",
            width=36,
        )
        self.rain_station_combo.grid(row=rr, column=0, sticky="w", pady=(0, 3))
        rr += 1

        # Rain days detail list (hidden until data available)
        self._rain_days_detail_label = ttk.Label(
            self._rain_section_frame,
            textvariable=self._rain_days_detail,
            foreground="#336699",
            wraplength=400,
            anchor="w",
        )
        self._rain_days_detail_label.grid(row=rr, column=0, sticky="w", pady=(2, 0))
        self._rain_days_detail_label.grid_remove()
        rr += 1

        # Progress bar (hidden until fetch)
        self._rain_progress = ttk.Progressbar(
            self._rain_section_frame,
            orient="horizontal",
            mode="determinate",
            length=300,
        )
        self._rain_progress.grid(row=rr, column=0, sticky="ew", pady=(6, 0))
        self._rain_progress.grid_remove()
        rr += 1

        # Status label
        ttk.Label(
            self._rain_section_frame,
            textvariable=self.rain_status,
            foreground="#336699",
            wraplength=400,
            anchor="w",
        ).grid(row=rr, column=0, sticky="w", pady=(4, 0))
        sr += 1

        self._generate_btn = ttk.Button(
            settings_frame,
            text="Generate",
            command=self._on_generate_months,
            width=18,
        )
        self._generate_btn.grid(row=sr, column=0, columnspan=2, pady=(8, 0))

        ttk.Separator(left).grid(
            row=lr, column=0, columnspan=2, sticky="ew", pady=(8, 8)
        )
        lr += 1

        # ====================================================
        #  RIGHT COLUMN — Checklist (scrollable)
        # ====================================================
        right = ttk.Frame(root)
        right.grid(row=2, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        chk_header = ttk.Frame(right)
        chk_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(0, 4))
        ttk.Label(chk_header, text="Checklist", font=("Segoe UI", 10, "bold")).pack(
            side="left"
        )
        tk.Button(
            chk_header,
            text="N",
            width=2,
            height=1,
            font=("Segoe UI", 7, "bold"),
            relief="raised",
            bg="#f0f0f0",
            command=self._fill_test_notes,
        ).pack(side="right", padx=(2, 0))
        tk.Button(
            chk_header,
            text="T",
            width=2,
            height=1,
            font=("Segoe UI", 7, "bold"),
            relief="raised",
            bg="#f0f0f0",
            command=self._fill_test_checklist,
        ).pack(side="right", padx=(2, 0))
        checks_border = ttk.Frame(right, borderwidth=1, relief="groove")
        checks_border.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 10))
        checks_border.columnconfigure(0, weight=1)
        checks_border.rowconfigure(0, weight=1)
        self.checks_area = ScrollableFrame(checks_border)
        self.checks_area.grid(row=0, column=0, sticky="nsew")

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
    #  Custom date toggle helpers
    # --------------------------------------------------------
    def _toggle_custom_dates(self):
        if self._custom_dates_enabled.get():
            self._custom_dates_frame.grid()
            self._set_month_selection_state("disabled")
        else:
            self._custom_dates_frame.grid_remove()
            self._set_month_selection_state("normal")
            self.start_date.set("")
            self.end_date.set("")
        self._invalidate_rain_data()

    def _toggle_rain_section(self):
        if self._rain_enabled.get():
            self._rain_section_frame.grid()
        else:
            self._rain_section_frame.grid_remove()
        self._update_generate_button_state()

    def _bind_generator_input_traces(self):
        self._quick_year.trace_add("write", self._on_generator_inputs_changed)
        self.start_date.trace_add("write", self._on_generator_inputs_changed)
        self.end_date.trace_add("write", self._on_generator_inputs_changed)
        for var in self._month_vars:
            var.trace_add("write", self._on_generator_inputs_changed)

    def _on_generator_inputs_changed(self, *_args):
        self._invalidate_rain_data()

    def _set_month_selection_state(self, state: str):
        if hasattr(self, "_year_combo"):
            self._year_combo.configure(
                state="disabled" if state == "disabled" else "readonly"
            )
        for checkbutton in self._month_checkbuttons:
            checkbutton.configure(state=state)

    def _show_rain_days_detail(self, events: list[RainDay]):
        if events:
            sorted_events = sorted(events, key=lambda rd: rd.date)
            lines = [
                f"  {rd.date.strftime('%b %d, %Y')}  —  {rd.rainfall_inches:.2f}\""
                for rd in sorted_events
            ]
            self._rain_days_detail.set("Rain days found:\n" + "\n".join(lines))
            self._rain_days_detail_label.grid()
        else:
            self._rain_days_detail.set("")
            self._rain_days_detail_label.grid_remove()

    def _invalidate_rain_data(self):
        self._rain_data_ready = False
        self._rain_days = []
        self._rain_days_detail.set("")
        self._rain_days_detail_label.grid_remove()
        if self._rain_enabled.get() and self.rain_status.get():
            self.rain_status.set("Rain data needs to be fetched or loaded.")
        self._update_generate_button_state()

    def _update_generate_button_state(self):
        if not hasattr(self, "_generate_btn"):
            return
        state = "normal"
        if self._rain_enabled.get() and not self._rain_data_ready:
            state = "disabled"
        self._generate_btn.config(state=state)

    def _get_selected_months(self) -> list[int]:
        checked = [i + 1 for i, v in enumerate(self._month_vars) if v.get()]
        if not checked:
            raise ValueError("Check at least one month.")
        return checked

    def _resolve_selected_date_range(self) -> tuple[date_cls, date_cls]:
        today = date_cls.today()
        if self._custom_dates_enabled.get():
            start = date_cls.fromisoformat(
                self._parse_user_date_mdy(self.start_date.get())
            )
            end = date_cls.fromisoformat(self._parse_user_date_mdy(self.end_date.get()))
        else:
            checked = self._get_selected_months()
            year = int(self._quick_year.get())
            first_month = checked[0]
            last_month = checked[-1]
            start = date_cls(year, first_month, 1)
            end = date_cls(year, last_month, cal_mod.monthrange(year, last_month)[1])

        if start <= today <= end:
            end = today
        if start > end:
            raise ValueError("The selected date range ends before it begins.")
        return start, end

    def _resolve_rain_fetch_date_range(self) -> tuple[date_cls, date_cls]:
        start, end = self._resolve_selected_date_range()
        last_completed_day = date_cls.today() - timedelta(days=1)
        if end > last_completed_day:
            end = last_completed_day
        if start > end:
            raise ValueError(
                "No completed rain data is available for the selected range yet."
            )
        return start, end

    # --------------------------------------------------------
    #  Generate (month checkboxes)
    # --------------------------------------------------------
    def _on_generate_months(self):
        """Generate weekly + rain PDFs for the checked months."""
        try:
            checked = self._get_selected_months()
            year = int(self._quick_year.get())
            start_date, end_date = self._resolve_selected_date_range()

            template = DEFAULT_TEMPLATE
            raw_output_dir = filedialog.askdirectory(title="Choose output folder")
            if not raw_output_dir:
                return
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
            notes_texts = self._collect_notes_texts()

            iso_start = start_date.isoformat()
            iso_end = end_date.isoformat()

            options = build_run_options(
                output_dir=str(outdir),
                start_date=iso_start,
                end_date=iso_end,
                date_format=DEFAULT_DATE_FORMAT,
                make_zip=True,
            )

            dates = list(weekly_dates(options.start_date, options.end_date))
            written = generate_batch(
                template_path=str(template),
                project=project,
                options=options,
                dates=dates,
                mapping=mapping,
                checkbox_states=check_states,
                notes_texts=notes_texts,
            )

            # Rain event PDFs for loaded rain days within checked months
            if self._rain_enabled.get() and self._rain_data_ready and self._rain_days:
                month_rain = [
                    rd for rd in self._rain_days if start_date <= rd.date <= end_date
                ]
                if month_rain:
                    original_type = (
                        self.project_entries.get("inspection_type", tk.StringVar())
                        .get()
                        .strip()
                    )
                    rain_written = generate_rain_batch(
                        template_path=str(template),
                        project=project,
                        options=options,
                        rain_days=month_rain,
                        mapping=mapping,
                        checkbox_states=check_states,
                        notes_texts=notes_texts,
                        original_inspection_type=original_type,
                    )
                    written.extend(rain_written)

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

    # --------------------------------------------------------
    #  Rain Days handlers
    # --------------------------------------------------------
    def _on_fetch_rain(self):
        """Fetch rainfall data from Mesonet using checked months for the date range."""
        try:
            station_display = self.rain_station.get()
            if not station_display:
                raise ValueError("Select a Mesonet station.")
            station_code = parse_station_code(station_display)
            start_dt, end_dt = self._resolve_rain_fetch_date_range()
        except ValueError as exc:
            self._rain_data_ready = False
            self._update_generate_button_state()
            self.rain_status.set(f"Error: {exc}")
            return

        self.rain_status.set("Fetching rainfall data from Mesonet...")
        self._rain_data_ready = False
        self._update_generate_button_state()
        self._rain_progress["value"] = 0
        self._rain_progress.grid()  # show progress bar

        def _on_progress(day_num: int, total_days: int):
            self.after(
                0, lambda d=day_num, t=total_days: self._rain_update_progress(d, t)
            )

        def _worker():
            try:
                result = fetch_rainfall(
                    station_code, start_dt, end_dt, progress=_on_progress
                )
                events = filter_rain_events(result.days)
                self.after(
                    0,
                    lambda: self._rain_fetch_done(
                        result.days,
                        events,
                        result.failed,
                        result.missing,
                    ),
                )
            except Exception as exc:
                self.after(0, lambda e=exc: self._rain_fetch_error(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _rain_update_progress(self, day_num: int, total_days: int):
        pct = int(day_num / total_days * 100) if total_days else 100
        self._rain_progress["value"] = pct
        self.rain_status.set(f"Fetching day {day_num} of {total_days}  ({pct}%)")

    def _rain_fetch_done(
        self,
        all_days: list[RainDay],
        events: list[RainDay],
        failed: int = 0,
        missing: int = 0,
    ):
        self._rain_progress.grid_remove()  # hide progress bar
        self._rain_days = events
        self._rain_data_ready = True
        self._update_generate_button_state()
        msg = (
            f"Found {len(events)} rain day(s) with 0.5+ inches "
            f"out of {len(all_days)} total day(s)."
        )
        if failed:
            msg += f"  \u26a0 {failed} day(s) failed to fetch."
        if missing:
            msg += f"  \u26a0 {missing} day(s) had missing data."
        self.rain_status.set(msg)
        self._show_rain_days_detail(events)

    def _rain_fetch_error(self, exc: Exception):
        self._rain_progress.grid_remove()  # hide progress bar
        self._rain_data_ready = False
        self._update_generate_button_state()
        self.rain_status.set(
            f"Fetch failed: {exc}\nUse 'Load CSV' to load data manually."
        )

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
            self._rain_data_ready = True
            self._update_generate_button_state()
            self.rain_status.set(
                f"Loaded {len(all_days)} day(s) from file. "
                f"{len(events)} rain day(s) with 0.5+ inches."
            )
            self._show_rain_days_detail(events)
        except Exception as exc:
            self._rain_data_ready = False
            self._update_generate_button_state()
            self.rain_status.set(f"CSV load error: {exc}")

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
        self.notes_vars.clear()

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

            # Notes text box for this section
            if group.notes_field:
                notes_row = len(pdf_list) + 1
                ttk.Label(grp_frame, text="Notes:", anchor="w").grid(
                    row=notes_row,
                    column=0,
                    columnspan=4,
                    sticky="w",
                    padx=6,
                    pady=(6, 0),
                )
                notes_text = tk.Text(
                    grp_frame,
                    height=4,
                    width=10,
                    wrap="word",
                    font=("Segoe UI", 9),
                )
                notes_text.grid(
                    row=notes_row + 1,
                    column=0,
                    columnspan=4,
                    sticky="ew",
                    padx=6,
                    pady=(0, 6),
                )
                self.notes_vars[group_key] = notes_text

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

    # --------------------------------------------------------
    #  Test-data helpers
    # --------------------------------------------------------
    def _fill_test_fields(self):
        for var in self.project_entries.values():
            var.set(_random_lorem(random.randint(2, 5)))

    def _fill_test_checklist(self):
        for group_key, group in self.checkbox_vars.items():
            for _label, var in group.items():
                var.set(random.choice(["YES", "NO"]))
        # Refresh toggle-button visuals
        for group_key in self.checkbox_vars:
            mapping_group = self._current_mapping.checkboxes[group_key]
            pdf_list = getattr(mapping_group, "pdf_fields", []) or []
            parent = None
            for w in self.checks_area.inner.winfo_children():
                if (
                    isinstance(w, ttk.LabelFrame)
                    and w.cget("text") == group_key.replace("_", " ").title()
                ):
                    parent = w
                    break
            if parent is None:
                continue
            for i, item in enumerate(pdf_list, start=1):
                val = self.checkbox_vars[group_key].get(item.text, tk.StringVar()).get()
                for sib in parent.grid_slaves(row=i):
                    if isinstance(sib, tk.Button):
                        if sib.cget("text") == val:
                            sib.config(relief="sunken", bg="#cde9ff")
                        else:
                            sib.config(relief="raised", bg="#f0f0f0")

    def _fill_test_notes(self):
        for widget in self.notes_vars.values():
            widget.delete("1.0", "end")
            widget.insert("1.0", _random_lorem(random.randint(8, 20)))

    def _collect_checkbox_states(self) -> dict[str, dict[str, str]]:
        return {
            group_key: {item: var.get() for item, var in group.items()}
            for group_key, group in self.checkbox_vars.items()
        }

    def _collect_notes_texts(self) -> dict[str, str]:
        return {
            group_key: widget.get("1.0", "end-1c").strip()
            for group_key, widget in self.notes_vars.items()
            if widget.get("1.0", "end-1c").strip()
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


# ============================================================
#  Entry Point
# ============================================================


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
