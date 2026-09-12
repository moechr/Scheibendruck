"""
Gemeinsame Logik fuer print_counter.py und print_session.py:
Profile/Zaehler aus config.ini laden, Platzhalter rendern, Overrides parsen.
"""
import configparser
import datetime
from pathlib import Path


def opt_int(section: configparser.SectionProxy, key: str):
    v = section.get(key, fallback="").strip()
    return int(v) if v else None


def load_profile(profile_name: str, cp: configparser.ConfigParser, config_path: Path) -> dict:
    section_name = f"profile:{profile_name}"
    if section_name not in cp:
        available = [s.split(":", 1)[1] for s in cp.sections() if s.startswith("profile:")]
        raise SystemExit(
            f"Profil '{profile_name}' nicht in {config_path} gefunden. "
            f"Verfuegbar: {', '.join(available) or '(keine)'}"
        )
    p = cp[section_name]
    counter_names = [n.strip() for n in p.get("counters", fallback="").split(",") if n.strip()]
    stack_change_counters = [n.strip() for n in p.get("stack_change_counters", fallback="").split(",") if n.strip()]
    return {
        "baudrate": p.getint("baudrate", fallback=9600),
        "parity": p.get("parity", fallback="N"),
        "xonxoff": p.getboolean("xonxoff", fallback=False),
        "rtscts": p.getboolean("rtscts", fallback=False),
        "eject_length": opt_int(p, "eject_length"),
        "line_spacing": opt_int(p, "line_spacing"),
        "encoding": p.get("encoding", fallback="cp850"),
        "line_width": p.getint("line_width", fallback=40),
        "shots_per_sheet": p.getint("shots_per_sheet", fallback=1),
        "counter_file": p.get("counter_file", fallback=f"state/{profile_name.lower()}_counters.json"),
        "counter_names": counter_names,
        "stack_change_counters": stack_change_counters or list(counter_names),
    }


def load_counter_defs(profile_name: str, counter_names: list, shots_per_sheet: int,
                      cp: configparser.ConfigParser, config_path: Path) -> dict:
    defs = {}
    for name in counter_names:
        section_name = f"counter:{profile_name}:{name}"
        if section_name not in cp:
            raise SystemExit(f"Zaehler-Definition fehlt: [{section_name}] in {config_path}")
        c = cp[section_name]
        is_range = c.getboolean("range", fallback=False)
        step_raw = c.get("step", fallback="1").strip().lower()
        if is_range or step_raw == "shots_per_sheet":
            step = shots_per_sheet
        else:
            try:
                step = int(step_raw)
            except ValueError:
                raise SystemExit(f"[{section_name}] step={step_raw!r} ist keine Zahl (oder 'shots_per_sheet').")
        defs[name] = {
            "start": c.getint("start", fallback=1),
            "step": step,
            "zero_pad": c.getint("zero_pad", fallback=0),
            "range": is_range,
        }
    return defs


def format_counter(value: int, zero_pad: int, is_range: bool, shots_per_sheet: int) -> str:
    def fmt(n: int) -> str:
        return f"{n:0{zero_pad}d}" if zero_pad else str(n)

    if is_range and shots_per_sheet > 1:
        return f"{fmt(value)}-{fmt(value + shots_per_sheet - 1)}"
    return fmt(value)


def render(template_lines: list, values: dict, line_width: int) -> str:
    """
    Rendert Template-Zeilen mit Platzhaltern. Jede Zeile wird standardmaessig
    rechtsbuendig auf 'line_width' ausgerichtet (wie bisher). Eine Zeile, die
    mit '<<' beginnt, wird stattdessen LINKSBUENDIG ausgegeben (das Praefix
    wird entfernt) - fuer Layouts mit gemischter Ausrichtung, z.B. Verein/
    Paarungs-Label oben links, Stand/Serie/Schuss-Block rechtsbuendig
    darunter (siehe templates/*_paarung.txt).
    """
    today = datetime.date.today().strftime("%d.%m.%Y")
    mapping = dict(values)
    mapping.setdefault("date", today)
    out = []
    for raw_line in template_lines:
        left_align = raw_line.startswith("<<")
        line_src = raw_line[2:] if left_align else raw_line
        try:
            line = line_src.format(**mapping)
        except KeyError as exc:
            raise SystemExit(
                f"Unbekannter Platzhalter {{{exc.args[0]}}} im Template. "
                f"Verfuegbar: {', '.join(sorted(mapping))}"
            )
        if left_align or not line_width:
            out.append(line)
        else:
            out.append(line.rjust(line_width))
    return "\n".join(out)


def parse_set_overrides(pairs) -> dict:
    overrides = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--set erwartet NAME=WERT, bekommen: {item!r}")
        name, value = item.split("=", 1)
        try:
            overrides[name.strip()] = int(value.strip())
        except ValueError:
            raise SystemExit(f"--set {item!r}: Wert muss eine ganze Zahl sein.")
    return overrides
