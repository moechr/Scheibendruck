"""
tmu950.py
---------
Ansteuerung des Epson TM-U950 (Nadeldrucker mit Bon/Journal/Scheiben-Station)
ueber die serielle Schnittstelle (RS-232, meist per USB-Seriell-Wandler) mit
ESC/POS-aehnlichen Befehlen.

WICHTIG: Der TM-U950 unterstuetzt nur eine Teilmenge des "normalen"
ESC/POS-Befehlssatzes moderner Bondrucker (kein Rastergrafik-Druck). Die hier
verwendeten Befehle stammen aus der TM-U950-Spezifikation, die im
Projektordner als "TM-U950_spc_k.pdf" liegt. Bei Unklarheiten - vor allem bei
ESC C (Eject-Length) und ESC c 0 n (Papierstation) - dort nachschlagen und
per test_connection.py auf echtem Papier verifizieren, bevor produktiv
gedruckt wird.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import serial
from serial import SerialException

ESC = b"\x1b"
GS = b"\x1d"
DLE = b"\x10"

# ---------------------------------------------------------------------------
# ESC/POS Befehls-Bausteine
# ---------------------------------------------------------------------------


def cmd_init() -> bytes:
    """ESC @ - Drucker initialisieren / zuruecksetzen."""
    return ESC + b"@"


def cmd_bold(on: bool = True) -> bytes:
    """ESC E n - Fettdruck (Emphasized Mode) an/aus."""
    return ESC + b"E" + bytes([1 if on else 0])


def cmd_underline(on: bool = True) -> bytes:
    """ESC - n - Unterstreichen an/aus."""
    return ESC + b"-" + bytes([1 if on else 0])


def cmd_double_strike(on: bool = True) -> bytes:
    """ESC G n - Fettdruck durch doppeltes Anschlagen (Double-Strike)."""
    return ESC + b"G" + bytes([1 if on else 0])


def cmd_line_spacing_default() -> bytes:
    """ESC 2 - Standard-Zeilenabstand wiederherstellen."""
    return ESC + b"2"


def cmd_line_spacing(n: int) -> bytes:
    """ESC 3 n - Zeilenabstand in Motoreinheiten setzen (0-255)."""
    return ESC + b"3" + bytes([n & 0xFF])


def cmd_feed_lines(n: int) -> bytes:
    """ESC J n - Drucken und um n Zeilen weitertransportieren (0-255)."""
    return ESC + b"J" + bytes([n & 0xFF])


def cmd_select_paper(n: int) -> bytes:
    """
    ESC c 0 n - Papierstation waehlen (modellabhaengig!):
      1 = Bon/Receipt, 2 = Journal, 4 = Slip/Beleg.
    Vor Nutzung im TM-U950-Handbuch verifizieren - manche Firmwares waehlen
    die Station automatisch anhand des eingelegten Papiers.
    """
    return ESC + b"c" + b"0" + bytes([n & 0xFF])


def cmd_set_eject_length(n: int) -> bytes:
    """
    ESC C n - Eject-Length fuer die Einzelblatt-/Scheiben-Station setzen.
    n in 1/6-Zoll-Schritten (lt. Spezifikation).
    Aus dem Bestandsprojekt bereits bekannt: 0x90 (144) wurde fuer die
    LG-Baender verwendet ("Wichtige Infos zum Druck": 1B 43 90). Fuer die
    kuerzeren LP-Baender muss n kleiner sein - siehe Kalibrierungshinweis
    in der README.
    """
    return ESC + b"C" + bytes([n & 0xFF])


def cmd_form_feed() -> bytes:
    """FF - Drucken und Beleg auswerfen (Slip-Modus) / Seitenumbruch."""
    return b"\x0c"


def cmd_line_feed() -> bytes:
    return b"\n"


# --- Statusabfragen (Real-Time Status Transmission) -------------------------


def cmd_status(n: int) -> bytes:
    """
    DLE EOT n - Echtzeit-Status abfragen (1 Byte Antwort):
      1 = Druckerstatus, 2 = Offline-Status, 3 = Fehlerstatus,
      4 = Papiersensor-Status.
    """
    return DLE + b"\x04" + bytes([n])


def cmd_enable_asb(n: int) -> bytes:
    """
    GS a n - "Automatic Status Back" (ASB) aktivieren/deaktivieren.
    Laut TM-U950-Spezifikation (Abschnitt 3.7, Note 8) ausdruecklich der
    vom Hersteller empfohlene Weg, um den Scheiben-Status zuverlaessig zu
    erkennen ("To check the slip status exactly, ASB function should be
    used") - im Gegensatz zu DLE EOT (siehe cmd_status), das sich in
    Tests am echten Geraet ueber den gesamten Einlege-/Druck-/
    Auswurfzyklus hinweg NICHT geaendert hat. Einmal aktiviert, sendet der
    Drucker Statusbytes von sich aus (unaufgefordert), sobald sich etwas
    aendert - siehe find_ready_signal.py, das dies live protokolliert, um
    die genaue Byte-/Bit-Belegung am echten Geraet zu ermitteln (im
    oeffentlich zugaenglichen Teil der Spezifikation nicht vollstaendig
    dokumentiert).
    """
    return GS + b"a" + bytes([n & 0xFF])


def cmd_transmit_status(n: int) -> bytes:
    """
    GS r n - Status anfordern (im Gegensatz zu DLE EOT eine andere
    Befehlsklasse). Laut Spezifikation kann n=3 die "remaining printing
    space" auf der aktuell eingelegten Scheibe liefern (Abschnitt 3.7,
    Note 10) - moeglicherweise nutzbar, um "Scheibe eingelegt" bzw.
    "kein Scheibe eingelegt" zu unterscheiden. Genaue Bit-/Byte-Belegung
    ebenfalls per find_ready_signal.py am echten Geraet zu verifizieren.
    """
    return GS + b"r" + bytes([n & 0xFF])


def extract_status4(data: bytes) -> Optional[tuple]:
    """
    Sucht das LETZTE '14 00 <b3> <b4>'-Muster in einer GS-r-3/ASB-Antwort
    (auch wenn davor noch andere/alte Bytes im Puffer standen) und gibt
    (b3, b4) zurueck, sonst None. Live per find_ready_signal.py ermittelt:
    der TM-U950 antwortet auf 'GS r 3' im Ruhezustand mit genau diesem
    4-Byte-Muster; b3 aendert sich waehrend eines Einlege-/Druck-/
    Auswurfzyklus (z.B. 0x60 -> 0x40 -> 0x20 -> wieder 0x60) - siehe
    TMU950.calibrate_slip_baseline / wait_for_slip_cycle.
    """
    for i in range(len(data) - 4, -1, -1):
        if data[i] == 0x14 and data[i + 1] == 0x00:
            return data[i + 2], data[i + 3]
    return None


def decode_status_byte(kind: int, b: int) -> dict:
    """
    Grobe Interpretation der Statusbits nach ESC/POS-Konvention. Im Zweifel
    gegen TM-U950_spc_k.pdf pruefen - je nach Firmware koennen einzelne Bits
    abweichen.
    """
    bits = {f"bit{i}": bool(b & (1 << i)) for i in range(8)}
    info: dict = {"raw": b, "bits": bits}
    if kind == 1:
        info["online"] = not bits["bit3"]
    elif kind == 2:
        info["cover_open"] = bits["bit2"]
        info["feed_button_pressed"] = bits["bit6"]
    elif kind == 3:
        info["error"] = bits["bit2"] or bits["bit5"] or bits["bit6"]
    elif kind == 4:
        info["paper_near_end"] = bits["bit2"] or bits["bit3"]
        info["paper_out"] = bits["bit5"] or bits["bit6"]
    return info


@dataclass
class SerialConfig:
    port: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"  # 'N', 'E', 'O'
    stopbits: float = 1
    xonxoff: bool = False  # Software-Flusssteuerung
    rtscts: bool = False  # Hardware-Flusssteuerung (RTS/CTS)
    dsrdtr: bool = False  # Hardware-Flusssteuerung (DTR/DSR)
    timeout: float = 2.0


class TMU950:
    """Kleine Hilfsklasse fuer den seriellen Zugriff auf den TM-U950."""

    def __init__(self, config: SerialConfig):
        self.config = config
        self._ser: Optional[serial.Serial] = None

    def open(self, retries: int = 3, retry_delay: float = 1.5) -> None:
        """
        Port oeffnen. Manche USB-Seriell-Chips (v.a. CH340-Klone) melden beim
        schnellen Wiederoeffnen kurz nach dem letzten Schliessen einen
        generischen Windows-Fehler ("Ein an das System angeschlossenes Geraet
        funktioniert nicht" / PermissionError 13, WinError 31). Das ist meist
        ein voruebergehender Treiber-Aussetzer, kein echter Defekt - deshalb
        hier ein paar Versuche mit kurzer Pause, bevor aufgegeben wird.
        """
        c = self.config
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                self._ser = serial.Serial(
                    port=c.port,
                    baudrate=c.baudrate,
                    bytesize=c.bytesize,
                    parity=c.parity,
                    stopbits=c.stopbits,
                    xonxoff=c.xonxoff,
                    rtscts=c.rtscts,
                    dsrdtr=c.dsrdtr,
                    timeout=c.timeout,
                )
                if not c.rtscts and not c.dsrdtr:
                    # Wir nutzen keine Hardware-Flusssteuerung - DTR/RTS
                    # explizit auf "aus" setzen, statt sie in unbekanntem
                    # Zustand zu lassen (manche Adapter/Drucker reagieren
                    # empfindlich auf toggelnde Steuerleitungen).
                    try:
                        self._ser.dtr = False
                        self._ser.rts = False
                    except (OSError, ValueError):
                        pass
                return
            except serial.SerialException as exc:
                last_exc = exc
                if attempt < retries:
                    print(f"  ({c.port} liess sich nicht oeffnen, Versuch {attempt}/{retries} "
                          f"- warte {retry_delay:.1f}s und versuche erneut: {exc})")
                    time.sleep(retry_delay)
        raise SerialException(
            f"Port {c.port} konnte nach {retries} Versuchen nicht geoeffnet werden ({last_exc}). "
            f"Haeufigste Ursachen bei CH340-Adaptern: anderes Programm haelt den Port noch offen, "
            f"oder der USB-Chip braucht nach dem letzten Schliessen ein paar Sekunden Pause - "
            f"kurz warten und erneut versuchen, notfalls den Adapter einmal aus- und wieder einstecken."
        ) from last_exc

    def close(self, settle_delay: float = 0.3) -> None:
        """
        Port schliessen. Beim Slip-Einzug ist FF (Form Feed) ein mechanischer
        Auswurf, der etwas Zeit braucht - eine kurze Pause vorm Schliessen
        gibt dem Drucker Zeit, den letzten Befehl fertig zu verarbeiten.
        (Ein frueher beobachtetes Haengenbleiben lag an einem instabilen
        CH340-Adapter, nicht an dieser Wartezeit - mit einem funktionierenden
        Adapter reicht ein kurzer Wert wie hier als Default.)
        """
        if self._ser and self._ser.is_open:
            if settle_delay:
                time.sleep(settle_delay)
            self._ser.close()

    def __enter__(self) -> "TMU950":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def write(self, data: bytes) -> None:
        assert self._ser is not None, "Verbindung nicht geoeffnet (open() aufrufen)"
        self._ser.write(data)
        self._ser.flush()

    def print_text(self, text: str, encoding: str = "cp850") -> None:
        """
        Text senden. cp850 ist die klassische DOS/Epson-Codepage fuer
        westeuropaeische Umlaute (ae/oe/ue/sz). Falls Umlaute falsch
        erscheinen: andere Codepage probieren (z.B. cp437) oder per
        ESC t n auf dem Drucker die passende Codepage waehlen.
        """
        self.write(text.encode(encoding, errors="replace"))

    def read_status(self, kind: int) -> Optional[dict]:
        assert self._ser is not None
        self.write(cmd_status(kind))
        b = self._ser.read(1)
        if not b:
            return None
        return decode_status_byte(kind, b[0])

    def in_waiting(self) -> int:
        """Anzahl gerade im Empfangspuffer wartender Bytes (fuer ASB-Diagnose)."""
        assert self._ser is not None
        return self._ser.in_waiting

    def calibrate_slip_baseline(self, poll_timeout: float = 0.3, calib_time: float = 1.5) -> int:
        """
        Ermittelt den Ruhe-Wert von Statusbyte 3 (siehe extract_status4)
        durch kurzes Pollen von GS r 3, BEVOR eine Scheibe eingelegt wird.
        Dient als Referenz fuer wait_for_slip_cycle(), um automatisch (ohne
        Bestaetigung am PC) zu erkennen, wann ein Einlege-/Druck-/
        Auswurfzyklus beendet ist. Live in auto_advance_test.py erprobt
        (3/3 Zyklen korrekt erkannt).
        """
        start = time.time()
        last_b3 = None
        while time.time() - start < calib_time:
            self.write(cmd_transmit_status(3))
            resp = self.read_raw(64, timeout=poll_timeout)
            if resp:
                parsed = extract_status4(resp)
                if parsed:
                    last_b3, _ = parsed
            time.sleep(0.2)
        return last_b3 if last_b3 is not None else 0x60

    def wait_for_slip_cycle(self, baseline_b3: int, poll_interval: float = 0.25,
                             poll_timeout: float = 0.3, max_wait: Optional[float] = None,
                             on_tick=None, min_wait: float = 2.0) -> tuple:
        """
        Wartet, bis der Einlege-/Druck-/Auswurfzyklus fertig ist. Primaeres
        Signal (in ALLEN bisherigen Tests reproduziert - auch bei echten
        mehrzeiligen Drucken, wo das sekundaere Signal unten NICHT auftrat):
        die GS-r-3-Antwort "flacht" auf ein einzelnes 0x00-Byte ab und
        bleibt dabei (mind. 2x in Folge, fruehestens nach 'min_wait'
        Sekunden, als Schutz gegen einen Fehlalarm direkt nach dem Senden).
        Eine vorherige "Abweichung" von 'baseline_b3' wird NICHT mehr
        vorausgesetzt - bei einem echten mehrzeiligen Druck wurde
        stattdessen z.B. "03 03 03" (vermutlich "verbleibender Druckplatz")
        beobachtet statt des erwarteten "14 00 <b3> <b4>"-Musters, bevor es
        auf Flatline ging; das reine Verlangen einer Abweichung fuehrte
        dort zum Haengenbleiben bis 'max_wait'.

        Sekundaeres Signal (falls es auftritt): Statusbyte 3 (siehe
        extract_status4) weicht von 'baseline_b3' ab und kehrt danach
        dorthin zurueck - das gilt ebenfalls sofort als "fertig".

        (Kein DLE EOT - das aendert sich auf diesem Geraet nachweislich
        nicht, siehe Modul-Docstring/README.)

        'on_tick' wird bei JEDEM Schleifendurchlauf aufgerufen (mit der
        zuletzt empfangenen GS-r-3-Antwort als bytes, oder b"" falls in
        diesem Tick nichts ankam) - z.B. fuer eine nicht-blockierende
        Tastaturabfrage. Gibt 'on_tick' einen String zurueck (z.B. 'w' oder
        'q'), wird sofort mit ("hotkey:<wert>", dauer) abgebrochen.

        'max_wait': None (Default) heisst UNBEGRENZT warten - kein
        Timeout, es wird einfach so lange gewartet, bis der Zyklus
        erkannt wird oder eine Hotkey gedrueckt wird (der Bediener
        bestimmt durchs Einlegen selbst das Tempo, es soll dabei nie
        vorzeitig "weitergemacht" werden). Nur wenn explizit eine Zahl
        angegeben wird, gibt es nach dieser Zeit einen "timeout"-Abbruch.

        Gibt (status, dauer) zurueck; status ist "done", "timeout" oder
        "hotkey:<taste>".
        """
        start = time.time()
        deviated = False
        flatline_count = 0
        while max_wait is None or time.time() - start < max_wait:
            self.write(cmd_transmit_status(3))
            resp = self.read_raw(64, timeout=poll_timeout)

            if on_tick is not None:
                hk = on_tick(resp)
                if hk:
                    return f"hotkey:{hk}", time.time() - start

            elapsed = time.time() - start
            if resp:
                if len(resp) == 1 and resp[0] == 0x00:
                    flatline_count += 1
                    if elapsed >= min_wait and flatline_count >= 2:
                        return "done", elapsed
                else:
                    flatline_count = 0
                    parsed = extract_status4(resp)
                    if parsed:
                        b3, _ = parsed
                        if b3 != baseline_b3:
                            deviated = True
                        elif deviated and b3 == baseline_b3:
                            return "done", time.time() - start
            time.sleep(poll_interval)
        return "timeout", time.time() - start

    def read_raw(self, size: int = 64, timeout: Optional[float] = None) -> bytes:
        """
        Liest bis zu 'size' Bytes roh (ohne Interpretation), z.B. um
        spontan eintreffende ASB-Bytes oder eine GS-r-n-Antwort
        einzusammeln. Blockiert standardmaessig hoechstens bis
        SerialConfig.timeout (Default 2.0s) - fuer Diagnosezwecke laesst
        sich das per 'timeout' voruebergehend auf einen kurzen Wert
        (z.B. 0.15s) setzen, damit ein Poll nicht jedes Mal die vollen
        2 Sekunden blockiert, wenn weniger als 'size' Bytes ankommen.
        """
        assert self._ser is not None
        if timeout is None:
            return self._ser.read(size)
        old_timeout = self._ser.timeout
        self._ser.timeout = timeout
        try:
            return self._ser.read(size)
        finally:
            self._ser.timeout = old_timeout

    def wait_until_ready(self, timeout: float = 20.0, poll_interval: float = 0.3,
                          settle_polls: int = 2, on_poll=None, debug: bool = False) -> bool:
        """
        Wartet, bis der Drucker wieder 'online'/bereit meldet (Druckerstatus
        DLE EOT 1), fuer 'settle_polls' aufeinanderfolgende Abfragen in
        Folge - so wird ein kurzes Flackern beim Zustandswechsel (z.B.
        waehrend des Slip-Einzugs/Auswurfs) nicht faelschlich schon als
        "fertig" gewertet.

        Der TM-U950 wartet laut Handbuch nach 'ESC c 0 4' selbststaendig auf
        das Einlegen einer Scheibe (Slip-LED blinkt) und druckt automatisch,
        sobald eine erkannt wird - man muss also nicht selbst "Enter"
        druecken, um den Druck zu starten. Diese Funktion erkennt, wann der
        gesamte Zyklus (Warten auf Einlegen, Drucken, Auswerfen) fertig ist,
        damit die naechste Scheibe sicher angestossen werden kann.

        Achtung: Das Handbuch dokumentiert die genaue Bit-Belegung fuer
        "Auftrag noch aktiv" nicht vollstaendig - dies ist ein
        Best-Effort-Ansatz auf Basis des allgemeinen Online-Bits. Gibt
        True zurueck, wenn ein bereiter Zustand erkannt wurde, sonst False
        nach Ablauf von 'timeout' (dann sollte trotzdem weitergemacht
        werden, siehe README).

        'on_poll' ist ein optionaler Callback, der bei jedem Poll-Versuch
        mit (statuses, consecutive_ok) aufgerufen wird - 'statuses' ist ein
        dict {kind: status_dict_oder_None}. Mit debug=True werden zusaetzlich
        zum Druckerstatus (1) auch Offline- (2), Fehler- (3) und
        Papierstatus (4) abgefragt und mitgegeben, um die tatsaechliche
        Bit-Belegung am echten Geraet sichtbar zu machen (siehe
        print_session.py --debug-status).
        """
        start = time.time()
        consecutive_ok = 0
        while time.time() - start < timeout:
            if debug:
                statuses = {k: self.read_status(k) for k in (1, 2, 3, 4)}
            else:
                statuses = {1: self.read_status(1)}
            if on_poll is not None:
                on_poll(statuses, consecutive_ok)
            status1 = statuses.get(1)
            if status1 is not None and status1.get("online"):
                consecutive_ok += 1
                if consecutive_ok >= settle_polls:
                    return True
            else:
                consecutive_ok = 0
            time.sleep(poll_interval)
        return False
