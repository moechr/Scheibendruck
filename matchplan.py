"""
matchplan.py
------------
Erzeugt die Druckreihenfolge (eine Zeile = eine physische Scheibe) fuer
einen Wettkampf mit zwei Vereinen im Wechsel - als Python-Nachbau der
bisherigen Excel-Listen ("1_Schuss"/"2_Schuss" in WM_LIGA_Transfer.xlsm),
OHNE Excel/Makro:

  - Pro "Paarung" (Startplatz-Paar) schiesst zuerst ein Schuetze von
    Verein A, dann einer von Verein B (oder umgekehrt, siehe
    'start_club') - das ergibt die fortlaufende "Stand"-Nummer (1, 2, 3,
    ... ueber BEIDE Vereine hinweg, wechselt also bei jedem neuen
    Schuetzen automatisch den Verein).
  - Jeder Schuetze schiesst 'series_count' Serien (Standard 4), jede
    Serie hat 'shots_per_serie' Schuss (Standard 10), aufgeteilt in
    Scheiben zu je 'shots_per_sheet' Schuss (1 oder 2).
  - 'name' ist das Bezeichner-Label ("Paarung <N> - <Kuerzel>"); das
    Kuerzel ist per Default der erste Buchstabe des Vereinsnamens (wie
    in der Vorlage: "Niederrieden" -> N, "Salgen" -> S).

Beispiel (2 Paarungen, 2 Schuss/Scheibe, Verein A startet):
    Stand 1: Paarung 1 - A, Serie 1..4, je Schuss 1-2,3-4,5-6,7-8,9-10
    Stand 2: Paarung 1 - B, Serie 1..4, ...
    Stand 3: Paarung 2 - A, Serie 1..4, ...
    Stand 4: Paarung 2 - B, Serie 1..4, ...

generate_plan() gibt eine Liste von dicts zurueck, je Eintrag eine zu
druckende Scheibe:
    {"stand": int, "serie": int, "schuss": str, "verein": str, "name": str}
"""
from typing import Dict, List, Optional


def _schuss_label(start: int, shots_per_serie: int, shots_per_sheet: int) -> str:
    end = min(start + shots_per_sheet - 1, shots_per_serie)
    return str(start) if end == start else f"{start}-{end}"


def generate_plan(club_a: str, club_b: str, num_paarungen: int,
                   shots_per_sheet: int = 2, start_club: str = "A",
                   series_count: int = 4, shots_per_serie: int = 10,
                   abbrev_a: Optional[str] = None, abbrev_b: Optional[str] = None) -> List[Dict]:
    if num_paarungen < 1:
        raise ValueError("num_paarungen muss >= 1 sein")
    if start_club not in ("A", "B"):
        raise ValueError("start_club muss 'A' oder 'B' sein")
    if shots_per_sheet < 1:
        raise ValueError("shots_per_sheet muss >= 1 sein")

    abbrev_a = (abbrev_a or club_a[:1] or "A").upper()
    abbrev_b = (abbrev_b or club_b[:1] or "B").upper()
    order = [(club_a, abbrev_a), (club_b, abbrev_b)]
    if start_club == "B":
        order.reverse()

    rows: List[Dict] = []
    stand = 0
    for paarung in range(1, num_paarungen + 1):
        for verein_name, abbrev in order:
            stand += 1
            label = f"Paarung {paarung} - {abbrev}"
            for serie in range(1, series_count + 1):
                schuss_start = 1
                while schuss_start <= shots_per_serie:
                    rows.append({
                        "stand": stand,
                        "serie": serie,
                        "schuss": _schuss_label(schuss_start, shots_per_serie, shots_per_sheet),
                        "verein": verein_name,
                        "name": label,
                    })
                    schuss_start += shots_per_sheet
    return rows


def generate_single_plan(text1: str = "", text2: str = "", series_count: int = 1,
                         shots_per_serie: int = 10, shots_per_sheet: int = 1,
                         text1_first_only: bool = False, text2_first_only: bool = False) -> List[Dict]:
    """
    Druckreihenfolge fuer den Einzeldruck (z.B. am Schiessabend): die Baender
    fuer EINE Person - wie ein einzelner Stand im Wettkampf 'series_count'
    Serien zu je 'shots_per_serie' Schuss, aufgeteilt in Baender zu je
    'shots_per_sheet' Schuss. Die Schuss-Nummern beginnen in jeder Serie neu
    (bei 10 Schuss und 5 Schuss pro Scheibe: Serie 1 1-5, 6-10, Serie 2
    1-5, ...).

    'text1'/'text2' sind freie Texte (Platzhalter {freitext1}/{freitext2},
    z.B. Verein und Name). Mit textN_first_only=True steht der jeweilige Text
    nur auf dem ersten Band jeder Serie, sonst auf jedem. Eine Zeile je Band:
        {"freitext1": str, "freitext2": str, "serie": int, "schuss": str}
    """
    if series_count < 1 or shots_per_serie < 1:
        raise ValueError("series_count und shots_per_serie muessen >= 1 sein")
    if shots_per_sheet < 1:
        raise ValueError("shots_per_sheet muss >= 1 sein")
    rows: List[Dict] = []
    for serie in range(1, series_count + 1):
        for start in range(1, shots_per_serie + 1, shots_per_sheet):
            first = start == 1
            rows.append({"freitext1": text1 if first or not text1_first_only else "",
                         "freitext2": text2 if first or not text2_first_only else "",
                         "serie": serie, "schuss": _schuss_label(start, shots_per_serie, shots_per_sheet)})
    return rows


def plan_fingerprint(club_a: str, club_b: str, num_paarungen: int, shots_per_sheet: int,
                      start_club: str, series_count: int, shots_per_serie: int) -> str:
    """Kurze, stabile Kennung der Plan-Parameter - dient dazu, beim Fortsetzen
    einer Session zu erkennen, ob sich die Konfiguration seit dem letzten
    Lauf geaendert hat (siehe print_session.py: state/*.json)."""
    return "|".join(str(x) for x in (
        club_a, club_b, num_paarungen, shots_per_sheet, start_club, series_count, shots_per_serie
    ))


def plan_summary(rows: List[Dict]) -> str:
    if not rows:
        return "(leer)"
    stands = {r["stand"] for r in rows}
    return f"{len(rows)} Scheiben insgesamt, {len(stands)} Staende (1..{max(stands)})"


def plan_condensed(rows: List[Dict]) -> List[Dict]:
    """
    Fasst die (u.U. sehr lange) Scheiben-Liste zu einer Zeile pro Stand
    zusammen - fuer eine kurze Kontroll-Uebersicht vor dem Drucken:
        {"stand": int, "verein": str, "name": str, "sheets": int,
         "first_index": int, "last_index": int, "schuss_range": str}
    'first_index'/'last_index' sind 0-basierte Positionen in 'rows'.
    """
    condensed: List[Dict] = []
    for i, row in enumerate(rows):
        if not condensed or condensed[-1]["stand"] != row["stand"]:
            condensed.append({
                "stand": row["stand"], "verein": row["verein"], "name": row["name"],
                "sheets": 0, "first_index": i, "last_index": i,
                "schuss_first": row["schuss"], "schuss_last": row["schuss"],
            })
        condensed[-1]["sheets"] += 1
        condensed[-1]["last_index"] = i
        condensed[-1]["schuss_last"] = row["schuss"]
    for c in condensed:
        c["schuss_range"] = (c["schuss_first"] if c["schuss_first"] == c["schuss_last"]
                              else f"{c['schuss_first']} .. {c['schuss_last']}")
    return condensed
