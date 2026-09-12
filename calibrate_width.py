"""
Kalibrierungshilfe fuer 'line_width' in config.ini.

Druckt mehrere Testzeilen unterschiedlicher Laenge auf EINE eingelegte
Scheibe. Jede Zeile beginnt mit ihrer eigenen Laenge als Praefix
(z.B. "042|") gefolgt von fortlaufenden Ziffern bis zu dieser Laenge.
Auf dem Ausdruck ablesen, welche Zeile am weitesten rechts genau bis zum
Scheibenrand reicht (ohne umzubrechen oder abgeschnitten zu werden) -
diese Zahl (ohne Praefix, also die Ziffernanzahl) als 'line_width' in
config.ini eintragen.

Verwendung:
    python calibrate_width.py COM5
    python calibrate_width.py COM5 --from 30 --to 50 --step 2
"""
import argparse
import sys
import time

from tmu950 import TMU950, SerialConfig, cmd_init, cmd_select_paper, cmd_form_feed


def ruler_line(length: int) -> str:
    digits = "".join(str(i % 10) for i in range(length))
    return digits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", help="COM-Port, z.B. COM5")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--parity", choices=["N", "E", "O"], default="N")
    parser.add_argument("--xonxoff", action="store_true")
    parser.add_argument("--rtscts", action="store_true")
    parser.add_argument("--from", dest="from_len", type=int, default=120, help="Kleinste Testbreite (Default 120)")
    parser.add_argument("--to", dest="to_len", type=int, default=250, help="Groesste Testbreite (Default 250)")
    parser.add_argument("--step", type=int, default=10, help="Schrittweite zwischen den Testzeilen (Default 10)")
    args = parser.parse_args()

    cfg = SerialConfig(port=args.port, baudrate=args.baud, parity=args.parity,
                        xonxoff=args.xonxoff, rtscts=args.rtscts)

    lines = []
    n = args.from_len
    while n <= args.to_len:
        lines.append(ruler_line(n))
        n += args.step

    print("Bitte eine Test-Scheibe einlegen.")
    input("Enter druecken, wenn bereit ...")

    printer = TMU950(cfg)
    printer.open()
    try:
        printer.write(cmd_init())
        printer.write(cmd_select_paper(4))
        time.sleep(0.2)
        text = "\n".join(lines) + "\n"
        printer.print_text(text)
        printer.write(cmd_form_feed())
        print("Gedruckt. Jede Zeile ist eine fortlaufende Ziffernfolge dieser Laenge:")
        for l in lines:
            print(f"  Laenge {len(l):3d}: {l}")
        print("\nAuf dem Ausdruck ablesen: Die Zeilen sind nach Laenge sortiert gedruckt.")
        print("Gesucht ist die laengste Zeile, die noch VOLLSTAENDIG IN EINER ZEILE")
        print("bis zum Scheibenrand passt - also NICHT auf eine zweite Zeile umbricht")
        print("und auch nicht am Rand abgeschnitten wird. Deren Laenge als 'line_width'")
        print("in config.ini eintragen. Bricht schon die kuerzeste Testzeile um, mit")
        print("--from einen kleineren Wert testen; sind alle vollstaendig (auch die")
        print("laengste), mit --to einen groesseren Wert testen.")
    finally:
        printer.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
