"""
session_engine.py
-----------------
Gemeinsame Drucklogik fuer den Wettkampf-Betrieb - ohne Bildschirmausgabe,
damit sie sowohl von der Konsolen-Anwendung (print_session.py) als auch von
der grafischen Oberflaeche (print_gui.py) genutzt werden kann:

  - Programmordner (APP_DIR) und gespeicherter Druckfortschritt
    (state/<profil>_plan_state.json)
  - print_row_for()/sheet_values(): welche Werte tatsaechlich auf eine
    Scheibe kommen
  - SlipPrinter: Drucker vorbereiten, eine Scheibe senden, das Ende des
    Einlege-/Druck-/Auswurfzyklus abwarten, laufenden Druck abbrechen
  - SimulatedPrinter: dasselbe ohne Drucker (Testmodus der GUI)
  - PlanRun: druckt fertig gerenderte Scheiben nacheinander (inkl.
    Wiederholen/Zurueckspringen/Beenden) und meldet den Fortschritt per
    Callback - fuer den Wettkampf genauso wie fuer den Einzeldruck
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from tmu950 import (
    TMU950,
    SerialConfig,
    cmd_init,
    cmd_select_paper,
    cmd_set_eject_length,
    cmd_form_feed,
    cmd_line_spacing,
)

# Ordner, in dem config.ini/templates/state erwartet werden. Als normales
# Python-Skript ist das der Ordner dieser Datei; als mit PyInstaller
# gebautes .exe ("--onefile") liegt der Code aber in einem entpackten
# Temp-Ordner, nicht neben der .exe - dort zaehlt stattdessen der Ordner der
# .exe selbst (sys.executable), siehe build_exe.bat/README.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

DEFAULT_CONFIG = APP_DIR / "config.ini"

# Schluessel, die ein gespeicherter Fortschritt enthalten muss, damit sich
# die Session daraus fortsetzen laesst (siehe PlanRun/print_session.py).
PLAN_KEYS = ("club_a", "club_b", "num_paarungen", "start_club",
             "series_count", "shots_per_serie", "shots_per_sheet")


def state_path_for(config_path: Path, profile_name: str) -> Path:
    return config_path.parent / "state" / f"{profile_name.lower()}_plan_state.json"


def load_plan_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_plan_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def print_row_for(plan: list, idx: int) -> dict:
    """
    Baut das Dict, mit dem plan[idx] tatsaechlich gerendert/gedruckt wird:
    der Vereinsname steht auf der ERSTEN Scheibe JEDER Serie (also bei
    jedem Stand- ODER Serienwechsel neu), auf allen weiteren Scheiben
    derselben Serie bleibt er leer. Wird sowohl von den Vorschauen als auch
    vom eigentlichen Druck verwendet, damit beide exakt denselben Text
    zeigen/drucken.
    """
    row = plan[idx]
    if "stand" not in row:  # Einzeldruck (matchplan.generate_single_plan): kein Stand/Serie
        return dict(row)
    prev = plan[idx - 1] if idx > 0 else None
    is_first_of_serie = prev is None or (prev["stand"], prev["serie"]) != (row["stand"], row["serie"])
    return dict(row) if is_first_of_serie else {**row, "verein": ""}


def sheet_values(plan: list, idx: int, freitext: str = "") -> dict:
    """
    Alle Platzhalter-Werte fuer die Vorlage von Scheibe plan[idx]: die
    Plan-Zeile (siehe print_row_for) plus {freitext} (z.B. "LP Auflage")
    und {nr}/{anzahl} (laufende Scheibennummer/Gesamtzahl).
    """
    return {**print_row_for(plan, idx), "freitext": freitext, "nr": idx + 1, "anzahl": len(plan)}


@dataclass
class Timing:
    """Zeitverhalten beim Warten auf den Drucker (Defaults siehe print_session.py --help)."""
    pause: float = 0.3              # Pause nach erkanntem Zyklus-Ende
    max_wait: Optional[float] = None  # None = unbegrenzt auf das Fertig-Signal warten
    poll_interval: float = 0.15
    poll_timeout: float = 0.2
    min_wait: float = 1.0
    calib_time: float = 1.5


class SlipPrinter:
    """
    Der TM-U950 mit den Einstellungen eines config.ini-Profils, fertig fuer
    den Scheibendruck auf der Slip-Station.
    """

    def __init__(self, port: str, profile: dict, timing: Timing):
        self.profile = profile
        self.timing = timing
        self.baseline_b3: Optional[int] = None
        self.printer = TMU950(SerialConfig(
            port=port,
            baudrate=profile["baudrate"],
            parity=profile["parity"],
            xonxoff=profile["xonxoff"],
            rtscts=profile["rtscts"],
        ))

    def _setup(self) -> None:
        p = self.profile
        self.printer.write(cmd_init())
        if p["line_spacing"]:
            self.printer.write(cmd_line_spacing(p["line_spacing"]))
        if p["eject_length"]:
            self.printer.write(cmd_set_eject_length(p["eject_length"]))
        self.printer.write(cmd_select_paper(4))
        time.sleep(0.2)

    def open(self) -> None:
        self.printer.open()
        self._setup()

    def calibrate(self) -> int:
        """Ruhezustand der Slip-Station ermitteln - vorher darf nichts eingelegt sein."""
        self.baseline_b3 = self.printer.calibrate_slip_baseline(
            poll_timeout=self.timing.poll_timeout, calib_time=self.timing.calib_time
        )
        return self.baseline_b3

    def send_sheet(self, text: str) -> None:
        # Slip-Station vor JEDER Scheibe neu waehlen - der Drucker faellt nach
        # dem Auswurf auf die Bon/Journal-Rolle zurueck (siehe README).
        self.printer.write(cmd_select_paper(4))
        time.sleep(0.2)
        self.printer.print_text(text + "\n", encoding=self.profile["encoding"])
        self.printer.write(cmd_form_feed())

    def wait_cycle(self, on_tick=None) -> tuple:
        """Wartet auf Einlegen/Druck/Auswurf, siehe TMU950.wait_for_slip_cycle."""
        t = self.timing
        return self.printer.wait_for_slip_cycle(
            self.baseline_b3,
            poll_interval=t.poll_interval,
            poll_timeout=t.poll_timeout,
            max_wait=t.max_wait,
            min_wait=t.min_wait,
            on_tick=on_tick,
        )

    def cancel_and_eject(self) -> None:
        """
        Laufenden Druckauftrag so gut wie moeglich abbrechen und die
        (verkorkste) Scheibe auswerfen. Der TM-U950 ist alt genug, dass er
        NICHT Teil des modernen ESC/POS-Echtzeitbefehlssatzes ist (z.B. kein
        dokumentierter "DLE DC4"-Sofort-Abbruch) - verifiziert gegen Epsons
        aktuelle ESC/POS-Referenz, die den TM-U950 gar nicht mehr auflistet.
        Was dokumentiert und nutzbar ist: ESC @ (siehe cmd_init) "loescht die
        Daten im Druckpuffer" - also alles, was der Drucker schon empfangen,
        aber noch NICHT gedruckt hat, wird verworfen. Bereits physisch
        gedruckte Zeilen auf der Scheibe lassen sich dadurch NICHT
        rueckgaengig machen - je frueher man abbricht, desto weniger
        Schrott landet noch auf dem Papier. Danach wird die (Rest-)Scheibe
        wie gewohnt per Form-Feed ausgeworfen. Reagiert der Drucker gar
        nicht mehr (echter Papierstau), muss die Scheibe von Hand entnommen
        werden (Deckel oeffnen / Papierloese-Hebel der Scheibenstation -
        siehe Drucker-Handbuch "Removing Jammed Paper").
        """
        self._setup()
        self.printer.write(cmd_form_feed())
        time.sleep(self.timing.pause)

    def close(self) -> None:
        self.printer.close()


class SimulatedPrinter:
    """
    Ersatz fuer SlipPrinter ohne Drucker (Testmodus): jede Scheibe gilt nach
    'cycle_time' Sekunden als eingelegt/gedruckt/ausgeworfen. Wiederholen/
    Zurueckspringen/Beenden funktionieren wie mit echtem Drucker.
    """

    def __init__(self, timing: Timing, cycle_time: float = 1.5):
        self.timing = timing
        self.cycle_time = cycle_time

    def open(self) -> None:
        pass

    def calibrate(self) -> int:
        time.sleep(min(self.timing.calib_time, 0.5))
        return 0x60

    def send_sheet(self, text: str) -> None:
        pass

    def wait_cycle(self, on_tick=None) -> tuple:
        start = time.time()
        while time.time() - start < self.cycle_time:
            if on_tick is not None:
                hk = on_tick(b"")
                if hk:
                    return f"hotkey:{hk}", time.time() - start
            time.sleep(self.timing.poll_interval)
        return "done", time.time() - start

    def cancel_and_eject(self) -> None:
        time.sleep(self.timing.pause)

    def close(self) -> None:
        pass


class PlanRun:
    """
    Druckt 'sheets' (den fertig gerenderten Text jeder Scheibe) ab
    'start_index' nacheinander - dieselbe Abfolge wie in print_session.py,
    aber ohne eigene Bildschirmausgabe, damit sie in einem Hintergrund-Thread
    laufen kann (siehe print_gui.py).

    Bedienung von aussen (thread-sicher) per request():
        "w"  laufenden Druck abbrechen, Scheibe auswerfen, dieselbe erneut
        "b"  abbrechen, auswerfen, dann ask_back(index) fragen, um wie viele
             Scheiben zurueckgesprungen wird (1 = nur diese erneut, ...)
        "q"  beenden (die gerade gesendete Scheibe zaehlt nicht als erledigt)

    Rueckmeldungen gehen an on_event(art, daten):
        ("status", {"text"})             was gerade passiert
        ("log", {"text"})                Protokollzeile
        ("sheet", {"index", "retries"})  Scheibe 'index' wurde gesendet, warte
        ("advanced", {"index"})          Fortschritt: 'index' Scheiben erledigt
    'save_progress(index)' wird nach jeder erledigten Scheibe und nach jedem
    Zuruecksprung aufgerufen (None = nichts speichern, z.B. im Testmodus).
    """

    def __init__(self, sheets: List[str], printer, start_index: int,
                 on_event: Callable[[str, dict], None],
                 ask_back: Callable[[int], int],
                 save_progress: Optional[Callable[[int], None]] = None,
                 pause: float = Timing.pause):
        self.sheets = sheets
        self.printer = printer
        self.start_index = start_index
        self.on_event = on_event
        self.ask_back = ask_back
        self.save_progress = save_progress
        self.pause = pause
        self._stop = threading.Event()
        self._requests: "queue.Queue[str]" = queue.Queue()

    def request(self, key: str) -> None:
        if key == "q":
            self._stop.set()
        elif key in ("w", "b"):
            self._requests.put(key)

    def _on_tick(self, _resp: bytes) -> Optional[str]:
        if self._stop.is_set():
            return "q"
        try:
            return self._requests.get_nowait()
        except queue.Empty:
            return None

    def _emit(self, kind: str, **data) -> None:
        self.on_event(kind, data)

    def _save(self, index: int) -> None:
        if self.save_progress is not None:
            self.save_progress(index)

    def run(self) -> dict:
        sheets = self.sheets
        index = self.start_index
        printed = 0
        retries = 0
        try:
            self._emit("status", text="Verbinde mit dem Drucker ...")
            self.printer.open()
            self._emit("status", text="Kalibriere Ruhezustand der Scheiben-Station - "
                                      "bitte jetzt nichts einlegen ...")
            baseline = self.printer.calibrate()
            self._emit("log", text=f"Ruhezustand kalibriert (Referenzwert 0x{baseline:02X}).")

            while index < len(sheets) and not self._stop.is_set():
                self._emit("sheet", index=index, retries=retries)
                self.printer.send_sheet(sheets[index])

                status, elapsed = self.printer.wait_cycle(on_tick=self._on_tick)
                if status == "hotkey:q":
                    break
                if status == "done":
                    self._emit("log", text=f"Scheibe {index + 1}: Zyklus erkannt nach {elapsed:.1f}s.")
                elif status == "timeout":
                    self._emit("log", text=f"Scheibe {index + 1}: kein eindeutiges Fertig-Signal "
                                           f"nach {elapsed:.0f}s - mache trotzdem weiter.")
                elif status == "hotkey:w":
                    self._emit("status", text="Breche den Druck ab und werfe die Scheibe aus ...")
                    self.printer.cancel_and_eject()
                    self._emit("log", text=f"Scheibe {index + 1} wird wiederholt.")
                    retries += 1
                    time.sleep(self.pause)
                    continue
                elif status == "hotkey:b":
                    self._emit("status", text="Breche den Druck ab und werfe die Scheibe aus ...")
                    self.printer.cancel_and_eject()
                    n = max(1, self.ask_back(index) or 1)
                    new_index = max(0, index - (n - 1))
                    self._emit("log", text=f"Zurueckgesprungen: weiter mit Scheibe {new_index + 1}.")
                    index = new_index
                    self._save(index)
                    self._emit("advanced", index=index)
                    retries += 1
                    time.sleep(self.pause)
                    continue

                time.sleep(self.pause)
                index += 1
                printed += 1
                self._save(index)
                self._emit("advanced", index=index)
        finally:
            self.printer.close()

        return {"printed": printed, "retries": retries, "index": index, "total": len(sheets),
                "complete": index >= len(sheets)}
