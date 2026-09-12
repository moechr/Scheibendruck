"""Listet verfuegbare serielle Ports auf (z.B. den USB-Seriell-Wandler)."""
import serial.tools.list_ports


def main() -> None:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("Keine seriellen Ports gefunden. USB-Seriell-Wandler eingesteckt "
              "und Treiber installiert (siehe Ordner 'RS232 Adapter Driver')?")
        return
    print("Gefundene serielle Ports:")
    for p in ports:
        print(f"  {p.device:10s}  {p.description}  (hwid: {p.hwid})")


if __name__ == "__main__":
    main()
