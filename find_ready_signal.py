"""
find_ready_signal.py
---------------------
Live-Diagnose-Tool: sucht das Signal, mit dem der TM-U950 zuverlaessig
meldet, dass eine Scheibe eingelegt, bedruckt und ausgeworfen wurde - und
zwar OHNE dass am PC etwas bestaetigt werden muss. Das ist Voraussetzung
dafuer, dass print_session.py komplett automatisch (ohne "Druck ok?"
druecken) von Scheibe zu Scheibe weiterlaufen kann.

Hintergrund:
  Die einfachen Echtzeit-Statusabfragen (DLE EOT 1-4) haben sich als
  untauglich erwiesen (Rohwerte blieben ueber 45+ Abfragen konstant). Das
  TM-U950-Handbuch (Abschnitt 3.7) sagt dazu ausdruecklich:
      "To check the slip status exactly, ASB function should be used."
  ASB = "Automatic Status Back" (GS a) - der Drucker meldet sich dabei von
  SICH AUS, sobald sich etwas aendert. Zusaetzlich gibt es GS r 3
  ("remaining printing space" auf der Scheibe).

  Aus dem ersten Testlauf wissen wir bereits: es AENDERT sich etwas
  (Bytes wie "14 00 60 6F" vs. "14 00 40 6C" vs. spaeter konstant "00").
  Was genau noch unklar war: WELCHE Aenderung zu WELCHER physischen
  Aktion (Einlegen / Drucken-Auswerfen / Entnehmen) gehoert - der erste
  Lauf wurde vor der vollen Dauer per Ctrl+C abgebrochen, ohne dass die
  Aktionen mit Zeitstempeln vermerkt waren.

  Dieses Skript loest das durch klar angekuendigte PHASEN mit fester
  Dauer: es sagt im Terminal an, was in den naechsten N Sekunden zu tun
  ist, und jede geloggte Zeile traegt die Phase mit. Dadurch lassen sich
  Byte-Aenderungen eindeutig einer Aktion zuordnen.

Verwendung:
    python find_ready_signal.py COM16

  Bitte den kompletten Ablauf bis zum Ende durchlaufen lassen (nicht mit
  Ctrl+C abbrechen) - inkl. der Phase "ENTNAHME".

Phasen (Gesamtdauer per Default ~40s):
    1. IDLE_VORHER      (5s)  - nichts tun, nichts eingelegt
    2. EINLEGEN_DRUCKEN (15s) - JETZT Scheibe einlegen, Druck/Auswurf abwarten
    3. ENTNAHME         (10s) - die ausgeworfene Scheibe JETZT entnehmen
    4. IDLE_NACHHER     (10s) - nichts mehr tun

Am Ende: Zusammenfassung aller unterschiedlichen Byte-Folgen je Quelle
(ASB-Stream / GS r 3), mit Angabe in welcher Phase sie zuerst auftraten.

Das komplette Konsolen-Protokoll bitte 1:1 zurueckmelden (copy/paste).
"""
import argparse
import time

from tmu950 import (
    TMU950,
    SerialConfig,
    cmd_init,
    cmd_select_paper,
    cmd_form_feed,
    cmd_enable_asb,
    cmd_transmit_status,
)


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", help="COM-Port, z.B. COM16")
    parser.add_argument("--baudrate", type=int, default=9600)
    parser.add_argument("--parity", default="N")
    parser.add_argument("--asb-mask", type=lambda s: int(s, 0), default=0xFF,
                         help="Bitmaske fuer GS a (Default: 0xFF = alles aktivieren)")
    parser.add_argument("--gsr-interval", type=float, default=0.4,
                         help="Sekunden zwischen GS r 3 Abfragen (Default: 0.4)")
    parser.add_argument("--phase-idle-vorher", type=float, default=5.0)
    parser.add_argument("--phase-einlegen", type=float, default=15.0)
    parser.add_argument("--phase-entnahme", type=float, default=10.0)
    parser.add_argument("--phase-idle-nachher", type=float, default=10.0)
    return parser.parse_args()


class Logger:
    def __init__(self):
        self.start = time.time()
        self.phase = "INIT"
        self.seen_asb = {}
        self.seen_gsr = {}
        self.first_phase_asb = {}
        self.first_phase_gsr = {}

    def set_phase(self, name: str, duration: float) -> None:
        self.phase = name
        ts = time.time() - self.start
        print()
        print(f"[{ts:6.2f}s] {'='*20} PHASE: {name} (Dauer: {duration:.0f}s) {'='*20}")

    def log_asb(self, chunk: bytes) -> None:
        ts = time.time() - self.start
        h = hexdump(chunk)
        print(f"[{ts:6.2f}s] ({self.phase:<18}) ASB-Stream : {h}  (len={len(chunk)})")
        key = bytes(chunk)
        self.seen_asb[key] = self.seen_asb.get(key, 0) + 1
        self.first_phase_asb.setdefault(key, self.phase)

    def log_gsr(self, chunk: bytes) -> None:
        ts = time.time() - self.start
        h = hexdump(chunk)
        print(f"[{ts:6.2f}s] ({self.phase:<18}) GS r 3     : {h}  (len={len(chunk)})")
        key = bytes(chunk)
        self.seen_gsr[key] = self.seen_gsr.get(key, 0) + 1
        self.first_phase_gsr.setdefault(key, self.phase)


def run_phase(printer: TMU950, log: Logger, duration: float, gsr_interval: float) -> None:
    phase_start = time.time()
    last_gsr_poll = 0.0
    while time.time() - phase_start < duration:
        n = printer.in_waiting()
        if n:
            chunk = printer.read_raw(n, timeout=0.15)
            if chunk:
                log.log_asb(chunk)

        if time.time() - last_gsr_poll >= gsr_interval:
            last_gsr_poll = time.time()
            printer.write(cmd_transmit_status(3))
            resp = printer.read_raw(64, timeout=0.15)
            if resp:
                log.log_gsr(resp)

        time.sleep(0.05)


def main() -> None:
    args = parse_args()

    cfg = SerialConfig(port=args.port, baudrate=args.baudrate, parity=args.parity)
    printer = TMU950(cfg)
    printer.open()

    log = Logger()

    print("Init (ESC @) ...")
    printer.write(cmd_init())
    time.sleep(0.1)

    print(f"Aktiviere ASB (GS a, Maske=0x{args.asb_mask:02X}) ...")
    printer.write(cmd_enable_asb(args.asb_mask))
    time.sleep(0.1)

    print("Waehle Slip-Station (ESC c 0 4) ...")
    printer.write(cmd_select_paper(4))
    time.sleep(0.2)

    try:
        log.set_phase("IDLE_VORHER", args.phase_idle_vorher)
        print(">>> BITTE JETZT NICHTS TUN - noch keine Scheibe einlegen. <<<")
        run_phase(printer, log, args.phase_idle_vorher, args.gsr_interval)

        log.set_phase("EINLEGEN_DRUCKEN", args.phase_einlegen)
        print(">>> JETZT Testdruck senden + SOFORT eine Scheibe einlegen. <<<")
        printer.print_text("TEST ASB-DIAGNOSE\n", encoding="cp850")
        printer.write(cmd_form_feed())
        run_phase(printer, log, args.phase_einlegen, args.gsr_interval)

        log.set_phase("ENTNAHME", args.phase_entnahme)
        print(">>> JETZT die ausgeworfene Scheibe entnehmen. <<<")
        run_phase(printer, log, args.phase_entnahme, args.gsr_interval)

        log.set_phase("IDLE_NACHHER", args.phase_idle_nachher)
        print(">>> Fertig - bitte jetzt nichts mehr tun. <<<")
        run_phase(printer, log, args.phase_idle_nachher, args.gsr_interval)
    except KeyboardInterrupt:
        print("\n(abgebrochen mit Ctrl+C)")
    finally:
        printer.close()

    print()
    print("=" * 78)
    print("ZUSAMMENFASSUNG")
    print(f"ASB-Stream: {len(log.seen_asb)} unterschiedliche Byte-Folge(n):")
    for v, count in log.seen_asb.items():
        print(f"    {hexdump(v):<40}  x{count:<3}  zuerst in Phase: {log.first_phase_asb[v]}")
    print(f"GS r 3:     {len(log.seen_gsr)} unterschiedliche Antwort(en):")
    for v, count in log.seen_gsr.items():
        print(f"    {hexdump(v):<40}  x{count:<3}  zuerst in Phase: {log.first_phase_gsr[v]}")
    print("=" * 78)


if __name__ == "__main__":
    main()
