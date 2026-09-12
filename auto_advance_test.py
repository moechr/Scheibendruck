"""
auto_advance_test.py
---------------------
Testet, ob sich der Abschluss eines Scheiben-Zyklus (Einlegen -> Drucken ->
Auswerfen) anhand von GS r 3 zuverlaessig automatisch erkennen laesst - OHNE
jegliche Bestaetigung am PC.

Nutzt die gemeinsame Erkennungslogik aus tmu950.py
(TMU950.calibrate_slip_baseline / TMU950.wait_for_slip_cycle) - siehe dort
fuer die Herleitung aus dem Diagnose-Lauf (find_ready_signal.py).

Bereits live erprobt: 3/3 Zyklen korrekt automatisch erkannt (siehe
Projektnotizen). Dieses Skript dient weiterhin als einfacher, isolierter
Test dieser Logik, unabhaengig von print_session.py.

Verwendung:
    python auto_advance_test.py COM16 --count 3

Bitte beim Test mehrere Scheiben nacheinander einlegen, OHNE etwas am PC
zu druecken - nur beobachten, ob/wann automatisch zur naechsten Scheibe
weitergeschaltet wird.
"""
import argparse
import time

from tmu950 import TMU950, SerialConfig, cmd_init, cmd_select_paper, cmd_form_feed, cmd_enable_asb


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("port", help="COM-Port, z.B. COM16")
    p.add_argument("--baudrate", type=int, default=9600)
    p.add_argument("--parity", default="N")
    p.add_argument("--count", type=int, default=None,
                    help="Anzahl Scheiben (ohne Angabe: laeuft bis Ctrl+C)")
    p.add_argument("--poll-interval", type=float, default=0.25)
    p.add_argument("--poll-timeout", type=float, default=0.3)
    p.add_argument("--max-wait", type=float, default=60.0,
                    help="Maximale Wartezeit pro Scheibe, bevor trotzdem automatisch weitergemacht wird")
    p.add_argument("--calib-time", type=float, default=1.5)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = SerialConfig(port=args.port, baudrate=args.baudrate, parity=args.parity)
    printer = TMU950(cfg)
    printer.open()

    printer.write(cmd_init())
    time.sleep(0.1)
    printer.write(cmd_enable_asb(0xFF))
    time.sleep(0.1)
    printer.write(cmd_select_paper(4))
    time.sleep(0.2)

    print("Kalibriere Ruhezustand (bitte JETZT noch nichts einlegen) ...")
    baseline_b3 = printer.calibrate_slip_baseline(poll_timeout=args.poll_timeout, calib_time=args.calib_time)
    print(f"  Baseline B3 = 0x{baseline_b3:02X}")

    printed = 0
    try:
        while args.count is None or printed < args.count:
            printed += 1
            label = f"{printed}" if args.count is None else f"{printed}/{args.count}"
            print(f"\n--- Scheibe {label}: Druckauftrag wird gesendet, bitte einlegen ---")
            printer.write(cmd_select_paper(4))
            time.sleep(0.1)
            printer.print_text(f"AUTO-TEST #{printed}\n", encoding="cp850")
            printer.write(cmd_form_feed())

            def _on_tick(resp: bytes):
                if resp:
                    print(f"    GS r 3: {hexdump(resp)}")
                return None  # keine Hotkeys in diesem einfachen Test

            status, elapsed = printer.wait_for_slip_cycle(
                baseline_b3,
                poll_interval=args.poll_interval,
                poll_timeout=args.poll_timeout,
                max_wait=args.max_wait,
                on_tick=_on_tick,
            )
            if status == "done":
                print(f"  -> Zyklus erkannt als fertig nach {elapsed:.1f}s. Naechste Scheibe automatisch ...")
            else:
                print(f"  -> Kein eindeutiges Fertig-Signal erkannt nach {elapsed:.1f}s "
                      f"(max_wait erreicht) - mache trotzdem automatisch weiter ...")
    except KeyboardInterrupt:
        print("\n(abgebrochen mit Ctrl+C)")
    finally:
        printer.close()

    print(f"\n{printed} Scheibe(n) im Test durchlaufen.")


if __name__ == "__main__":
    main()
