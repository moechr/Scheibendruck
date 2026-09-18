"""
print_gui.py
------------
Grafische Oberflaeche fuer den Wettkampf-Scheibendruck mit dem TM-U950 -
derselbe Ablauf wie print_session.py, aber per Formular und Maus statt
Kommandozeile und Eingabemasken:

  1. Drucker:   Disziplin (Profil aus config.ini), Vorlage, COM-Port,
                optional Testmodus ohne Drucker
  2. Wettkampf: Vereine, Paarungen, wer beginnt, Schuss pro Scheibe,
                Serien je Stand, Schuss je Serie - Stand-Uebersicht und
                Scheiben-Vorschau rechts aktualisieren sich sofort
  3. Drucken:   ab welcher Scheibe (ein unterbrochener Druck wird
                automatisch erkannt und fortgesetzt), dann "Druck starten"

Waehrend des Drucks laeuft alles automatisch weiter (Einlegen/Druck/
Auswurf werden erkannt, siehe session_engine.py). Per Knopf oder Taste:
    W  diese Scheibe wiederholen (bricht den laufenden Druck ab)
    B  mehrere Scheiben zurueckspringen (bricht ebenfalls ab)
    Q  Druck beenden

Verwendung:
    python print_gui.py                                  (alles im Fenster einstellen)
    python print_gui.py COM5 templates\\lp_paarung.txt --profile LP   (vorbelegen)
"""
import argparse
import configparser
import datetime
import json
import queue
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from matchplan import generate_plan, plan_condensed, plan_fingerprint
from printjob import load_profile, render
from session_engine import (
    APP_DIR,
    DEFAULT_CONFIG,
    PLAN_KEYS,
    PlanRun,
    SimulatedPrinter,
    SlipPrinter,
    Timing,
    load_plan_state,
    print_row_for,
    save_plan_state,
    state_path_for,
)

# Statusanzeige: (Hintergrund, Schrift) je Zustand
BANNER_STYLES = {
    "idle": ("#e9eef4", "#1f2937"),
    "info": ("#dbeafe", "#1e3a8a"),
    "stand": ("#a5e3ef", "#0b1f24"),   # neuer Stand, gleicher Verein
    "verein": ("#ffd84d", "#1f1600"),  # neuer Stand UND anderer Verein
    "ok": ("#d1fae5", "#065f46"),
    "warn": ("#fef3c7", "#78350f"),
    "error": ("#fee2e2", "#991b1b"),
}
HINT_COLORS = {"normal": "#4b5563", "info": "#1d4ed8", "warn": "#b45309", "error": "#b91c1c"}
PAPER_BG = "#fffdf4"

# Erlaubte Bereiche der Zahlenfelder (Minimum, Maximum)
LIMIT_PAARUNGEN = (1, 50)
LIMIT_SERIES = (1, 20)
LIMIT_SHOTS_SERIE = (1, 100)

DEFAULT_FORM = {"club_a": "Verein A", "club_b": "Verein B", "num_paarungen": 5,
                "start_club": "A", "series_count": 4, "shots_per_serie": 10}

# Beispielzeile, um eine Vorlage vor dem Druck auf unbekannte Platzhalter zu pruefen
SAMPLE_ROW = {"stand": 1, "serie": 1, "schuss": "1-2", "verein": "Verein", "name": "Paarung 1 - V"}


class ConfigError(ValueError):
    """Ungueltige Eingabe im Wettkampf-Formular (der Text wird direkt angezeigt)."""


def enable_dpi_awareness() -> None:
    """Ohne das zeichnet Windows das Fenster bei Skalierung > 100 % unscharf."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class BackJumpDialog(tk.Toplevel):
    """Fragt nach einem Abbruch per "Zurueckspringen", wie viele Scheiben zurueck."""

    def __init__(self, app: "App", index: int):
        super().__init__(app.root)
        self.app = app
        self.index = index
        self.result = 1
        self.title("Zurückspringen")
        self.transient(app.root)
        self.resizable(False, False)

        px = app.px
        body = ttk.Frame(self, padding=px(16))
        body.grid(sticky="nsew")
        ttk.Label(body, text=f"Scheibe {index + 1} wurde abgebrochen und ausgeworfen.\n"
                             f"Um wie viele Scheiben zurückspringen?",
                  font=app.bold_font).grid(row=0, column=0, columnspan=2, sticky="w")

        self.var = tk.StringVar(value="1")
        spin = ttk.Spinbox(body, from_=1, to=index + 1, textvariable=self.var, width=6,
                           font=app.big_font)
        spin.grid(row=1, column=0, sticky="w", pady=(px(10), px(4)))
        ttk.Label(body, text="1 = nur diese Scheibe erneut, 2 = auch die davor, …",
                  style="Hint.TLabel").grid(row=1, column=1, sticky="w", padx=(px(10), 0))
        self.target = ttk.Label(body, wraplength=px(460))
        self.target.grid(row=2, column=0, columnspan=2, sticky="w", pady=(px(4), px(12)))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Nur diese Scheibe erneut", command=self._cancel).grid(row=0, column=0)
        ttk.Button(buttons, text="Zurückspringen", command=self._ok,
                   default="active").grid(row=0, column=1, padx=(px(8), 0))

        self.var.trace_add("write", lambda *_: self._update_target())
        self._update_target()
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.update_idletasks()
        root = app.root
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        spin.focus_set()
        spin.selection_range(0, "end")
        self.wait_window()

    def _count(self):
        try:
            n = int(self.var.get())
        except ValueError:
            return None
        return n if 1 <= n <= self.index + 1 else None

    def _update_target(self) -> None:
        n = self._count()
        if n is None:
            self.target.configure(text=f"Bitte eine Zahl von 1 bis {self.index + 1} eingeben.",
                                  foreground=HINT_COLORS["error"])
            return
        target = self.index - (n - 1)
        self.target.configure(text=f"Weiter mit Scheibe {target + 1} von {len(self.app.plan)}: "
                                   f"{self.app.sheet_desc(target)}",
                              foreground=HINT_COLORS["info"])

    def _ok(self) -> None:
        n = self._count()
        if n is None:
            self.bell()
            return
        self.result = n
        self.destroy()

    def _cancel(self) -> None:
        self.result = 1
        self.destroy()


class App:
    POLL_MS = 100

    def __init__(self, root: tk.Tk, config_path: Path, cp: configparser.ConfigParser, args):
        self.root = root
        self.config_path = config_path
        self.cp = cp
        self.templates_dir = config_path.parent / "templates"
        self.settings_path = config_path.parent / "state" / "gui_settings.json"

        scale = root.winfo_fpixels("1i") / 96.0
        self.px = lambda n: int(round(n * scale))

        # Anzeige-Text in der Auswahlliste -> Profilname in config.ini
        self.profiles = {}
        for section in cp.sections():
            if section.startswith("profile:"):
                name = section.split(":", 1)[1]
                title = cp[section].get("title", "").strip()
                self.profiles[f"{name} – {title}" if title else name] = name
        self.template_paths = {}  # Anzeige-Text -> Pfad
        self.port_desc = {}       # COM-Port -> Beschreibung

        self.profile_name = None
        self.profile = {}
        self.saved = {}
        self.template_lines = []
        self.template_error = None
        self.cfg = None
        self.config_error = None
        self.plan = []
        self.condensed = []
        self.fingerprint = None
        self.preview_pos = 0

        self.run = None
        self.worker = None
        self.run_dry = False
        self.current_index = 0
        self.run_retries = 0
        self.quit_requested = False
        self.pending_reply = None
        self.events = queue.Queue()
        self._refresh_pending = False
        self._suppress_preview_var = False

        self.profile_var = tk.StringVar()
        self.template_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=False)
        self.club_a_var = tk.StringVar()
        self.club_b_var = tk.StringVar()
        self.paarungen_var = tk.StringVar()
        self.start_club_var = tk.StringVar(value="A")
        self.sheet_var = tk.StringVar(value="2")
        self.series_var = tk.StringVar()
        self.shots_serie_var = tk.StringVar()
        self.start_at_var = tk.StringVar(value="1")
        self.preview_var = tk.StringVar(value="1")

        self._setup_style()
        self._build_ui()

        for var in (self.club_a_var, self.club_b_var, self.paarungen_var, self.start_club_var,
                    self.sheet_var, self.series_var, self.shots_serie_var):
            var.trace_add("write", self._schedule_refresh)
        self.start_at_var.trace_add("write", lambda *_: self._on_start_at_changed())
        self.preview_var.trace_add("write", lambda *_: self._on_preview_var())
        self.port_var.trace_add("write", lambda *_: self._update_port_hint())
        self.dry_run_var.trace_add("write", lambda *_: self._update_start_state())

        self._load_initial(args)

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<Key>", self._on_key)
        root.bind("<Prior>", lambda e: self._preview_step(-1))
        root.bind("<Next>", lambda e: self._preview_step(1))
        root.after(self.POLL_MS, self._poll_events)

    # ------------------------------------------------------------------ Aufbau

    def _setup_style(self) -> None:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(size=10)
        base = tkfont.nametofont("TkDefaultFont")
        family = base.actual("family")
        self.bold_font = tkfont.Font(family=family, size=10, weight="bold")
        self.big_font = tkfont.Font(family=family, size=13, weight="bold")
        self.banner_font = tkfont.Font(family=family, size=16, weight="bold")
        self.mono_font = tkfont.Font(family="Consolas", size=11)

        style = ttk.Style(self.root)
        px = self.px
        style.configure("TLabelframe.Label", font=self.bold_font)
        style.configure("Hint.TLabel", foreground=HINT_COLORS["normal"])
        style.configure("Summary.TLabel", font=self.bold_font)
        style.configure("Start.TButton", font=self.big_font, padding=(px(12), px(8)))
        style.configure("Control.TButton", padding=(px(10), px(6)))
        style.configure("Treeview", rowheight=int(base.metrics("linespace") * 1.5))
        style.configure("Treeview.Heading", font=self.bold_font)

    def _build_ui(self) -> None:
        root = self.root
        px = self.px
        root.title("TM-U950 Scheibendruck")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        self.inputs = []  # werden waehrend des Drucks gesperrt

        left = ttk.Frame(root, padding=(px(10), px(10), px(8), px(10)))
        left.grid(row=0, column=0, sticky="ns")
        self._build_printer_frame(left).grid(row=0, column=0, sticky="ew")
        self._build_match_frame(left).grid(row=1, column=0, sticky="ew", pady=(px(8), 0))
        self._build_start_frame(left).grid(row=2, column=0, sticky="ew", pady=(px(8), 0))

        right = ttk.Frame(root, padding=(px(2), px(10), px(10), px(10)))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self._build_notebook(right).grid(row=0, column=0, sticky="nsew")
        self._build_preview_frame(right).grid(row=1, column=0, sticky="ew", pady=(px(10), 0))
        self._build_status_frame(right).grid(row=2, column=0, sticky="ew", pady=(px(10), 0))

    def _row_label(self, parent, row: int, text: str) -> None:
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", padx=(0, self.px(10)),
                                          pady=self.px(2))

    def _build_printer_frame(self, parent) -> ttk.LabelFrame:
        px = self.px
        f = ttk.LabelFrame(parent, text=" 1  Drucker ", padding=px(8))
        f.columnconfigure(1, weight=1)

        self._row_label(f, 0, "Disziplin")
        self.profile_cb = ttk.Combobox(f, textvariable=self.profile_var, state="readonly",
                                       values=list(self.profiles), width=24)
        self.profile_cb.grid(row=0, column=1, columnspan=2, sticky="ew")
        self.profile_cb.bind("<<ComboboxSelected>>", lambda e: self._on_profile_selected())

        self._row_label(f, 1, "Vorlage")
        self.template_cb = ttk.Combobox(f, textvariable=self.template_var, state="readonly")
        self.template_cb.grid(row=1, column=1, columnspan=2, sticky="ew")
        self.template_cb.bind("<<ComboboxSelected>>", lambda e: self._on_template_selected())

        self._row_label(f, 2, "COM-Port")
        self.port_cb = ttk.Combobox(f, textvariable=self.port_var, width=10)
        self.port_cb.grid(row=2, column=1, sticky="ew")
        self.port_btn = ttk.Button(f, text="Suchen", command=self._refresh_ports, width=8)
        self.port_btn.grid(row=2, column=2, padx=(px(6), 0))
        self.port_hint = ttk.Label(f, style="Hint.TLabel", wraplength=px(250))
        self.port_hint.grid(row=3, column=1, columnspan=2, sticky="w")

        self.dry_cb = ttk.Checkbutton(f, text="Testmodus (ohne Drucker – nichts wird gedruckt)",
                                      variable=self.dry_run_var)
        self.dry_cb.grid(row=4, column=0, columnspan=3, sticky="w", pady=(px(8), 0))

        self.inputs += [self.profile_cb, self.template_cb, self.port_cb, self.port_btn, self.dry_cb]
        return f

    def _build_match_frame(self, parent) -> ttk.LabelFrame:
        px = self.px
        f = ttk.LabelFrame(parent, text=" 2  Wettkampf ", padding=px(8))
        f.columnconfigure(1, weight=1)

        self._row_label(f, 0, "Verein A")
        club_a = ttk.Entry(f, textvariable=self.club_a_var, width=26)
        club_a.grid(row=0, column=1, sticky="ew")
        self._row_label(f, 1, "Verein B")
        club_b = ttk.Entry(f, textvariable=self.club_b_var, width=26)
        club_b.grid(row=1, column=1, sticky="ew")

        self._row_label(f, 2, "Paarungen")
        pf = ttk.Frame(f)
        pf.grid(row=2, column=1, sticky="w")
        paarungen = ttk.Spinbox(pf, from_=LIMIT_PAARUNGEN[0], to=LIMIT_PAARUNGEN[1],
                                textvariable=self.paarungen_var, width=5)
        paarungen.grid(row=0, column=0)
        ttk.Label(pf, text="Schützen-Paare", style="Hint.TLabel").grid(row=0, column=1, padx=(px(8), 0))

        self._row_label(f, 3, "Beginnt an Stand 1")
        sf = ttk.Frame(f)
        sf.grid(row=3, column=1, sticky="w", pady=px(3))
        self.rb_a = ttk.Radiobutton(sf, variable=self.start_club_var, value="A")
        self.rb_a.grid(row=0, column=0, sticky="w")
        self.rb_b = ttk.Radiobutton(sf, variable=self.start_club_var, value="B")
        self.rb_b.grid(row=1, column=0, sticky="w")

        self._row_label(f, 4, "Schuss pro Scheibe")
        shf = ttk.Frame(f)
        shf.grid(row=4, column=1, sticky="w")
        rb1 = ttk.Radiobutton(shf, text="1", variable=self.sheet_var, value="1")
        rb1.grid(row=0, column=0)
        rb2 = ttk.Radiobutton(shf, text="2", variable=self.sheet_var, value="2")
        rb2.grid(row=0, column=1, padx=(px(16), 0))

        self._row_label(f, 5, "Serien je Stand")
        series = ttk.Spinbox(f, from_=LIMIT_SERIES[0], to=LIMIT_SERIES[1],
                             textvariable=self.series_var, width=5)
        series.grid(row=5, column=1, sticky="w")
        self._row_label(f, 6, "Schuss je Serie")
        shots = ttk.Spinbox(f, from_=LIMIT_SHOTS_SERIE[0], to=LIMIT_SHOTS_SERIE[1],
                            textvariable=self.shots_serie_var, width=5)
        shots.grid(row=6, column=1, sticky="w")

        self.summary = ttk.Label(f, style="Summary.TLabel", wraplength=px(320))
        self.summary.grid(row=7, column=0, columnspan=2, sticky="w", pady=(px(8), 0))

        self.inputs += [club_a, club_b, paarungen, self.rb_a, self.rb_b, rb1, rb2, series, shots]
        return f

    def _build_start_frame(self, parent) -> ttk.LabelFrame:
        px = self.px
        f = ttk.LabelFrame(parent, text=" 3  Drucken ", padding=px(8))
        f.columnconfigure(0, weight=1)

        row = ttk.Frame(f)
        row.grid(row=0, column=0, sticky="ew")
        ttk.Label(row, text="Start ab Scheibe").grid(row=0, column=0, padx=(0, px(8)))
        self.start_sb = ttk.Spinbox(row, from_=1, to=1, textvariable=self.start_at_var, width=6)
        self.start_sb.grid(row=0, column=1)
        self.total_label = ttk.Label(row)
        self.total_label.grid(row=0, column=2, padx=(px(6), 0))
        self.from_start_btn = ttk.Button(row, text="Von vorne",
                                         command=lambda: self.start_at_var.set("1"))
        self.from_start_btn.grid(row=0, column=3, padx=(px(10), 0))

        self.start_hint = ttk.Label(f, wraplength=px(320))
        self.start_hint.grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        self.adopt_btn = ttk.Button(f, text="Unterbrochenen Druck übernehmen",
                                    command=self._adopt_saved)
        self.adopt_btn.grid(row=2, column=0, sticky="w", pady=(px(6), 0))
        self.test_hint = ttk.Label(f, wraplength=px(320), foreground=HINT_COLORS["warn"],
                                   text="Testmodus: Es wird nichts gedruckt und kein "
                                        "Fortschritt gespeichert.")
        self.test_hint.grid(row=3, column=0, sticky="w", pady=(px(6), 0))

        self.start_btn = ttk.Button(f, text="▶  Druck starten", style="Start.TButton",
                                    command=self._start)
        self.start_btn.grid(row=4, column=0, sticky="ew", pady=(px(12), 0))

        self.inputs += [self.start_sb, self.from_start_btn, self.adopt_btn]
        return f

    def _build_notebook(self, parent) -> ttk.Notebook:
        px = self.px
        nb = ttk.Notebook(parent)

        tab = ttk.Frame(nb, padding=px(6))
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        columns = ("stand", "verein", "name", "sheets", "schuss", "status")
        self.tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse", height=6)
        for col, text, width, anchor in (
                ("stand", "Stand", 60, "center"), ("verein", "Verein", 150, "w"),
                ("name", "Paarung", 130, "w"), ("sheets", "Scheiben", 75, "center"),
                ("schuss", "Schuss", 100, "center"), ("status", "Status", 150, "w")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=px(width), minwidth=px(50), anchor=anchor)
        self.tree.tag_configure("done", foreground="#9ca3af")
        self.tree.tag_configure("current", background="#fff3b0")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_tree_select())
        nb.add(tab, text="Übersicht: Stand für Stand")

        tab = ttk.Frame(nb, padding=px(6))
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.log = tk.Text(tab, height=8, wrap="word", state="disabled", relief="flat",
                           font=tkfont.nametofont("TkDefaultFont"))
        self.log.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=self.log.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=vsb.set)
        nb.add(tab, text="Protokoll")
        return nb

    def _build_preview_frame(self, parent) -> ttk.LabelFrame:
        px = self.px
        f = ttk.LabelFrame(parent, text=" Vorschau – genau so wird die Scheibe bedruckt ",
                           padding=px(10))
        f.columnconfigure(0, weight=1)

        nav = ttk.Frame(f)
        nav.grid(row=0, column=0, sticky="ew")
        nav.columnconfigure(5, weight=1)
        self.prev_btn = ttk.Button(nav, text="◀", width=3, command=lambda: self._preview_step(-1))
        self.prev_btn.grid(row=0, column=0)
        ttk.Label(nav, text="Scheibe").grid(row=0, column=1, padx=(px(8), px(4)))
        self.preview_sb = ttk.Spinbox(nav, from_=1, to=1, textvariable=self.preview_var, width=6)
        self.preview_sb.grid(row=0, column=2)
        self.preview_total = ttk.Label(nav)
        self.preview_total.grid(row=0, column=3, padx=(px(4), px(8)))
        self.next_btn = ttk.Button(nav, text="▶", width=3, command=lambda: self._preview_step(1))
        self.next_btn.grid(row=0, column=4)
        self.here_btn = ttk.Button(nav, text="Ab dieser Scheibe drucken",
                                   command=lambda: self.start_at_var.set(str(self.preview_pos + 1)))
        self.here_btn.grid(row=0, column=6, sticky="e")

        card_frame = tk.Frame(f, bg="#9ca3af", padx=1, pady=1)
        card_frame.grid(row=1, column=0, pady=(px(10), px(6)))
        self.card = tk.Text(card_frame, font=self.mono_font, width=66, height=6, wrap="none",
                            bg=PAPER_BG, fg="#111827", relief="flat", padx=px(14), pady=px(10),
                            cursor="arrow", takefocus=0, highlightthickness=0)
        self.card.grid()
        self.card.configure(state="disabled")

        self.preview_info = ttk.Label(f, style="Hint.TLabel")
        self.preview_info.grid(row=2, column=0)

        self.inputs += [self.prev_btn, self.preview_sb, self.next_btn, self.here_btn]
        return f

    def _build_status_frame(self, parent) -> ttk.LabelFrame:
        px = self.px
        f = ttk.LabelFrame(parent, text=" Druckstatus ", padding=px(8))
        f.columnconfigure(0, weight=1)

        self.banner = tk.Frame(f, padx=px(14), pady=px(10))
        self.banner.grid(row=0, column=0, sticky="ew")
        self.banner.columnconfigure(0, weight=1)
        self.banner_title = tk.Label(self.banner, font=self.banner_font, anchor="w", justify="left")
        self.banner_title.grid(row=0, column=0, sticky="ew")
        self.banner_sub = tk.Label(self.banner, anchor="nw", justify="left", wraplength=px(620), height=2)
        self.banner_sub.grid(row=1, column=0, sticky="ew")

        prog = ttk.Frame(f)
        prog.grid(row=1, column=0, sticky="ew", pady=(px(10), 0))
        prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(prog, anchor="e")
        self.progress_label.grid(row=0, column=1, padx=(px(10), 0))

        buttons = ttk.Frame(f)
        buttons.grid(row=2, column=0, sticky="w", pady=(px(10), 0))
        self.repeat_btn = ttk.Button(buttons, text="↻  Scheibe wiederholen  (W)",
                                     style="Control.TButton", command=lambda: self._control("w"))
        self.repeat_btn.grid(row=0, column=0)
        self.back_btn = ttk.Button(buttons, text="⟲  Zurückspringen …  (B)",
                                   style="Control.TButton", command=lambda: self._control("b"))
        self.back_btn.grid(row=0, column=1, padx=(px(8), 0))
        self.quit_btn = ttk.Button(buttons, text="■  Druck beenden  (Q)",
                                   style="Control.TButton", command=lambda: self._control("q"))
        self.quit_btn.grid(row=0, column=2, padx=(px(8), 0))
        for b in (self.repeat_btn, self.back_btn, self.quit_btn):
            b.state(["disabled"])

        self._set_banner("Bereit", "idle",
                         "Einstellungen links prüfen, dann „Druck starten“ klicken.")
        return f

    # ------------------------------------------------------ Laden/Speichern

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_settings(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps({
                "profile": self.profile_name,
                "template": self.template_var.get(),
                "port": self.port_var.get().strip(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _add_template(self, path: Path) -> str:
        """Nimmt eine Vorlage in die Auswahlliste auf und gibt ihren Anzeige-Text zurueck."""
        path = path.resolve()
        key = path.name if path.parent == self.templates_dir.resolve() else str(path)
        self.template_paths[key] = path
        self.template_cb.configure(values=list(self.template_paths))
        return key

    def _load_initial(self, args) -> None:
        settings = self._load_settings()

        names = list(self.profiles.values())
        profile = next((p for p in (args.profile, settings.get("profile")) if p in names), names[0])

        for path in sorted(self.templates_dir.glob("*.txt")):
            self._add_template(path)
        template_key = None
        saved_template = settings.get("template") if settings.get("profile") == profile else None
        if args.template:
            path = Path(args.template)
            if not path.exists() and not path.is_absolute() and (APP_DIR / path).exists():
                path = APP_DIR / path
            if path.exists():
                template_key = self._add_template(path)
        elif saved_template in self.template_paths:
            template_key = saved_template
        elif saved_template and Path(saved_template).exists():
            template_key = self._add_template(Path(saved_template))

        self.profile_var.set(next(d for d, n in self.profiles.items() if n == profile))
        self._select_profile(profile, adopt_template=template_key is None)
        if template_key is not None:
            self.template_var.set(template_key)
        elif not self.template_var.get() and self.template_paths:
            self.template_var.set(next(iter(self.template_paths)))
        self._load_template()

        self._refresh_ports()
        # Ohne gemerkten Port: bevorzugt den USB-Seriell-Wandler statt z.B. eines eingebauten COM1
        usb = next((p for p, desc in self.port_desc.items() if "usb" in desc.lower()), None)
        port = args.port or settings.get("port") or usb or next(iter(self.port_desc), "")
        self.port_var.set(port)
        self._update_port_hint()
        self.dry_run_var.set(bool(args.dry_run))

        # Die zuletzt gedruckten Werte als Vorschlag - bei einem unterbrochenen
        # Druck passt das Formular damit sofort zum gespeicherten Fortschritt.
        if all(k in self.saved for k in PLAN_KEYS):
            self._fill_form(self.saved)
        else:
            self._fill_form({**DEFAULT_FORM, "shots_per_sheet": self.profile["shots_per_sheet"]})
        self._refresh()

    def _fill_form(self, values: dict) -> None:
        self.club_a_var.set(values["club_a"])
        self.club_b_var.set(values["club_b"])
        self.paarungen_var.set(str(values["num_paarungen"]))
        self.start_club_var.set(values["start_club"])
        self.sheet_var.set("1" if values["shots_per_sheet"] == 1 else "2")
        self.series_var.set(str(values["series_count"]))
        self.shots_serie_var.set(str(values["shots_per_serie"]))

    def _select_profile(self, name: str, adopt_template: bool = True) -> None:
        self.profile_name = name
        self.profile = load_profile(name, self.cp, self.config_path)
        self.saved = load_plan_state(state_path_for(self.config_path, name))
        if adopt_template:
            default = f"{name.lower()}_paarung.txt"
            if default in self.template_paths:
                self.template_var.set(default)

    def _load_template(self) -> None:
        self.template_lines = []
        self.template_error = None
        path = self.template_paths.get(self.template_var.get())
        if path is None:
            self.template_error = f"Keine Vorlage gefunden (Ordner {self.templates_dir})."
            return
        sample = ""
        try:
            self.template_lines = path.read_text(encoding="utf-8").splitlines()
            sample = render(self.template_lines, SAMPLE_ROW, self.profile["line_width"])
        except OSError as exc:
            self.template_error = f"Vorlage nicht lesbar: {exc}"
        except SystemExit as exc:  # printjob.render meldet unbekannte Platzhalter so
            self.template_error = f"Vorlage passt nicht: {exc}"
        widest = max((len(line) for line in sample.split("\n")), default=0)
        self.card.configure(width=max(self.profile["line_width"] or 40, widest),
                            height=max(3, len(self.template_lines)))

    def _refresh_ports(self) -> None:
        try:
            from serial.tools import list_ports
            ports = list_ports.comports()
        except Exception:
            ports = []
        ports = sorted(ports, key=lambda p: (len(p.device), p.device))
        self.port_desc = {p.device: p.description for p in ports}
        self.port_cb.configure(values=list(self.port_desc))
        self._update_port_hint()

    def _update_port_hint(self) -> None:
        port = self.port_var.get().strip()
        if port in self.port_desc:
            text = self.port_desc[port]
        elif not self.port_desc:
            text = "Kein COM-Port gefunden – USB-Seriell-Wandler eingesteckt? Dann „Suchen“."
        elif port:
            text = f"{port} ist gerade nicht angeschlossen – Wandler eingesteckt? Dann „Suchen“."
        else:
            text = "Bitte den COM-Port des Druckers auswählen."
        self.port_hint.configure(text=text)

    # ------------------------------------------------ Formular -> Druckplan

    def _schedule_refresh(self, *_) -> None:
        if not self._refresh_pending:
            self._refresh_pending = True
            self.root.after_idle(self._refresh)

    def _read_int(self, var: tk.StringVar, label: str, limits: tuple) -> int:
        lo, hi = limits
        try:
            value = int(var.get().strip())
        except ValueError:
            raise ConfigError(f"„{label}“ muss eine ganze Zahl sein.")
        if not lo <= value <= hi:
            raise ConfigError(f"„{label}“ muss zwischen {lo} und {hi} liegen.")
        return value

    def _read_form(self) -> dict:
        club_a = self.club_a_var.get().strip()
        club_b = self.club_b_var.get().strip()
        if not club_a or not club_b:
            raise ConfigError("Bitte beide Vereinsnamen eingeben.")
        return {
            "club_a": club_a,
            "club_b": club_b,
            "num_paarungen": self._read_int(self.paarungen_var, "Paarungen", LIMIT_PAARUNGEN),
            "start_club": "B" if self.start_club_var.get() == "B" else "A",
            "shots_per_sheet": 1 if self.sheet_var.get() == "1" else 2,
            "series_count": self._read_int(self.series_var, "Serien je Stand", LIMIT_SERIES),
            "shots_per_serie": self._read_int(self.shots_serie_var, "Schuss je Serie", LIMIT_SHOTS_SERIE),
        }

    def _refresh(self) -> None:
        self._refresh_pending = False
        if self.run is not None:
            return
        self.rb_a.configure(text=self.club_a_var.get().strip() or "Verein A")
        self.rb_b.configure(text=self.club_b_var.get().strip() or "Verein B")
        try:
            self.cfg = self._read_form()
            self.config_error = None
            self.plan = generate_plan(**self.cfg)
            self.fingerprint = plan_fingerprint(**self.cfg)
        except ConfigError as exc:
            self.cfg = None
            self.config_error = str(exc)
            self.plan = []
            self.fingerprint = None
        self.condensed = plan_condensed(self.plan)

        self.tree.delete(*self.tree.get_children())
        for c in self.condensed:
            self.tree.insert("", "end", iid=str(c["first_index"]),
                             values=(c["stand"], c["verein"], c["name"], c["sheets"],
                                     c["schuss_range"], ""))
        total = max(1, len(self.plan))
        self.start_sb.configure(to=total)
        self.preview_sb.configure(to=total)
        self._update_summary()
        self._auto_start_index()

    def _update_summary(self) -> None:
        if self.config_error:
            self.summary.configure(text=self.config_error, foreground=HINT_COLORS["error"])
        elif self.template_error:
            self.summary.configure(text=self.template_error, foreground=HINT_COLORS["error"])
        else:
            per_stand = self.condensed[0]["sheets"] if self.condensed else 0
            self.summary.configure(
                text=f"→ {len(self.plan)} Scheiben · {len(self.condensed)} Stände à {per_stand}",
                foreground="")

    def _saved_matches(self) -> bool:
        return self.fingerprint is not None and self.saved.get("fingerprint") == self.fingerprint

    def _saved_session(self):
        """(Scheiben gesamt, erledigt) des gespeicherten Fortschritts oder None."""
        if not all(k in self.saved for k in PLAN_KEYS):
            return None
        try:
            total = len(generate_plan(**{k: self.saved[k] for k in PLAN_KEYS}))
        except (ValueError, TypeError):
            return None
        return total, self.saved.get("index", 0)

    def _auto_start_index(self) -> None:
        """Startet bei einem passenden unterbrochenen Druck dort, sonst bei Scheibe 1."""
        index = 0
        if self._saved_matches() and 0 < self.saved.get("index", 0) < len(self.plan):
            index = self.saved["index"]
        self.start_at_var.set(str(index + 1))

    def _start_index(self):
        try:
            value = int(self.start_at_var.get().strip()) - 1
        except ValueError:
            return None
        return value if 0 <= value < len(self.plan) else None

    def _on_start_at_changed(self) -> None:
        if self.run is not None:
            return
        self._update_start_state()
        start = self._start_index()
        self._show_preview(start if start is not None else self.preview_pos)

    def _start_hint(self, start):
        if not self.plan:
            return "", "normal"
        total = len(self.plan)
        if start is None:
            return f"Bitte eine Scheibe zwischen 1 und {total} angeben.", "error"
        if self._saved_matches():
            done = self.saved.get("index", 0)
            if done >= total:
                return (f"Dieser Wettkampf wurde bereits vollständig gedruckt. „Druck starten“ "
                        f"druckt ihn ab Scheibe {start + 1} noch einmal."), "warn"
            if done > 0 and start == done:
                return (f"Unterbrochener Druck erkannt: Scheibe 1–{done} sind bereits gedruckt, "
                        f"es geht bei Scheibe {start + 1} von {total} weiter."), "info"
            if done > 0:
                return (f"Bereits gedruckt bis Scheibe {done}. Gewählt: Start bei Scheibe "
                        f"{start + 1}."), "warn"
        else:
            session = self._saved_session()
            if session and 0 < session[1] < session[0]:
                return (f"Neuer Wettkampf. Achtung: Der unterbrochene Druck "
                        f"„{self.saved['club_a']} vs. {self.saved['club_b']}“ (bis Scheibe "
                        f"{session[1]} von {session[0]}) wird beim Start verworfen."), "warn"
        if start == 0:
            return "Neuer Wettkampf – der Druck beginnt bei Scheibe 1.", "normal"
        return (f"Der Druck beginnt bei Scheibe {start + 1}, die Scheiben davor werden "
                f"übersprungen."), "warn"

    def _update_start_state(self) -> None:
        self.total_label.configure(text=f"von {len(self.plan)}" if self.plan else "")
        start = self._start_index()
        text, kind = self._start_hint(start)
        self.start_hint.configure(text=text, foreground=HINT_COLORS[kind])

        session = self._saved_session()
        if (self.run is None and session and 0 < session[1] < session[0]
                and not self._saved_matches()):
            self.adopt_btn.grid()
        else:
            self.adopt_btn.grid_remove()
        if self.dry_run_var.get():
            self.test_hint.grid()
        else:
            self.test_hint.grid_remove()
        self._update_tree_status()

    def _update_tree_status(self) -> None:
        running = self.run is not None
        if running:
            pos, done_upto = self.current_index, self.current_index
        else:
            pos = self._start_index()
            done_upto = min(self.saved.get("index", 0), len(self.plan)) if self._saved_matches() else 0
        for c in self.condensed:
            iid = str(c["first_index"])
            if pos is None or pos >= len(self.plan):
                status, tags = ("erledigt", ("done",)) if pos is not None else ("", ())
            elif c["last_index"] < pos:
                status = "erledigt" if c["last_index"] < done_upto else "wird übersprungen"
                tags = ("done",)
            elif c["first_index"] <= pos:
                n = pos - c["first_index"] + 1
                status = f"▶ {'läuft' if running else 'Start'} (Scheibe {n}/{c['sheets']})"
                tags = ("current",)
            else:
                status, tags = "offen", ()
            self.tree.set(iid, "status", status)
            self.tree.item(iid, tags=tags)

    def _adopt_saved(self) -> None:
        self._fill_form(self.saved)
        self._refresh()

    def _on_profile_selected(self) -> None:
        self._select_profile(self.profiles[self.profile_var.get()])
        self._load_template()
        self._refresh()

    def _on_template_selected(self) -> None:
        self._load_template()
        self._refresh()

    # ---------------------------------------------------------------- Vorschau

    def sheet_desc(self, idx: int) -> str:
        r = self.plan[idx]
        return f"Stand {r['stand']} · Serie {r['serie']} · Schuss {r['schuss']} · {r['verein']}"

    def _set_card(self, text: str) -> None:
        self.card.configure(state="normal")
        self.card.delete("1.0", "end")
        self.card.insert("1.0", text)
        self.card.configure(state="disabled")

    def _show_preview(self, idx: int) -> None:
        if not self.plan or self.template_error:
            self._set_card(self.config_error or self.template_error or "")
            self.preview_total.configure(text="")
            self.preview_info.configure(text="")
            return
        idx = max(0, min(len(self.plan) - 1, idx))
        self.preview_pos = idx
        self._suppress_preview_var = True
        try:
            self.preview_var.set(str(idx + 1))
        finally:
            self._suppress_preview_var = False
        self.preview_total.configure(text=f"von {len(self.plan)}")

        printed = print_row_for(self.plan, idx)
        self._set_card(render(self.template_lines, printed, self.profile["line_width"]))
        info = self.sheet_desc(idx)
        if not printed["verein"]:
            info += "   (Vereinsname nur auf der 1. Scheibe jeder Serie)"
        self.preview_info.configure(text=info)

        if self.run is None:
            for b, enabled in ((self.prev_btn, idx > 0), (self.next_btn, idx < len(self.plan) - 1)):
                b.state(["!disabled"] if enabled else ["disabled"])

        stand = next((c for c in self.condensed if c["first_index"] <= idx <= c["last_index"]), None)
        if stand is not None:
            iid = str(stand["first_index"])
            if self.tree.selection() != (iid,):
                self.tree.selection_set(iid)
            self.tree.see(iid)

    def _preview_step(self, delta: int) -> None:
        if self.run is None:
            self._show_preview(self.preview_pos + delta)

    def _on_preview_var(self) -> None:
        if self._suppress_preview_var or self.run is not None:
            return
        try:
            value = int(self.preview_var.get().strip())
        except ValueError:
            return
        if 1 <= value <= len(self.plan):
            self._show_preview(value - 1)

    def _on_tree_select(self) -> None:
        if self.run is not None:
            return
        sel = self.tree.selection()
        if not sel:
            return
        stand = next((c for c in self.condensed if str(c["first_index"]) == sel[0]), None)
        if stand and not stand["first_index"] <= self.preview_pos <= stand["last_index"]:
            self._show_preview(stand["first_index"])

    # ------------------------------------------------------------------ Druck

    def _start(self) -> None:
        if self.run is not None:
            return
        if self.config_error:
            messagebox.showwarning("Eingabe prüfen", self.config_error, parent=self.root)
            return
        if self.template_error:
            messagebox.showwarning("Vorlage prüfen", self.template_error, parent=self.root)
            return
        start = self._start_index()
        if start is None:
            messagebox.showwarning("Startscheibe prüfen",
                                   f"Bitte bei „Start ab Scheibe“ eine Zahl von 1 bis "
                                   f"{len(self.plan)} eingeben.", parent=self.root)
            return
        dry = self.dry_run_var.get()
        port = self.port_var.get().strip()
        if not dry and not port:
            messagebox.showwarning("COM-Port fehlt",
                                   "Bitte den COM-Port des Druckers auswählen "
                                   "(oder den Testmodus einschalten).", parent=self.root)
            return

        cfg = self.cfg
        starter = cfg["club_a"] if cfg["start_club"] == "A" else cfg["club_b"]
        lines = [
            f"{cfg['club_a']} vs. {cfg['club_b']} – {cfg['num_paarungen']} Paarungen, "
            f"beginnt: {starter}",
            f"{len(self.plan)} Scheiben auf {len(self.condensed)} Ständen, "
            f"{cfg['shots_per_sheet']} Schuss pro Scheibe",
            "",
            f"Start bei Scheibe {start + 1} von {len(self.plan)}:",
            f"   {self.sheet_desc(start)}",
            "",
            ("Testmodus – es wird NICHTS gedruckt." if dry else
             f"Drucker: {port} · {self.profile_name} · Vorlage {self.template_var.get()}"),
        ]
        text, kind = self._start_hint(start)
        if kind == "warn" and not dry:
            lines += ["", text]
        lines += ["", "Jetzt starten?"]
        if not messagebox.askyesno("Druck starten?", "\n".join(lines), parent=self.root):
            return

        self._save_settings()
        timing = Timing()
        printer = SimulatedPrinter(timing) if dry else SlipPrinter(port, self.profile, timing)
        state_path = state_path_for(self.config_path, self.profile_name)
        progress = {"fingerprint": self.fingerprint, **cfg}

        def save_progress(index: int) -> None:
            save_plan_state(state_path, {"index": index, **progress})

        self.run = PlanRun(
            self.plan, self.template_lines, self.profile["line_width"], printer, start,
            on_event=lambda kind, data: self.events.put((kind, data)),
            ask_back=self._ask_back_blocking,
            save_progress=None if dry else save_progress,  # Testmodus speichert nichts
            pause=timing.pause,
        )
        self.run_dry = dry
        self.current_index = start
        self.run_retries = 0
        self.quit_requested = False
        self._set_running(True)
        self.progress.configure(maximum=len(self.plan), value=start)
        self._update_progress_label(start, 0)
        self._log(f"Druck gestartet{' (Testmodus)' if dry else ' an ' + port}: "
                  f"{cfg['club_a']} vs. {cfg['club_b']}, ab Scheibe {start + 1} von {len(self.plan)}.")
        self.worker = threading.Thread(target=self._worker_main, args=(self.run,), daemon=True)
        self.worker.start()

    def _worker_main(self, run: PlanRun) -> None:
        try:
            result = run.run()
        except (Exception, SystemExit) as exc:
            self.events.put(("failed", {"text": str(exc) or exc.__class__.__name__}))
        else:
            self.events.put(("finished", result))

    def _ask_back_blocking(self, index: int) -> int:
        """Laeuft im Druck-Thread: Frage an die Oberflaeche stellen und auf die Antwort warten."""
        reply = queue.Queue(maxsize=1)
        self.events.put(("ask_back", {"index": index, "reply": reply}))
        return reply.get()

    def _set_running(self, running: bool) -> None:
        for w in self.inputs:
            w.state(["disabled"] if running else ["!disabled"])
        self.start_btn.state(["disabled"] if running else ["!disabled"])
        for b in (self.repeat_btn, self.back_btn, self.quit_btn):
            b.state(["!disabled"] if running else ["disabled"])
        if running:
            self.adopt_btn.grid_remove()

    def _control(self, key: str) -> None:
        if self.run is None or self.quit_requested:
            return
        if key == "q":
            where = ("Testmodus – es wird kein Fortschritt gespeichert." if self.run_dry else
                     f"Der Fortschritt ist gespeichert – beim nächsten Start geht es bei "
                     f"Scheibe {self.current_index + 1} weiter.")
            if not messagebox.askyesno("Druck beenden?", f"Druck jetzt beenden?\n\n{where}",
                                       parent=self.root):
                return
            self.quit_requested = True
            self.quit_btn.state(["disabled"])
            self._set_banner("Druck wird beendet …", "warn")
        elif self.repeat_btn.instate(["disabled"]):
            return  # Abbruch laeuft schon - erst die naechste Scheibe abwarten
        else:
            self._set_banner("Breche den Druck ab …", "warn",
                             "Die Scheibe wird ausgeworfen.")
        self.repeat_btn.state(["disabled"])
        self.back_btn.state(["disabled"])
        self.run.request(key)

    def _update_progress_label(self, done: int, retries: int) -> None:
        text = f"{done} von {len(self.plan)} Scheiben erledigt"
        if retries:
            text += f" · {retries} Wiederholung{'en' if retries != 1 else ''}"
        self.progress_label.configure(text=text)

    def _poll_events(self) -> None:
        try:
            while True:
                try:
                    kind, data = self.events.get_nowait()
                except queue.Empty:
                    break
                getattr(self, f"_ev_{kind}")(data)
        finally:
            self.root.after(self.POLL_MS, self._poll_events)

    def _ev_status(self, data: dict) -> None:
        self._set_banner(data["text"], "info")

    def _ev_log(self, data: dict) -> None:
        self._log(data["text"])

    def _ev_sheet(self, data: dict) -> None:
        idx = data["index"]
        self.current_index = idx
        self.run_retries = data["retries"]
        row = self.plan[idx]
        prev = self.plan[idx - 1] if idx > 0 else None
        detail = (f"Scheibe {idx + 1} von {len(self.plan)}: {self.sheet_desc(idx)}\n"
                  f"Scheibe einlegen – Druck und Auswurf laufen automatisch.")
        if prev is None or prev["stand"] != row["stand"]:
            if prev is None or prev["verein"] != row["verein"]:
                self._set_banner(f"NEUER STAND {row['stand']}  –  {row['verein']}", "verein", detail)
            else:
                self._set_banner(f"NEUER STAND {row['stand']}", "stand", detail)
        else:
            self._set_banner(f"Scheibe {idx + 1} einlegen", "info", detail)
        if not self.quit_requested:
            self.repeat_btn.state(["!disabled"])
            self.back_btn.state(["!disabled"])
        self._show_preview(idx)
        self._update_tree_status()
        self._update_progress_label(idx, data["retries"])

    def _ev_advanced(self, data: dict) -> None:
        self.current_index = data["index"]
        self.progress.configure(value=data["index"])
        self._update_progress_label(data["index"], self.run_retries)
        self._update_tree_status()

    def _ev_ask_back(self, data: dict) -> None:
        self.pending_reply = data["reply"]
        self._set_banner("Scheibe ausgeworfen", "warn", "Wie weit soll zurückgesprungen werden?")
        n = 1
        try:
            n = BackJumpDialog(self, data["index"]).result
        finally:
            self.pending_reply = None
            data["reply"].put(n)

    def _ev_finished(self, result: dict) -> None:
        dry = self.run_dry
        self._end_run()
        prefix = "Testmodus: " if dry else ""
        done_text = (f"{result['printed']} Scheibe(n) gedruckt, {result['retries']} Wiederholung(en).")
        if result["complete"]:
            self._set_banner(f"{prefix}Fertig – alle {result['total']} Scheiben gedruckt", "ok",
                             done_text)
        else:
            nxt = f"Beim nächsten Start geht es bei Scheibe {result['index'] + 1} weiter." if not dry else ""
            self._set_banner(f"{prefix}Druck beendet bei Scheibe {result['index'] + 1} von "
                             f"{result['total']}", "warn", f"{done_text} {nxt}".strip())
        self._log(f"Druck beendet: {done_text} Stand: {result['index']}/{result['total']}.")

    def _ev_failed(self, data: dict) -> None:
        self._end_run()
        self._set_banner("Fehler beim Drucken", "error", data["text"])
        self._log(f"FEHLER: {data['text']}")
        messagebox.showerror(
            "Fehler beim Drucken",
            f"{data['text']}\n\nBitte COM-Port, Kabel und Drucker prüfen. Der Fortschritt bis zur "
            f"letzten fertigen Scheibe ist gespeichert.", parent=self.root)

    def _end_run(self) -> None:
        self.run = None
        self.worker = None
        self.quit_requested = False
        self._set_running(False)
        self.saved = load_plan_state(state_path_for(self.config_path, self.profile_name))
        self._refresh()

    # ------------------------------------------------------------- Allgemein

    def _set_banner(self, title: str, kind: str, sub: str = "") -> None:
        bg, fg = BANNER_STYLES[kind]
        self.banner.configure(bg=bg)
        self.banner_title.configure(text=title, bg=bg, fg=fg)
        self.banner_sub.configure(text=sub, bg=bg, fg=fg)

    def _log(self, text: str) -> None:
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_key(self, event) -> None:
        if self.run is not None and event.char and event.char.lower() in ("w", "b", "q"):
            self._control(event.char.lower())

    def _on_close(self) -> None:
        if self.pending_reply is not None:
            self.root.bell()
            return
        if self.run is not None:
            if not messagebox.askyesno("Druck läuft", "Es wird gerade gedruckt.\n"
                                       "Druck beenden und Programm schließen?", parent=self.root):
                return
            self.run.request("q")
            if self.worker is not None:
                self.worker.join(timeout=5)  # Port sauber schliessen lassen
        self._save_settings()
        self.root.destroy()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", help="COM-Port vorbelegen, z.B. COM5")
    parser.add_argument("template", nargs="?", help="Vorlage vorbelegen, z.B. templates\\lp_paarung.txt")
    parser.add_argument("--profile", help="Profil aus config.ini vorbelegen (z.B. LP oder LG)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Pfad zur config.ini")
    parser.add_argument("--dry-run", action="store_true", help="Im Testmodus (ohne Drucker) starten")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()

    def report_error(exc, val, tb):
        messagebox.showerror("Unerwarteter Fehler",
                             "".join(traceback.format_exception(exc, val, tb))[-3000:], parent=root)
    root.report_callback_exception = report_error

    config_path = Path(args.config)
    cp = configparser.ConfigParser()
    if not cp.read(config_path, encoding="utf-8"):
        messagebox.showerror("config.ini nicht gefunden",
                             f"Config-Datei nicht gefunden:\n{config_path}\n\nconfig.ini, templates\\ "
                             f"und state\\ müssen im selben Ordner wie das Programm liegen.")
        return 1
    if not any(s.startswith("profile:") for s in cp.sections()):
        messagebox.showerror("Kein Profil", f"In {config_path} ist kein [profile:...]-Abschnitt definiert.")
        return 1

    App(root, config_path, cp, args)
    root.update_idletasks()
    width, height = root.winfo_reqwidth(), root.winfo_reqheight()
    root.minsize(min(width, root.winfo_screenwidth()), min(height, root.winfo_screenheight()))
    if width > root.winfo_screenwidth() - 40 or height > root.winfo_screenheight() - 80:
        root.state("zoomed")  # kleiner Bildschirm (z.B. Laptop am Stand): gleich maximieren
    root.deiconify()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
