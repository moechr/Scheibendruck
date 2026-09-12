"""
Phase 1: Grundverbindung zum TM-U950 testen.

Verwendung:
    python test_connection.py COM5
    python test_connection.py COM5 --baud 9600 --eject 90 --print

Baudrate/Paritaet/Handshake muessen zu den DIP-Schaltern am Drucker passen
(siehe README, Abschnitt "DIP-Schalter").
"""
import argparse
import sys
import time

from tmu950 import (
    TMU950,
    SerialConfig,
    cmd_init,
    cmd_select_paper,
    cmd_set_eject_length,
    cmd_form_feed,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", help="COM-Port, z.B. COM5")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--parity", choices=["N", "E", "O"], default="N")
    parser.add_argument("--xonxoff", action="store_true", help="Software-Flusssteuerung (XON/XOFF)")
    parser.add_argument("--rtscts", action="store_true", help="Hardware-Flusssteuerung (RTS/CTS)")
    parser.add_argument("--eject", type=int, default=None,
                         help="Eject-Length in 1/6 Zoll (ESC C n) vor dem Testdruck setzen")
    parser.add_argument("--print", action="store_true", help="Zusaetzlich eine Testzeile drucken und auswerfen")
    parser.add_argument("--text", default="TEST 0001 - Verbindung OK", help="Testtext fuer --print")
    parser.add_argument("--settle", type=float, default=0.3,
                         help="Wartezeit in Sekunden nach dem Auswurf, bevor der Port geschlossen wird "
                              "(Default 0.3s; bei Aussetzern mit einem instabilen Adapter ggf. erhoehen)")
    args = parser.parse_args()

    cfg = SerialConfig(
        port=args.port,
        baudrate=args.baud,
        parity=args.parity,
        xonxoff=args.xonxoff,
        rtscts=args.rtscts,
    )

    print(f"Oeffne {cfg.port} @ {cfg.baudrate} Baud, Parity={cfg.parity}, "
          f"XONXOFF={cfg.xonxoff}, RTSCTS={cfg.rtscts} ...")

    printer = TMU950(cfg)
    printer.open()
    try:
        print("Verbindung geoeffnet. Sende Init (ESC @) ...")
        printer.write(cmd_init())
        time.sleep(0.2)

        print("Waehle Slip-Station (ESC c 0 4) ...")
        printer.write(cmd_select_paper(4))
        time.sleep(0.2)

        for kind, name in [(1, "Drucker"), (2, "Offline"), (3, "Fehler"), (4, "Papier")]:
            status = printer.read_status(kind)
            if status is None:
                print(f"  Status '{name}': keine Antwort (Timeout) - "
                      f"Verkabelung/Baudrate/Handshake pruefen.")
            else:
                print(f"  Status '{name}': {status}")

        if args.eject is not None:
            print(f"Setze Eject-Length auf {args.eject} (ESC C {args.eject}) ...")
            printer.write(cmd_set_eject_length(args.eject))

        if args.print:
            print(f"Drucke Testzeile: {args.text!r}")
            printer.print_text(args.text + "\n")
            printer.write(cmd_form_feed())
            print(f"Warte {args.settle:.1f}s auf mechanischen Auswurf (Slip-Einzug) ...")
    finally:
        printer.close(settle_delay=args.settle if args.print else 0.2)

    print("Fertig.")


if __name__ == "__main__":
    sys.exit(main() or 0)
