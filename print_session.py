"""
Interaktive Drucksession fuer den Schiessstand-Betrieb - VOLLAUTOMATISCH,
mit Paarungs-/Vereins-Wechsel (Wettkampf zweier Vereine).

Erzeugt die komplette Druckreihenfolge selbst (siehe matchplan.py, ein
Python-Nachbau der bisherigen Excel-Listen "1_Schuss"/"2_Schuss" aus
WM_LIGA_Transfer.xlsm) und druckt sie automatisch Scheibe fuer Scheibe:
  - Stand: fortlaufende Standnummer ueber BEIDE Vereine hinweg (wechselt
    automatisch bei jedem neuen Schuetzen: Stand 1 = Verein-A-Schuetze 1,
    Stand 2 = Verein-B-Schuetze 1, Stand 3 = Verein-A-Schuetze 2, ...)
  - Serie: 1..N je Stand (Standard 4)
  - Schuss: 1..M je Serie (Standard 10), als Bereich ("1-2") bei
    shots_per_sheet > 1
  - Verein/Name (Paarungs-Label) wechseln zusammen mit dem Stand

Start jeder Scheibe automatisch (kein "Enter zum Drucken" noetig), Ende
eines Einlege-/Druck-/Auswurfzyklus wird automatisch erkannt (siehe
tmu950.py: calibrate_slip_baseline/wait_for_slip_cycle) - der Bediener
muss also nichts am PC bestaetigen. Waehrend des Wartens optional (nicht
blockierend, nur Windows):
    w   laufenden Druck sofort abbrechen, Scheibe auswerfen und DIESELBE
        Scheibe erneut drucken (bei einem Fehldruck)
    b   laufenden Druck sofort abbrechen, Scheibe auswerfen und mehrere
        Scheiben zurueckspringen (fragt interaktiv, wie viele)
    q   Session beenden (der aktuelle Druck laeuft normal zu Ende)
"Abbrechen" (w/b) heisst konkret: ESC @ verwirft, was der Drucker schon
empfangen aber noch nicht gedruckt hat (siehe SlipPrinter.cancel_and_eject in
session_engine.py) - bereits physisch gedruckte Zeilen bleiben aber auf
dem Papier stehen, daher lohnt sich schnelles Reagieren.
Der aktuelle Fortschritt (welche Scheibe als naechstes drankommt) wird
gespeichert, damit ein Abbruch/Neustart an der richtigen Stelle
weitermacht.

Verwendung (Vereine/Paarungen per Kommandozeile):
    python print_session.py COM5 templates\\lp_paarung.txt ^
        --club-a Niederrieden --club-b Salgen --paarungen 5

Ohne --club-a/--club-b/--paarungen: fragt interaktiv danach (mit den
Werten aus dem letzten Lauf als Vorschlag).

    python print_session.py COM5 templates\\lp_paarung.txt --dry-run   (ohne Drucker testen)
"""
import argparse
import configparser
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table

from printjob import load_profile, render
from matchplan import generate_plan, plan_fingerprint, plan_summary, plan_condensed
from session_engine import (
    APP_DIR,
    DEFAULT_CONFIG,
    PLAN_KEYS,
    SlipPrinter,
    Timing,
    load_plan_state,
    print_row_for,
    save_plan_state,
    state_path_for,
)

try:
    import msvcrt
    HAS_MSVCRT = True
except ImportError:
    msvcrt = None
    HAS_MSVCRT = False


def poll_hotkey():
    """Nicht-blockierende Tastaturabfrage (nur Windows). Gibt 'w', 'q', 'b' oder None."""
    if not HAS_MSVCRT or not msvcrt.kbhit():
        return None
    try:
        ch = msvcrt.getch().decode("utf-8", errors="ignore").lower()
    except Exception:
        return None
    return ch if ch in ("w", "q", "b") else None


def progress_bar(done: int, total: int, width: int = 24) -> str:
    if not total:
        return f"Scheibe {done}"
    filled = min(width, int(width * done / total))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {done}/{total}"


def ask_plan_config_simple(console: Console, saved: dict) -> dict:
    """Einfache Tipp-und-Enter-Abfrage (Fallback, falls prompt_toolkit fehlt
    oder --simple-input gesetzt ist)."""
    console.print(Panel(
        "Wettkampf-Einstellungen ([Enter] uebernimmt den vorgeschlagenen Wert)",
        style="cyan",
    ))
    club_a = Prompt.ask("Verein A", default=saved.get("club_a", "Verein A"))
    club_b = Prompt.ask("Verein B", default=saved.get("club_b", "Verein B"))
    num_paarungen = IntPrompt.ask("Anzahl Paarungen (Schuetzen-Paare)",
                                   default=saved.get("num_paarungen", 5))
    start_club = Prompt.ask(f"Wer beginnt an Stand 1: {club_a} oder {club_b}?",
                             choices=[club_a, club_b], default=saved.get("start_club_name", club_a))
    start_club_code = "A" if start_club == club_a else "B"
    shots_per_sheet = IntPrompt.ask("Schuss pro Scheibe (1 oder 2)",
                                     default=saved.get("shots_per_sheet", 2), choices=["1", "2"])
    series_count = IntPrompt.ask("Serien je Stand", default=saved.get("series_count", 4))
    shots_per_serie = IntPrompt.ask("Schuss je Serie", default=saved.get("shots_per_serie", 10))
    return {
        "club_a": club_a, "club_b": club_b, "num_paarungen": num_paarungen,
        "start_club": start_club_code, "start_club_name": start_club,
        "shots_per_sheet": shots_per_sheet, "series_count": series_count,
        "shots_per_serie": shots_per_serie,
    }


def run_button_dialog(title, text, buttons):
    """
    Nachbau von prompt_toolkit.shortcuts.button_dialog(), aber mit an die
    Labels angepasster Button-Breite. button_dialog() verwendet fest
    width=12 pro Button (Standardwert der Button-Klasse) - laengere
    Vereinsnamen wie "Niederrieden" (12 Zeichen) wurden dadurch am rechten
    Rand abgeschnitten dargestellt ("< Salgen > <Niederriede" ohne
    schliessendes ">"). Hier wird die Breite stattdessen anhand der
    laengsten Beschriftung berechnet, damit auch lange Vereinsnamen
    vollstaendig sichtbar sind.
    """
    import functools
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding.defaults import load_key_bindings
    from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
    from prompt_toolkit.key_binding.key_bindings import KeyBindings, merge_key_bindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.widgets import Dialog, Button, Label

    app_box = {}

    def button_handler(v):
        app_box["app"].exit(result=v)

    max_label = max((len(str(t)) for t, _ in buttons), default=0)
    btn_width = max_label + 4  # Platz fuer die "<"/">" -Symbole plus etwas Luft

    btn_objs = [Button(text=str(t), handler=functools.partial(button_handler, v), width=btn_width)
                for t, v in buttons]

    dialog = Dialog(title=title, body=Label(text=text, dont_extend_height=True),
                     buttons=btn_objs, with_background=True)

    bindings = KeyBindings()
    bindings.add("tab")(focus_next)
    bindings.add("s-tab")(focus_previous)

    app = Application(
        layout=Layout(dialog),
        key_bindings=merge_key_bindings([load_key_bindings(), bindings]),
        mouse_support=True,
        full_screen=True,
    )
    app_box["app"] = app
    return app.run()


def ask_plan_config_tui(saved: dict) -> dict:
    """
    Vollbild-Eingabemaske mit Pfeiltasten-/Tab-Navigation (prompt_toolkit) -
    fuer den Betrieb durch Laien deutlich einfacher als Kommandozeilen-
    Optionen: pro Einstellung ein eigener Dialog, mit [Tab]/Pfeiltasten
    zwischen Feldern/Optionen, [Enter] bestaetigt, [Esc] bricht ab.
    """
    from prompt_toolkit.shortcuts import input_dialog, message_dialog

    def cancelled():
        message_dialog(title="Abgebrochen", text="Eingabe abgebrochen - Programm wird beendet.").run()
        raise SystemExit(0)

    def ask_text(title, text, default):
        result = input_dialog(title=title, text=text, default=str(default)).run()
        if result is None:
            cancelled()
        return result

    def ask_int(title, text, default):
        while True:
            result = ask_text(title, text, str(default))
            try:
                return int(result)
            except ValueError:
                message_dialog(title="Ungueltige Eingabe",
                                text=f"'{result}' ist keine ganze Zahl - bitte erneut eingeben.").run()

    def ask_button(title, text, buttons):
        # run_button_dialog() statt prompt_toolkit's button_dialog(): der
        # Fokus startet sofort auf dem ersten Button (Pfeiltasten/Tab
        # funktionieren direkt), UND die Button-Breite passt sich den
        # Beschriftungen an (keine abgeschnittenen Vereinsnamen mehr).
        result = run_button_dialog(title, text, buttons)
        if result is None:
            cancelled()
        return result

    club_a = ask_text("Verein A", "Name von Verein A:", saved.get("club_a", "Verein A"))
    club_b = ask_text("Verein B", "Name von Verein B:", saved.get("club_b", "Verein B"))
    num_paarungen = ask_int("Anzahl Paarungen", "Anzahl Paarungen (Schuetzen-Paare):",
                             saved.get("num_paarungen", 5))

    start_buttons = [(club_a, "A"), (club_b, "B")]
    if saved.get("start_club") == "B":
        start_buttons.reverse()
    start_club = ask_button("Wer beginnt?", "Wer beginnt an Stand 1?\n(Pfeiltasten/Tab waehlen, Enter bestaetigt)",
                             start_buttons)

    sheet_buttons = [("1 Schuss pro Scheibe", 1), ("2 Schuss pro Scheibe", 2)]
    if saved.get("shots_per_sheet") == 1:
        sheet_buttons.reverse()
    shots_per_sheet = ask_button("Schuss pro Scheibe", "Wie viele Schuss pro Scheibe?", sheet_buttons)

    series_count = ask_int("Serien je Stand", "Serien je Stand:", saved.get("series_count", 4))
    shots_per_serie = ask_int("Schuss je Serie", "Schuss je Serie:", saved.get("shots_per_serie", 10))

    return {
        "club_a": club_a, "club_b": club_b, "num_paarungen": num_paarungen,
        "start_club": start_club, "shots_per_sheet": shots_per_sheet,
        "series_count": series_count, "shots_per_serie": shots_per_serie,
    }


def ask_resume_unfinished(console: Console, saved: dict, prev_index: int, total: int,
                           simple: bool = False) -> bool:
    """
    Wird eine unvollstaendige gespeicherte Session gefunden, wird ZUERST
    gefragt, ob genau dort fortgesetzt werden soll - OHNE die Einstellungen
    (Vereine, Anzahl Paarungen, ...) erneut ueber die Eingabemasken
    abzufragen. Nur bei "Neu einrichten" (oder wenn nichts Unfertiges
    gefunden wird) kommt die normale Konfigurationsabfrage.
    """
    club_a = saved.get("club_a", "?")
    club_b = saved.get("club_b", "?")
    msg = (f"Unterbrochene Session gefunden:\n"
           f"{club_a} vs. {club_b}, Paarungen: {saved.get('num_paarungen', '?')}\n"
           f"Fortschritt: Scheibe {prev_index + 1}/{total}\n\n"
           f"Genau dort fortsetzen (ohne die Einstellungen neu einzugeben)?")
    if not simple:
        try:
            result = run_button_dialog(
                "Unterbrochene Session fortsetzen?", msg,
                [("Fortsetzen", True), ("Neu einrichten", False)],
            )
            return bool(result)
        except ImportError:
            pass
    console.print(Panel(msg, style="cyan"))
    return Confirm.ask("Fortsetzen?", default=True)


def ask_plan_config(console: Console, saved: dict, force_simple: bool = False) -> dict:
    if not force_simple:
        try:
            return ask_plan_config_tui(saved)
        except ImportError:
            console.print("[yellow]prompt_toolkit nicht installiert - nutze einfache Text-Abfrage "
                           "(fuer Pfeiltasten-Navigation: pip install prompt_toolkit).[/yellow]")
    return ask_plan_config_simple(console, saved)


def run_sheet_preview(plan: list, template_lines: list, line_width: int,
                      start_pos: int = 0) -> Optional[int]:
    """
    Vollbild-Scheibe-fuer-Scheibe-Vorschau (prompt_toolkit): zeigt fuer jede
    Scheibe GENAU den Text, der spaeter gedruckt wird, in einem Rahmen (wie
    auf dem Papier). Navigation:
        Pfeiltasten/Bild-Auf/-Ab/[j][k]  eine Scheibe vor/zurueck
        Pos1 / Ende                       zur ersten/letzten Scheibe
        Zahl(en) + [Enter]                direkt zu dieser Scheibennummer springen
        [Enter] (ohne Zahl) / [Esc] / [q] Vorschau schliessen, OHNE zu drucken
        [s]                                Vorschau schliessen und DRUCK AB
                                           DIESER (aktuell angezeigten) Scheibe
                                           starten
    Gibt bei [s] den 0-basierten Index der gewaehlten Scheibe zurueck, sonst
    None (dann aendert sich am Fortschritt nichts - siehe main()/Confirm.ask
    danach, wie bisher).
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    total = len(plan)
    state = {"pos": max(0, min(total - 1, start_pos)), "goto": ""}

    def clamp() -> None:
        state["pos"] = max(0, min(total - 1, state["pos"]))

    def get_body_text() -> str:
        idx = state["pos"]
        card_lines = render(template_lines, print_row_for(plan, idx), line_width).split("\n")
        width = max(line_width, max((len(l) for l in card_lines), default=0)) + 4
        border = "+" + "-" * (width - 2) + "+"
        out = [border]
        out.extend("| " + l.ljust(width - 4) + " |" for l in card_lines)
        out.append(border)
        return "\n".join(out)

    def get_status_text() -> str:
        idx = state["pos"]
        row = plan[idx]
        goto_hint = f"   Sprung zu Scheibe: {state['goto']}_" if state["goto"] else ""
        return (f" Scheibe {idx + 1}/{total}   Stand {row['stand']}   Serie {row['serie']}   "
                f"Schuss {row['schuss']}   Verein: {row['verein']}{goto_hint} ")

    kb = KeyBindings()

    @kb.add("left")
    @kb.add("up")
    @kb.add("pageup")
    @kb.add("k")
    def _(event) -> None:
        state["goto"] = ""
        state["pos"] -= 1
        clamp()

    @kb.add("right")
    @kb.add("down")
    @kb.add("pagedown")
    @kb.add("j")
    @kb.add(" ")
    def _(event) -> None:
        state["goto"] = ""
        state["pos"] += 1
        clamp()

    @kb.add("home")
    def _(event) -> None:
        state["goto"] = ""
        state["pos"] = 0

    @kb.add("end")
    def _(event) -> None:
        state["goto"] = ""
        state["pos"] = total - 1

    for _digit in "0123456789":
        @kb.add(_digit)
        def _(event, _digit=_digit) -> None:
            state["goto"] += _digit

    @kb.add("backspace")
    def _(event) -> None:
        state["goto"] = state["goto"][:-1]

    @kb.add("enter")
    def _(event) -> None:
        if state["goto"]:
            try:
                state["pos"] = int(state["goto"]) - 1
            except ValueError:
                pass
            state["goto"] = ""
            clamp()
        else:
            # [Enter] ohne eingegebene Zahl: Vorschau einfach schliessen (wie
            # [Esc]/[q]) - die naheliegendste Taste, wenn man "fertig
            # geschaut" hat.
            event.app.exit(result=None)

    @kb.add("s")
    def _(event) -> None:
        event.app.exit(result=state["pos"])

    @kb.add("escape")
    @kb.add("q")
    @kb.add("c-c")
    def _(event) -> None:
        event.app.exit(result=None)

    root = HSplit([
        Window(FormattedTextControl(
            " Scheibe-fuer-Scheibe-Vorschau  -  Pfeiltasten/Bild-Auf/-Ab: blaettern   "
            "Zahl+Enter: springen   Pos1/Ende: Anfang/Ende  |  "
            "[s]=DRUCK AB HIER STARTEN   [Enter]/[Esc]/[q]=schliessen "),
            height=1, style="reverse", dont_extend_height=True),
        Window(FormattedTextControl(get_body_text), align="center"),
        Window(FormattedTextControl(get_status_text), height=1, style="reverse",
               dont_extend_height=True),
    ])

    return Application(layout=Layout(root), key_bindings=kb, full_screen=True).run()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", help="COM-Port, z.B. COM5")
    parser.add_argument("template", help="Pfad zur Template-Textdatei")
    parser.add_argument("--profile", default="LP", help="Profilname aus config.ini (Default: LP)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Pfad zur config.ini")

    parser.add_argument("--club-a", help="Name Verein A (ohne Angabe: interaktive Abfrage)")
    parser.add_argument("--club-b", help="Name Verein B (ohne Angabe: interaktive Abfrage)")
    parser.add_argument("--paarungen", type=int, help="Anzahl Paarungen (Schuetzen-Paare)")
    parser.add_argument("--start-club", choices=["A", "B"], default=None,
                         help="Welcher Verein beginnt an Stand 1 (A oder B)")
    parser.add_argument("--series-count", type=int, default=None, help="Serien je Stand (Default: 4)")
    parser.add_argument("--shots-per-serie", type=int, default=None, help="Schuss je Serie (Default: 10)")
    parser.add_argument("--shots-per-sheet", type=int, default=None,
                         help="Ueberschreibt shots_per_sheet aus config.ini fuer diesen Lauf")

    parser.add_argument("--start-index", type=int, default=None,
                         help="An dieser Scheibe (1-basiert) starten/fortsetzen, statt dem "
                              "gespeicherten Fortschritt zu folgen")
    parser.add_argument("--no-save", action="store_true", help="Fortschritt NICHT speichern (zum Testen)")
    parser.add_argument("--simple-input", action="store_true",
                         help="Einfache Tipp-und-Enter-Abfrage statt der Pfeiltasten-Eingabemaske "
                              "(prompt_toolkit) verwenden")
    parser.add_argument("--no-preview", action="store_true",
                         help="Keine Scheibe-fuer-Scheibe-Vorschau vor dem Druck anbieten")
    parser.add_argument("--dry-run", action="store_true",
                         help="Ohne Drucker durchspielen (zeigt nur an, was gedruckt wuerde)")

    parser.add_argument("--pause", type=float, default=Timing.pause,
                         help="Sekunden Pause nach erkanntem Zyklus-Ende (Default: 0.3)")
    parser.add_argument("--max-wait", type=float, default=Timing.max_wait,
                         help="Maximale Wartezeit pro Scheibe auf das automatische Fertig-Signal, "
                              "danach wird trotzdem weitergemacht. Ohne Angabe: UNBEGRENZT warten "
                              "(kein Timeout) - der Bediener bestimmt durchs Einlegen selbst das Tempo.")
    parser.add_argument("--poll-interval", type=float, default=Timing.poll_interval,
                         help="Sekunden zwischen den Status-Abfragen waehrend des Wartens (Default: 0.15)")
    parser.add_argument("--poll-timeout", type=float, default=Timing.poll_timeout,
                         help="Timeout je Status-Abfrage in Sekunden (Default: 0.2)")
    parser.add_argument("--min-wait", type=float, default=Timing.min_wait,
                         help="Mindestwartezeit vor Akzeptanz des Fertig-Signals (Default: 1.0)")
    parser.add_argument("--calib-time", type=float, default=Timing.calib_time,
                         help="Sekunden zur Kalibrierung des Ruhezustands vor der ersten Scheibe "
                              "(Default: 1.5)")
    parser.add_argument("--debug-status", action="store_true",
                         help="Zeigt waehrend des Wartens jede rohe GS-r-3-Antwort an")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    console = Console()

    config_path = Path(args.config)
    cp = configparser.ConfigParser()
    if not cp.read(config_path, encoding="utf-8"):
        console.print(f"[bold red]Config-Datei nicht gefunden:[/bold red] {config_path}")
        raise SystemExit(1)

    profile = load_profile(args.profile, cp, config_path)
    shots_per_sheet_override = args.shots_per_sheet

    template_path = Path(args.template)
    if not template_path.exists() and not template_path.is_absolute():
        # Fallback fuer die .exe: falls der relative Pfad (z.B. von einer
        # Verknuepfung mit anderem "Ausfuehren in") nicht direkt gefunden
        # wird, zusaetzlich relativ zum Programmordner (APP_DIR) suchen.
        alt = APP_DIR / template_path
        if alt.exists():
            template_path = alt
    if not template_path.exists():
        console.print(f"[bold red]Template nicht gefunden:[/bold red] {template_path}")
        raise SystemExit(1)
    template_lines = template_path.read_text(encoding="utf-8").splitlines()

    state_path = state_path_for(config_path, args.profile)
    saved = load_plan_state(state_path)

    have_all_cli = all([args.club_a, args.club_b, args.paarungen])
    config_ready = False

    if have_all_cli:
        club_a, club_b = args.club_a, args.club_b
        num_paarungen = args.paarungen
        start_club = args.start_club or saved.get("start_club", "A")
        series_count = args.series_count or saved.get("series_count", 4)
        shots_per_serie = args.shots_per_serie or saved.get("shots_per_serie", 10)
        shots_per_sheet = shots_per_sheet_override or saved.get("shots_per_sheet") or profile["shots_per_sheet"]
        config_ready = True

    elif args.start_index is None and saved and all(k in saved for k in PLAN_KEYS):
        # Unfertige gespeicherte Session? Erst fragen, ob GENAU DORT
        # fortgesetzt werden soll - ohne die Einstellungen neu abzufragen
        # (siehe ask_resume_unfinished). So wird ein nicht fertig
        # gedruckter Stand beim naechsten Start automatisch zu Ende
        # gebracht, statt eine neue Konfiguration zu erzwingen.
        prev_plan = generate_plan(
            saved["club_a"], saved["club_b"], saved["num_paarungen"],
            shots_per_sheet=saved["shots_per_sheet"], start_club=saved["start_club"],
            series_count=saved["series_count"], shots_per_serie=saved["shots_per_serie"],
        )
        prev_index = saved.get("index", 0)
        if 0 < prev_index < len(prev_plan):
            if ask_resume_unfinished(console, saved, prev_index, len(prev_plan), args.simple_input):
                club_a, club_b = saved["club_a"], saved["club_b"]
                num_paarungen = saved["num_paarungen"]
                start_club = saved["start_club"]
                series_count = saved["series_count"]
                shots_per_serie = saved["shots_per_serie"]
                shots_per_sheet = shots_per_sheet_override or saved["shots_per_sheet"]
                config_ready = True

    if not config_ready:
        cfg_in = ask_plan_config(console, saved, force_simple=args.simple_input)
        club_a, club_b = cfg_in["club_a"], cfg_in["club_b"]
        num_paarungen = cfg_in["num_paarungen"]
        start_club = cfg_in["start_club"]
        series_count = cfg_in["series_count"]
        shots_per_serie = cfg_in["shots_per_serie"]
        shots_per_sheet = shots_per_sheet_override or cfg_in["shots_per_sheet"]

    fingerprint = plan_fingerprint(club_a, club_b, num_paarungen, shots_per_sheet,
                                    start_club, series_count, shots_per_serie)

    plan = generate_plan(club_a, club_b, num_paarungen, shots_per_sheet=shots_per_sheet,
                          start_club=start_club, series_count=series_count,
                          shots_per_serie=shots_per_serie)

    if args.start_index is not None:
        index = max(0, args.start_index - 1)
    elif saved.get("fingerprint") == fingerprint:
        index = saved.get("index", 0)
    else:
        if saved:
            console.print("[yellow]Einstellungen haben sich gegenueber dem letzten Lauf geaendert - "
                           "starte den Fortschritt neu bei Scheibe 1.[/yellow]")
        index = 0

    console.print(Panel(
        f"Verein A: [bold]{club_a}[/bold]   Verein B: [bold]{club_b}[/bold]   "
        f"Paarungen: [bold]{num_paarungen}[/bold]   Beginnt: [bold]"
        f"{club_a if start_club == 'A' else club_b}[/bold]\n"
        f"Serien/Stand: {series_count}   Schuss/Serie: {shots_per_serie}   "
        f"Schuss/Scheibe: {shots_per_sheet}   {plan_summary(plan)}"
        + (f"\nFortsetzung ab Scheibe {index + 1}/{len(plan)}" if 0 < index < len(plan) else ""),
        title="TM-U950 Drucksession (automatisch, Paarungen)", style="cyan",
    ))
    if HAS_MSVCRT and not args.dry_run:
        console.print(Panel(
            "Waehrend des automatischen Wartens auf Druck/Auswurf jederzeit "
            "druckbar (kein Enter noetig):\n"
            "  [bold]w[/bold]  = diese Scheibe wiederholen (Fehldruck) - "
            "bricht den laufenden Druckauftrag sofort ab, wirft die Scheibe "
            "aus und druckt dieselbe Scheibe erneut\n"
            "  [bold]b[/bold]  = mehrere Scheiben zurueckspringen (fragt, wie "
            "viele) - bricht den laufenden Druckauftrag ebenfalls sofort ab\n"
            "  [bold]q[/bold]  = Session beenden\n"
            "Ohne Tastendruck laeuft alles automatisch weiter, sobald die "
            "Scheibe eingelegt/gedruckt/ausgeworfen wurde.",
            title="Tasten waehrend des Druckens", style="cyan",
        ))
    elif not HAS_MSVCRT:
        console.print("[dim](Hinweis: 'w'/'b'/'q'-Tasten nur unter Windows verfuegbar.)[/dim]")

    if index >= len(plan):
        console.print(Panel(
            f"Dieser Plan wurde bereits vollstaendig gedruckt ({len(plan)}/{len(plan)} Scheiben) - "
            f"{plan_summary(plan)}.",
            style="green",
        ))
        if Confirm.ask("Trotzdem komplett von Scheibe 1 an neu drucken?", default=False):
            index = 0
        else:
            console.print("[dim]Nichts zu tun. Fuer eine einzelne Scheibe: --start-index N. "
                           "Fuer ein anderes Paar/andere Einstellungen: einfach andere Werte "
                           "eingeben (bzw. ohne --club-a/--club-b/--paarungen starten).[/dim]")
            return

    table = Table(title="Vorschau: Stand-fuer-Stand-Uebersicht (zur Kontrolle vor dem Druck)")
    table.add_column("#", justify="right")
    table.add_column("Stand", justify="right")
    table.add_column("Verein")
    table.add_column("Label")
    table.add_column("Scheiben", justify="right")
    table.add_column("Schuss-Bereich")
    table.add_column("Status")
    for pos, c in enumerate(plan_condensed(plan), start=1):
        if c["last_index"] < index:
            status = "[dim]erledigt[/dim]"
        elif c["first_index"] <= index <= c["last_index"]:
            status = "[bold yellow]-> aktuell[/bold yellow]"
        else:
            status = "offen"
        table.add_row(str(pos), str(c["stand"]), c["verein"], c["name"],
                       str(c["sheets"]), c["schuss_range"], status)
    console.print(table)

    if not args.no_preview and not args.simple_input:
        try:
            if Confirm.ask("Scheibe-fuer-Scheibe-Vorschau ansehen (mit Pfeiltasten durchblaettern)?",
                            default=True):
                start_from = run_sheet_preview(plan, template_lines, profile["line_width"],
                                                start_pos=index)
                if start_from is not None:
                    index = start_from
                    console.print(f"[yellow]Aus der Vorschau: Druck beginnt ab Scheibe "
                                   f"{index + 1}/{len(plan)}.[/yellow]")
        except ImportError:
            console.print("[yellow]prompt_toolkit nicht installiert - keine Vorschau moeglich "
                           "(pip install prompt_toolkit).[/yellow]")

    if not Confirm.ask("Passt diese Reihenfolge? Jetzt starten?", default=True):
        console.print("[yellow]Abgebrochen - nichts gedruckt. Mit anderen Einstellungen erneut starten.[/yellow]")
        return

    printer = None
    if not args.dry_run:
        printer = SlipPrinter(args.port, profile, Timing(
            pause=args.pause, max_wait=args.max_wait, poll_interval=args.poll_interval,
            poll_timeout=args.poll_timeout, min_wait=args.min_wait, calib_time=args.calib_time,
        ))
        printer.open()

        with console.status("[bold cyan]Kalibriere Ruhezustand der Slip-Station "
                             "(bitte jetzt nichts einlegen) ...[/bold cyan]", spinner="dots"):
            baseline_b3 = printer.calibrate()
        console.print(f"[dim]Ruhezustand kalibriert (Referenzwert 0x{baseline_b3:02X}).[/dim]")
    else:
        console.print("[dim](--dry-run: kein Drucker wird angesprochen)[/dim]")

    def _progress_dict(idx: int) -> dict:
        return {
            "index": idx, "fingerprint": fingerprint,
            "club_a": club_a, "club_b": club_b, "num_paarungen": num_paarungen,
            "start_club": start_club, "series_count": series_count,
            "shots_per_serie": shots_per_serie, "shots_per_sheet": shots_per_sheet,
        }

    printed = 0
    retries_total = 0
    quit_requested = False
    previous_row = plan[index - 1] if index > 0 else None

    try:
        while index < len(plan) and not quit_requested:
            row = plan[index]

            new_stand = previous_row is None or row["stand"] != previous_row["stand"]
            new_verein = previous_row is None or row["verein"] != previous_row["verein"]

            # print_row_for() sorgt dafuer, dass der Vereinsname nur auf der
            # ERSTEN Scheibe jeder Serie steht (danach leer, bis zur naechsten
            # Serie/zum naechsten Stand) - 'row' selbst bleibt unveraendert
            # (wird u.a. fuer die Info-Anzeige/Tabelle gebraucht, wo der
            # Vereinsname immer angezeigt werden soll). Dieselbe Funktion
            # baut auch die Scheibe-fuer-Scheibe-Vorschau (run_sheet_preview),
            # damit Vorschau und Druck identisch sind.
            text = render(template_lines, print_row_for(plan, index), profile["line_width"])

            console.rule(f"[bold]{progress_bar(index + 1, len(plan))}[/bold]")
            if new_stand:
                style = "bold black on yellow" if new_verein else "bold black on cyan"
                extra = f" - VEREIN: {row['verein']}" if new_verein else ""
                console.print(Panel(
                    f"[{style}] NEUER STAND {row['stand']}{extra} [/]",
                    expand=False,
                ))

            info = (f"[bold cyan]Stand:[/bold cyan] {row['stand']}   "
                    f"[bold cyan]Serie:[/bold cyan] {row['serie']}   "
                    f"[bold cyan]Schuss:[/bold cyan] {row['schuss']}   "
                    f"[bold cyan]Verein:[/bold cyan] {row['verein']}")
            console.print(Panel(text, title=info,
                                 subtitle=f"Scheibe {index + 1}/{len(plan)} | Wiederholungen: {retries_total}"))

            retry_this_sheet = False

            if not args.dry_run:
                printer.send_sheet(text)

                def _on_tick(resp: bytes, _console=console):
                    if args.debug_status and resp:
                        h = " ".join(f"{b:02X}" for b in resp)
                        _console.print(f"[dim]    GS r 3: {h}[/dim]")
                    return poll_hotkey()

                with console.status(
                    "[bold cyan]Scheibe einlegen - Druck/Auswurf laeuft automatisch "
                    "(\\[w]=wiederholen  \\[b]=zurueckspringen  \\[q]=Ende, sonst "
                    "nichts noetig) ...[/bold cyan]",
                    spinner="dots",
                ):
                    status, elapsed = printer.wait_cycle(on_tick=_on_tick)

                if status == "done":
                    console.print(f"[dim]  Zyklus erkannt (nach {elapsed:.1f}s) - weiter.[/dim]")
                elif status == "timeout":
                    console.print(
                        f"[yellow]Kein eindeutiges Fertig-Signal innerhalb {args.max_wait:.0f}s "
                        f"erhalten - mache trotzdem weiter.[/yellow]"
                    )
                elif status == "hotkey:q":
                    quit_requested = True
                    break
                elif status == "hotkey:w":
                    console.print("[yellow]Breche laufenden Druck ab, werfe die Scheibe aus und "
                                   "wiederhole dieselbe Scheibe ...[/yellow]")
                    printer.cancel_and_eject()
                    retries_total += 1
                    retry_this_sheet = True
                elif status == "hotkey:b":
                    console.print("[yellow]Breche laufenden Druck ab und werfe die Scheibe aus ...[/yellow]")
                    printer.cancel_and_eject()
                    n = IntPrompt.ask(
                        "Um wie viele Scheiben zurueckspringen? "
                        "(1 = nur diese Scheibe erneut, 2 = auch die davor, ...)",
                        default=1)
                    n = max(1, n)
                    new_index = max(0, index - (n - 1))
                    console.print(f"[yellow]Springe zurueck: Scheibe {new_index + 1}/{len(plan)} "
                                   f"wird als naechstes erneut gedruckt.[/yellow]")
                    index = new_index
                    previous_row = plan[index - 1] if index > 0 else None
                    if not args.no_save:
                        save_plan_state(state_path, _progress_dict(index))
                    retries_total += 1
                    retry_this_sheet = True

                time.sleep(args.pause)
            else:
                console.print("[dim](dry-run: nicht gedruckt)[/dim]")
                time.sleep(args.pause)

            if retry_this_sheet:
                continue

            previous_row = row
            index += 1
            printed += 1
            if not args.no_save:
                save_plan_state(state_path, _progress_dict(index))

    finally:
        if printer is not None:
            printer.close()

    console.print()
    console.print(Panel(
        f"[bold green]{printed} Scheibe(n) gedruckt[/bold green], "
        f"{retries_total} Wiederholung(en).\n"
        f"Fortschritt: {index}/{len(plan)}"
        + (" - Plan komplett!" if index >= len(plan) else ""),
        title="Fertig", style="green" if not quit_requested else "yellow",
    ))


if __name__ == "__main__":
    sys.exit(main() or 0)
