"""
Phase 2 / Batch-Modus: Fortlaufende, benannte Zaehler auf Scheiben drucken.

Fuer den taeglichen Betrieb am Schiessstand mit Fehldruck-Wiederholung,
Fortschrittsanzeige und Farbhinweisen bei Stapel-Wechsel: print_session.py
verwenden. Dieses Skript hier eignet sich fuer Tests, Dry-Runs und
unbeaufsichtigtes Stapel-Drucken ohne Rueckfragen.

Das Template nutzt benannte Platzhalter, deren Namen aus config.ini kommen
(z.B. {starter}, {serie}, {schuss} - frei konfigurierbar unter
[profile:...] -> counters= und den zugehoerigen [counter:PROFIL:NAME]
Abschnitten). Zusaetzlich immer verfuegbar: {date}.

Jede Zeile des Templates wird automatisch rechtsbuendig auf die in
config.ini konfigurierte Spaltenbreite (line_width) ausgerichtet - keine
manuellen Leerzeichen im Template noetig.

Beispiele:
    python print_counter.py COM5 templates\\lp_beispiel.txt
    python print_counter.py COM5 templates\\lp_beispiel.txt --count 5
    python print_counter.py COM5 templates\\lp_beispiel.txt --set starter=10 --set serie=2
    python print_counter.py COM5 templates\\lp_beispiel.txt --shots-per-sheet 1
    python print_counter.py COM5 templates\\lp_beispiel.txt --dry-run --no-save --count 3

Profile (Baudrate, Eject-Length, Zeilenbreite, Zaehler, ...) stehen in
config.ini.
"""
import argparse
import configparser
import sys
import time
from pathlib import Path

from tmu950 import (
    TMU950,
    SerialConfig,
    cmd_init,
    cmd_select_paper,
    cmd_set_eject_length,
    cmd_form_feed,
    cmd_line_spacing,
)
from counter_state import MultiCounterState
from printjob import load_profile, load_counter_defs, format_counter, render, parse_set_overrides

DEFAULT_CONFIG = Path(__file__).parent / "config.ini"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", help="COM-Port, z.B. COM5")
    parser.add_argument("template", help="Pfad zur Template-Textdatei")
    parser.add_argument("--profile", default="LP", help="Profilname aus config.ini (Default: LP)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Pfad zur config.ini")
    parser.add_argument("--count", type=int, default=1, help="Anzahl zu druckender Scheiben in Folge")
    parser.add_argument("--set", action="append", metavar="NAME=WERT",
                         help="Startwert eines Zaehlers fuer diesen Lauf ueberschreiben "
                              "(mehrfach nutzbar, z.B. --set starter=10 --set serie=2)")
    parser.add_argument("--shots-per-sheet", type=int, default=None,
                         help="Ueberschreibt shots_per_sheet aus config.ini fuer diesen Lauf (z.B. 1 oder 2)")
    parser.add_argument("--no-save", action="store_true",
                         help="Zaehlerstaende nach dem Lauf NICHT speichern (zum Testen)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Nur anzeigen was gedruckt wuerde, nicht an den Drucker senden")
    parser.add_argument("--pause", type=float, default=0.5,
                         help="Sekunden Pause nach jedem Ausdruck (Default: 0.5)")
    parser.add_argument("--prompt", action="store_true",
                         help="Bei --count > 1 vor jeder Scheibe auf Enter warten "
                              "(Default: ohne Nachfrage direkt durchlaufen, nur durch --pause getrennt)")
    args = parser.parse_args()

    config_path = Path(args.config)
    cp = configparser.ConfigParser()
    if not cp.read(config_path, encoding="utf-8"):
        raise SystemExit(f"Config-Datei nicht gefunden: {config_path}")

    profile = load_profile(args.profile, cp, config_path)
    shots_per_sheet = args.shots_per_sheet if args.shots_per_sheet is not None else profile["shots_per_sheet"]

    if not profile["counter_names"]:
        raise SystemExit(f"Profil '{args.profile}': keine 'counters = ...' Zeile in {config_path} gefunden.")

    counter_defs = load_counter_defs(args.profile, profile["counter_names"], shots_per_sheet, cp, config_path)

    template_path = Path(args.template)
    if not template_path.exists():
        raise SystemExit(f"Template nicht gefunden: {template_path}")
    template_lines = template_path.read_text(encoding="utf-8").splitlines()

    overrides = parse_set_overrides(args.set)
    unknown = set(overrides) - set(counter_defs)
    if unknown:
        raise SystemExit(f"--set nennt unbekannte Zaehler: {', '.join(sorted(unknown))}. "
                          f"Verfuegbar: {', '.join(counter_defs)}")

    state_path = config_path.parent / profile["counter_file"]
    state = MultiCounterState(state_path)

    current_values = {
        name: overrides.get(name, state.read(name, default=d["start"]))
        for name, d in counter_defs.items()
    }

    print(f"Profil '{args.profile}': {args.count}x drucken, shots_per_sheet={shots_per_sheet}.")
    for name, d in counter_defs.items():
        print(f"  Zaehler '{name}': Start={current_values[name]}, Schritt={d['step']}"
              f"{' (Bereich)' if d['range'] else ''}")

    printer = None
    if not args.dry_run:
        cfg = SerialConfig(
            port=args.port,
            baudrate=profile["baudrate"],
            parity=profile["parity"],
            xonxoff=profile["xonxoff"],
            rtscts=profile["rtscts"],
        )
        printer = TMU950(cfg)
        printer.open()
        printer.write(cmd_init())
        if profile["line_spacing"]:
            printer.write(cmd_line_spacing(profile["line_spacing"]))
        if profile["eject_length"]:
            printer.write(cmd_set_eject_length(profile["eject_length"]))

    try:
        for i in range(args.count):
            if args.count > 1 and args.prompt and not args.dry_run:
                input(f"Scheibe einlegen und Enter druecken ({i + 1}/{args.count}) ...")

            display_values = {
                name: format_counter(current_values[name], d["zero_pad"], d["range"], shots_per_sheet)
                for name, d in counter_defs.items()
            }
            text = render(template_lines, display_values, profile["line_width"])
            print(f"--- Scheibe {i + 1}/{args.count} ---")
            print(text)

            if args.dry_run:
                print("(dry-run: nicht gedruckt)")
            else:
                # Die Slip-Station muss vor JEDEM Ausdruck neu gewaehlt
                # werden - der Drucker faellt nach dem Auswurf offenbar auf
                # die Bon/Journal-Rolle zurueck, sonst landet der naechste
                # Ausdruck auf dem Bon statt auf der Scheibe.
                printer.write(cmd_select_paper(4))
                time.sleep(0.2)
                printer.print_text(text + "\n", encoding=profile["encoding"])
                printer.write(cmd_form_feed())
                time.sleep(args.pause)

            # Zaehler fuer die naechste Scheibe weiterschalten und sofort
            # sichern - so geht bei einem Abbruch mitten in einer Serie kein
            # Stand verloren.
            for name, d in counter_defs.items():
                current_values[name] += d["step"]
            if not args.no_save:
                state.write_many(current_values)

        if args.no_save:
            print("(--no-save: Zaehlerstaende nicht veraendert)")
        else:
            print("Neue Zaehlerstaende gespeichert: "
                  + ", ".join(f"{name}={v}" for name, v in current_values.items()))
    finally:
        if printer is not None:
            printer.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
