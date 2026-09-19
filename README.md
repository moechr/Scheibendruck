# TM-U950 Seriendruck-Suite (ESC/POS über RS-232)

Kleine Python-Suite, um den Epson TM-U950 direkt über die serielle
Schnittstelle (USB-Seriell-Wandler) mit ESC/POS-Befehlen anzusprechen —
zunächst zum reinen Verbindungstest (Phase 1), danach zum Drucken von
fortlaufenden Zählern auf Scheiben/Bänder (Phase 2).

Diese Suite ersetzt (vorerst) nicht den bestehenden Word-Seriendruck-Workflow
im Projektordner, sondern ist ein zweiter, direkterer Weg über die serielle
Schnittstelle — gedacht z. B. für automatisiertes fortlaufendes Durchnummerieren.

## Enthaltene Dateien

| Datei | Zweck |
|---|---|
| `tmu950.py` | ESC/POS-Befehle + serielle Verbindung (Kernmodul) |
| `list_ports.py` | Zeigt verfügbare COM-Ports an |
| `test_connection.py` | Phase 1: Verbindung testen, Status abfragen, Testzeile drucken |
| `calibrate_width.py` | Kalibrierungs-Testdruck für `line_width` |
| `print_gui.py` | **Die eigentliche Anwendung für den Wettkampf-Betrieb**: grafische Oberfläche (siehe Abschnitt 7) |
| `print_session.py` | Dieselbe Anwendung als Konsolen-Variante mit Kommandozeilen-Optionen (siehe Abschnitt 7, „Alternativ“) |
| `session_engine.py` | Gemeinsame Drucklogik von GUI und Konsole: Drucker vorbereiten, Scheibe senden, Zyklus abwarten, Abbrechen, Fortschritt speichern |
| `matchplan.py` | Erzeugt die komplette Druckreihenfolge (Stand/Serie/Schuss/Verein) für zwei Vereine im Wechsel |
| `printjob.py` | Gemeinsame Logik: Profil aus `config.ini` laden, Template-Zeilen rendern |
| `build_exe.bat` | Baut aus `print_gui.py` eine einzelne, per Doppelklick startbare `TMU950_Druck.exe` (siehe Abschnitt 11) - kein Python/pip mehr noetig, um die Anwendung zu benutzen |
| `version_info.txt` | Datei-Eigenschaften (Version, Beschreibung) fuer die `.exe`, von `build_exe.bat` eingebunden |
| `config.ini` | Profile `LP` (Luftpistole) und `LG` (Luftgewehr): Baudrate, Eject-Length, Zeilenbreite. (Die `[counter:...]`-Abschnitte sind Altlasten des frueheren, inzwischen ersetzten Zaehler-Systems und werden von `print_session.py` nicht mehr gelesen.) |
| `templates/lp_paarung.txt`, `templates/lg_paarung.txt` | Die Vorlagen für den Wettkampf (Verein/Paarung/Stand/Serie/Schuss/Freitext) |
| `templates/lp_einzel.txt`, `templates/lg_einzel.txt` | Die Vorlagen für den Einzeldruck am Schießabend (Freitext 1/2, Serie, Schuss, Freitext unten, siehe Abschnitt 7a) |
| `templates/lp_beispiel.txt`, `templates/lg_beispiel.txt` | Alte Beispiel-Vorlagen aus dem fruehen Zaehler-System - nicht mehr in Benutzung, nur als Referenz |
| `state/` | Wird automatisch angelegt, enthaelt pro Profil den gespeicherten Druckfortschritt (`lp_plan_state.json`/`lg_plan_state.json`) sowie die zuletzt in der Oberfläche gewählten Einstellungen – Disziplin, Druckmodus, Vorlagen, Freitext, COM-Port (`gui_settings.json`) |
| `print_counter.py`, `counter_state.py` | **Alt/unbenutzt**: fruehere Batch-/Zaehler-Version, vor der Umstellung auf das Stand/Serie/Schuss-Modell. Bleibt nur als Referenz liegen. |

## 1. Hardware: USB-Seriell-Wandler

Im Projektordner liegt bereits ein Treiber-Ordner `RS232 Adapter Driver`
(Unterordner für Windows/Mac/Linux/Android) — das deutet auf einen
**FTDI-Chipsatz** hin. Treiber installieren (falls noch nicht geschehen),
Wandler einstecken, dann im Geräte-Manager unter „Anschlüsse (COM & LPT)“
nachsehen, welcher COM-Port erscheint — oder einfach:

```
pip install -r requirements.txt
python list_ports.py
```

## 2. DIP-Schalter am Drucker

Der TM-U950 stellt Baudrate/Datenformat/Handshake über interne DIP-Schalter
ein (nicht per Software). Nach dem Handbuch (`TM-U950_spc_k.pdf` im
Projektordner, Abschnitt „Interface“):

- **Baudrate** (Schalter 1-4 / 1-5): z. B. beide OFF = 9600 Baud
- **Datenwortlänge** (1-1), **Parität an/aus** (1-2), **Parität gerade/ungerade** (1-3)
- **Handshake** (1-8): XON/XOFF oder DTR/DSR
- **Empfangspuffer** (2-2): 32 Byte oder 2048 Byte

**Empfehlung:** 9600 Baud, 8 Datenbits, keine Parität, XON/XOFF-Handshake
(braucht nur 3 Adern: TX, RX, GND — unkompliziert mit einfachen
USB-Seriell-Wandlern). Bitte die genaue Schalterstellung/-polung im
Handbuch verifizieren, bevor ihr die Schalter umlegt — Angaben aus
automatisiert exportierten Handbuch-Auszügen können ungenau sein.

Die Skripte in dieser Suite nutzen standardmäßig 9600/8/N/kein Handshake;
mit `--xonxoff` bzw. in `config.ini` (`xonxoff = true`) lässt sich das
anpassen, sobald ihr wisst, wie der Drucker eingestellt ist.

## 3. Python-Umgebung einrichten

Python 3.10+ von [python.org](https://www.python.org/downloads/) oder per
`winget install Python.Python.3.12`. Danach in diesem Ordner:

```
pip install -r requirements.txt
```

## 4. Verbindung testen (Phase 1)

```
python test_connection.py COM5
```

Das Skript öffnet den Port, schickt `ESC @` (Init) und fragt vier
Statusbytes ab (Drucker/Offline/Fehler/Papier). Antwortet der Drucker nicht:

- COM-Port richtig? (`list_ports.py` erneut prüfen)
- Baudrate/Parität passend zu den DIP-Schaltern? (`--baud`, `--parity`)
- Handshake passend? (`--xonxoff` oder `--rtscts` setzen bzw. weglassen)
- TX/RX ggf. vertauscht (Nullmodem nötig)? Kabel/Adapter prüfen.

Mit Testdruck:

```
python test_connection.py COM5 --print --text "TEST 0001"
```

**Wichtig:** Erst auf Schmierpapier/Testpapier ausprobieren, nicht gleich
auf echten Scheiben/Bändern.

## 5. Eject-Length kalibrieren

`ESC C n` (im Modul `cmd_set_eject_length`) legt fest, wie weit ein Blatt
nach dem Druck ausgeworfen wird — Einheit lt. Spezifikation 1/6 Zoll pro
Schritt. Aus der bestehenden Notiz im Projekt ist für **LG**-Bänder bereits
`n = 144` (0x90) bekannt und in `config.ini` unter `[profile:LG]` hinterlegt.

Für die **LP**-Bänder (kürzer) ist der Wert noch offen. Kalibrieren:

```
python test_connection.py COM5 --eject 90 --print
```

Wert von `--eject` schrittweise anpassen, bis der Auswurf optisch zur
LP-Bandlänge passt (grobe Faustformel: gewünschte gemessene Auswurflänge in
Zoll × 6 ≈ n). Den ermittelten Wert dann in `config.ini` unter
`[profile:LP]` → `eject_length` eintragen.

## 6. Der Wettkampf-Ablauf (Stand/Serie/Schuss/Verein)

Statt unabhängiger Zähler druckt die Anwendung eine **komplette,
im Voraus berechnete Druckreihenfolge** für einen Wettkampf zwischen zwei
Vereinen (nachgebaut aus der bisherigen Excel-Logik in
`WM_LIGA_Transfer.xlsm`, siehe `matchplan.py`):

- **Stand**: fortlaufende Standnummer über BEIDE Vereine hinweg. Bei jeder
  neuen "Paarung" (Schützen-Paar) wechselt der Verein automatisch:
  Stand 1 = Verein A, Stand 2 = Verein B, Stand 3 = Verein A (2. Paarung), ...
  Welcher Verein bei Stand 1 beginnt, ist einstellbar ("Wer beginnt?").
- **Serie**: 1..N je Stand (Standard 4).
- **Schuss**: 1..M je Serie (Standard 10), als Bereich ("1-2") dargestellt,
  wenn mehr als 1 Schuss pro Scheibe gedruckt wird.
- **Verein/Paarungs-Label**: wechselt zusammen mit dem Stand.

Beispiel (2 Paarungen, 2 Schuss/Scheibe, Verein A beginnt):

```
Stand 1: Verein A, Serie 1..4, je Schuss 1-2,3-4,5-6,7-8,9-10
Stand 2: Verein B, Serie 1..4, ...
Stand 3: Verein A (2. Paarung), Serie 1..4, ...
Stand 4: Verein B (2. Paarung), Serie 1..4, ...
```

Ein Stand wird dabei **immer komplett zu Ende gedruckt** (alle Serien, alle
Scheiben), bevor der nächste Stand beginnt — die Reihenfolge springt nie
zwischen zwei Ständen hin und her.

**Der Vereinsname wird nur auf der ERSTEN Scheibe jeder Serie gedruckt**
(bei mehreren Scheiben pro Serie bleibt die Vereins-Zeile auf den weiteren
Scheiben derselben Serie leer) — das entspricht der Vorlage: der Name muss
nicht auf jeder einzelnen Scheibe wiederholt werden. Die App zeigt ihn am
Bildschirm trotzdem bei jeder Scheibe zur Kontrolle an (Info-Panel/Tabelle),
nur auf dem tatsächlichen Ausdruck bleibt er auf den Folge-Scheiben leer.

Die Vorlagen `templates/lp_paarung.txt` / `templates/lg_paarung.txt` nutzen
dafür die Platzhalter `{verein}`, `{name}` (das Paarungs-Label,
z. B. "Paarung 1 - N"), `{stand}`, `{serie}`, `{schuss}` und `{freitext}`:

```
<<{verein}
{name}
Stand:  {stand}
Serie:  {serie}
Schuss: {schuss}
{freitext}
```

`{freitext}` ist die frei wählbare Zeile, z. B. „LP Auflage“. Sie wird in der
Oberfläche direkt eingetippt (Feld „Freitext unten“, pro Disziplin gemerkt), in der
Konsolen-Variante per `--freitext`. Der Vorschlag beim ersten Start kommt aus
`freitext = ...` im `[profile:...]`-Abschnitt der `config.ini`. Bleibt das Feld
leer, bleibt die Zeile auf der Scheibe leer.

Eine Zeile, die mit `<<` beginnt, wird **linksbündig** gedruckt (Präfix wird
entfernt) — alle anderen Zeilen werden wie gehabt **rechtsbündig** auf
`line_width` (aus `config.ini`) ausgerichtet. Feste Textzeilen ohne
Platzhalter bleiben unverändert stehen, leere Zeilen schieben den Rest nach
unten. In jeder Vorlage stehen außerdem `{nr}`/`{anzahl}` (laufende
Scheibennummer/Gesamtzahl) und `{date}` (heutiges Datum) zur Verfügung.

## 7. Wettkampf drucken

Am einfachsten per Doppelklick auf **`TMU950_Druck.exe`** (siehe Abschnitt 11)
bzw. mit Python:

```
python print_gui.py
```

Es öffnet sich ein Fenster, das von oben nach unten ausgefüllt wird:

1. **Drucker** – Disziplin (Profil `LP`/`LG` aus `config.ini`), Vorlage
   (wird passend zu Disziplin und Druckmodus vorgewählt), COM-Port (die Liste
   zeigt die angeschlossenen Anschlüsse, „Suchen“ aktualisiert sie; ein
   USB-Seriell-Wandler wird automatisch bevorzugt). Mit **Testmodus** läuft
   alles ohne Drucker durch – nichts wird gedruckt und kein Fortschritt
   gespeichert.
2. **Was wird gedruckt?** – Register **Wettkampf**: Verein A/B, Anzahl
   Paarungen, wer an Stand 1 beginnt, Schuss pro Scheibe (1, 2 oder 5),
   Serien je Stand à Schuss je Serie. Das Register **Einzeldruck** ist für
   Bänder einzelner Personen am Schießabend (siehe Abschnitt 7a). Darunter
   für beide das Feld **Freitext unten** (z. B. „LP Auflage“, siehe Abschnitt 6)
   und sofort, wie viele Scheiben/Stände das ergibt.
3. **Drucken** – ab welcher Scheibe gedruckt wird (normalerweise 1, bei einem
   unterbrochenen Druck automatisch die nächste offene Scheibe, siehe
   Abschnitt 10), dann **„Druck starten“**. Vor dem Start kommt noch eine
   Zusammenfassung zur Bestätigung.

Rechts aktualisieren sich bei jeder Änderung die **Stand-für-Stand-Übersicht**
(Status erledigt/Start/offen) und die **Vorschau**, die jede Scheibe genau so
zeigt, wie sie gedruckt wird (siehe Abschnitt 9). Die zuletzt gedruckten
Wettkampf-Werte sowie Disziplin, Druckmodus, Vorlagen, Freitext und COM-Port
werden beim nächsten Start automatisch vorbelegt.

Während des Drucks zeigt der **Druckstatus** unten rechts, welche Scheibe als
Nächstes eingelegt werden muss – bei einem **neuen Stand** farbig hervorgehoben
(gelb, wenn dabei auch der Verein wechselt). Die Knöpfe „Wiederholen“,
„Zurückspringen …“ und „Druck beenden“ entsprechen den Tasten `w`/`b`/`q`
(siehe Abschnitt 8) und funktionieren auch per Tastatur. Das Register
„Protokoll“ listet alle Ereignisse mit Uhrzeit.

Beim Aufruf mit Parametern werden Port, Vorlage und Profil vorbelegt (so
funktionieren bestehende Verknüpfungen weiter):

```
TMU950_Druck.exe COM5 templates\lp_paarung.txt --profile LP
```

**Start per Verknüpfung** (z. B. auf dem Desktop) funktioniert von überall –
das Programm sucht `config.ini`, `templates\` und `state\` immer in seinem
eigenen Ordner, der Eintrag „Ausführen in“ spielt keine Rolle. Praktisch ist
je eine Verknüpfung pro Disziplin, im Feld *Ziel* z. B.:

```
"D:\...\TMU950_Druck.exe" --profile LG
```

Ohne `.exe`, direkt mit installiertem Python (samt `pip install -r
requirements.txt`), als Ziel `pyw.exe` verwenden – das startet ohne
schwarzes Konsolenfenster:

```
C:\Windows\pyw.exe "D:\...\print_gui.py" --profile LP
```

Der Anzeigename einer Disziplin in der Auswahlliste kommt aus `title = ...`
im jeweiligen `[profile:...]`-Abschnitt der `config.ini`.

## 7a. Einzeldruck am Schießabend

Für einzelne Schützen schnell Bänder drucken, ohne sie von Hand zu
beschriften: im Abschnitt „Was wird gedruckt?“ das Register **Einzeldruck**
wählen und einstellen:

- **Freitext 1** (Zeile 1, links) und **Freitext 2** (Zeile 2, rechts) –
  beliebige Texte, z. B. Verein und Name der Person. Für jedes der beiden
  Felder einzeln wählbar: **„nur bei Serienbeginn“** (nur auf dem ersten
  Band jeder Serie) oder – ohne Häkchen – auf jedem Band.
- **Serien** à **Schuss** je Serie (wie ein einzelner Stand im Wettkampf)
- **Schuss pro Scheibe** (1, 2 oder 5) – daraus ergibt sich die Anzahl der
  Bänder; die Schuss-Nummern beginnen in jeder Serie neu, z. B. 2 Serien à
  10 Schuss mit 5 Schuss pro Scheibe = 4 Bänder: Serie 1 `1-5`, `6-10`,
  Serie 2 `1-5`, `6-10`
- darunter wie im Wettkampf **Freitext unten** (z. B. „LP Auflage“)

Die Vorschau zeigt jedes Band genau so, wie es gedruckt wird. **Enter in
Freitext 1 oder 2** (oder „Druck starten“) druckt sofort – ohne Rückfrage,
damit es am Schießabend schnell geht. Danach steht der Cursor wieder in dem
Feld, aus dem gestartet wurde, der Text ist markiert: einfach den nächsten
Namen tippen und Enter. Rechts listet „Einzeldruck: heute gedruckt“ alle
Drucke dieser Sitzung mit Uhrzeit, Freitext 1/2, Serien, Anzahl Bänder und
Status. Wiederholen/Zurückspringen/Beenden funktionieren wie im Wettkampf;
ein Fortschritt wird beim Einzeldruck nicht gespeichert (bei einem Abbruch
einfach die restliche Anzahl neu drucken).

Das Layout steht in `templates/lp_einzel.txt` bzw. `templates/lg_einzel.txt`
(Platzhalter `{freitext1}`, `{freitext2}`, `{serie}`, `{schuss}`,
`{freitext}` für „Freitext unten“, dazu `{nr}`/`{anzahl}`/`{date}`) –
standardmäßig an denselben Stellen wie Verein/Paarung/Serie/Schuss auf den
Wettkampf-Scheiben, die Stand-Zeile bleibt frei:

```
<<{freitext1}
{freitext2}

Serie:  {serie}
Schuss: {schuss}
{freitext}
```

### Alternativ: Konsolen-Variante `print_session.py`

Dieselbe Anwendung gibt es weiterhin für die Eingabeaufforderung (benötigt
Python mit `pip install -r requirements.txt`):

```
python print_session.py COM5 templates\lp_paarung.txt
```

Ohne weitere Angaben fragt die App die Einstellungen über eine
**Pfeiltasten-/Tab-Eingabemaske** ab (für Laien gedacht, wie ein
DOS-Programm): Verein A, Verein B, Anzahl Paarungen, wer an Stand 1 beginnt,
Schuss pro Scheibe, Serien je Stand, Schuss je Serie. Die zuletzt benutzten
Werte werden automatisch als Vorschlag vorbelegt. Alternativ lassen sich
alle Werte auch direkt per Kommandozeile setzen (überspringt die Eingabemaske
komplett):

```
python print_session.py COM5 templates\lp_paarung.txt ^
    --club-a Niederrieden --club-b Salgen --paarungen 5 ^
    --start-club A --series-count 4 --shots-per-serie 10 --shots-per-sheet 2
```

Danach zeigt die App eine **Stand-für-Stand-Übersicht** zur Kontrolle und
bietet optional eine **Scheibe-für-Scheibe-Vorschau** an (siehe Abschnitt 9),
bevor mit einer letzten Bestätigung ("Passt diese Reihenfolge? Jetzt
starten?") tatsächlich gedruckt wird.

**Jede Scheibe wird automatisch gedruckt** — kein "Enter zum Drucken" nötig.
Der TM-U950 wartet nach Auswahl der Slip-Station selbstständig auf das
Einlegen; die App pollt danach den Druckerstatus (`GS r 3`), bis
Einlegen + Drucken + Auswerfen abgeschlossen sind, und **wartet dabei
standardmäßig unbegrenzt** (kein Timeout) — der Bediener bestimmt durchs
Einlegen selbst das Tempo. Mit `--max-wait SEKUNDEN` lässt sich optional
doch ein Timeout setzen.

Wichtige Optionen:

```
--club-a NAME / --club-b NAME     Vereinsnamen (ohne Angabe: Eingabemaske)
--paarungen N                     Anzahl Paarungen
--start-club A|B                  wer an Stand 1 beginnt
--series-count N                  Serien je Stand (Default 4)
--shots-per-serie N                Schuss je Serie (Default 10)
--shots-per-sheet 1|2|5           Schuss pro Scheibe
--freitext TEXT                   Freitext-Zeile, z. B. "LP Auflage" (Default: freitext aus config.ini)
--start-index N                   an dieser Scheibe (1-basiert) statt beim gespeicherten Fortschritt beginnen
--no-save                         Fortschritt nicht speichern (zum Testen)
--simple-input                    einfache Tipp-und-Enter-Abfrage statt Pfeiltasten-Eingabemaske
--no-preview                      keine Scheibe-für-Scheibe-Vorschau anbieten
--dry-run                         ohne Drucker durchspielen (nichts wird gedruckt)
```

Zum gefahrlosen Ausprobieren ohne Drucker:

```
python print_session.py COM5 templates\lp_paarung.txt --dry-run
```

## 8. Während des Druckens: Tasten `w` / `b` / `q`

Während die App auf das Einlegen/Drucken/Auswerfen wartet, sind (nur unter
Windows, per `msvcrt`, nicht blockierend — es muss keine Taste gedrückt
werden, damit es weitergeht) folgende Tasten jederzeit aktiv:

- **`w`** — diese Scheibe wiederholen (bei einem Fehldruck). Bricht den
  laufenden Druckauftrag sofort ab (siehe unten), wirft die Scheibe aus und
  druckt danach dieselbe Scheibe erneut.
- **`b`** — mehrere Scheiben zurückspringen. Fragt interaktiv, wie viele
  (1 = nur diese Scheibe erneut, 2 = auch die davor, ...), bricht den
  laufenden Druckauftrag ebenfalls sofort ab und springt dann zurück.
- **`q`** — Session beenden (der aktuelle Druck läuft noch normal zu Ende).

In der Oberfläche (`print_gui.py`/`.exe`) gibt es dafür die drei Knöpfe im
Druckstatus; die Tasten `w`/`b`/`q` funktionieren dort ebenfalls. Bei
„Zurückspringen“ fragt ein kleines Fenster nach der Anzahl und zeigt dabei an,
mit welcher Scheibe es danach weitergeht. „Druck beenden“ fragt zur Sicherheit
noch einmal nach.

**Laufenden Druck abbrechen (`w`/`b`)**: Der TM-U950 ist alt genug, dass er
NICHT Teil des modernen ESC/POS-Echtzeitbefehlssatzes ist — es gibt dafür
keinen dokumentierten Sofort-Abbruch-Befehl wie bei neueren Epson-Druckern
(geprüft gegen Epsons aktuelle ESC/POS-Referenz, die den TM-U950 gar nicht
mehr auflistet). Was dokumentiert und nutzbar ist: `ESC @` verwirft, was der
Drucker schon empfangen, aber noch NICHT gedruckt hat. Bereits physisch
gedruckte Zeilen bleiben auf dem Papier stehen — je schneller `w`/`b`
gedrückt wird, desto weniger Schrott landet noch auf der Scheibe. Reagiert
der Drucker gar nicht mehr (echter Papierstau), hilft nur die mechanische
Entnahme (Drucker aus, Deckel öffnen, siehe Drucker-Handbuch "Removing
Jammed Paper").

## 9. Vorschau vor dem Druck

In der **Oberfläche** ist die Vorschau immer sichtbar und zeigt jede Scheibe
**genau so, wie sie gedruckt wird**: mit ◀/▶ (oder Bild-Auf/-Ab) blättern,
eine Scheibennummer eintippen oder einen Stand in der Übersicht anklicken.
**„Ab dieser Scheibe drucken“** übernimmt die angezeigte Scheibe als
Startpunkt. Während des Drucks folgt die Vorschau automatisch der aktuellen
Scheibe.

In der **Konsolen-Variante** bietet die App nach der Stand-Übersicht an, jede
einzelne Scheibe in einer Vollbild-Vorschau durchzublättern:

```
Pfeiltasten / Bild-Auf/-Ab / [j][k]   eine Scheibe vor/zurück
Pos1 / Ende                           zur ersten/letzten Scheibe
Zahl(en) + [Enter]                    direkt zu dieser Scheibennummer springen
[Enter] (ohne Zahl) / [Esc] / [q]      Vorschau schließen, OHNE zu drucken
[s]                                    Vorschau schließen und DRUCK AB DIESER
                                        (aktuell angezeigten) Scheibe starten
```

`[s]` ist der schnelle Weg, um mitten in der Übersicht gezielt eine
bestimmte Scheibe als Startpunkt auszuwählen (statt `--start-index` von Hand
auszurechnen und erneut zu starten). Die abschließende Bestätigung ("Passt
diese Reihenfolge? Jetzt starten?") kommt trotzdem noch als letzte
Sicherheitsfrage. Mit `--no-preview` lässt sich die Vorschau ganz
überspringen, mit `--simple-input` wird sie automatisch übersprungen.

## 10. Unterbrochene oder bereits fertige Sessions fortsetzen

Der Druckfortschritt wird nach jeder einzelnen Scheibe in
`state/lp_plan_state.json` bzw. `state/lg_plan_state.json` gespeichert
(außer mit `--no-save`).

- **Unterbrochene Session** (z. B. Programm/PC wurde mitten im Wettkampf
  beendet): Wird die App danach ohne `--club-a`/`--club-b`/`--paarungen`
  erneut gestartet, erkennt sie den unfertigen Stand automatisch und fragt
  direkt "Unterbrochene Session gefunden ... Genau dort fortsetzen?" — OHNE
  die Einstellungen erneut abzufragen. Bei "Fortsetzen" geht es exakt an der
  Stelle weiter, an der aufgehört wurde.
- **Bereits vollständig gedruckte Session**: Passt die eingegebene/übergebene
  Konfiguration exakt zu einem bereits fertig gedruckten Plan, fragt die App
  "Trotzdem komplett von Scheibe 1 an neu drucken?" — bei "Ja" beginnt der
  Druck einfach wieder bei Scheibe 1 (z. B. für eine Wiederholung mit
  identischen Einstellungen), ohne dass Dateien von Hand gelöscht werden
  müssten.
- Für eine einzelne, gezielte Scheibe (unabhängig vom gespeicherten
  Fortschritt) hilft weiterhin `--start-index N`.

In der **Oberfläche** passiert das ohne Rückfragen: Beim Öffnen werden die
Werte des gespeicherten Fortschritts vorbelegt, und bei einem unterbrochenen
Druck steht „Start ab Scheibe“ schon auf der nächsten offenen Scheibe (blauer
Hinweis „Unterbrochener Druck erkannt“). Werden die Einstellungen geändert,
warnt ein Hinweis, dass der unterbrochene Druck beim Start verworfen würde –
„Unterbrochenen Druck übernehmen“ stellt die gespeicherten Werte wieder her.
Ist der Wettkampf schon vollständig gedruckt, beginnt ein erneuter Start bei
Scheibe 1 (mit Hinweis). Eine gezielte Startscheibe lässt sich jederzeit bei
„Start ab Scheibe“ eintragen oder über die Vorschau wählen.

## 11. Als eigenständige `.exe` bauen (kein Python nötig)

Damit am Schießstand nicht erst Python + Abhängigkeiten installiert werden
müssen, lässt sich aus der Oberfläche `print_gui.py` mit
[PyInstaller](https://pyinstaller.org/) eine einzelne `TMU950_Druck.exe`
bauen, die per **Doppelklick** startet (ohne Konsolenfenster).

**Einmalig bauen** (auf einem PC mit Python — danach läuft die `.exe` auch
auf PCs OHNE installiertes Python):

```
build_exe.bat
```

Das Skript legt eine eigene Build-Umgebung `build_tmp\venv` an, installiert
dort PyInstaller und alle Abhängigkeiten und baut die `.exe` direkt in diesen
Ordner (neben `config.ini` und `templates\`). Danach genügt ein
**Doppelklick auf `TMU950_Druck.exe`** — alles Weitere wird im Fenster
eingestellt (siehe Abschnitt 7), ohne dass Python auf dem jeweiligen Rechner
installiert sein muss. Für den Alltag am Stand am besten eine Verknüpfung auf
den Desktop legen (Rechtsklick auf die `.exe` → *Weitere Optionen anzeigen* →
*Senden an* → *Desktop (Verknüpfung erstellen)*). **Wichtig:** `config.ini`,
`templates\` und `state\` müssen im selben Ordner wie die `.exe` bleiben —
die `.exe` sucht sie immer dort, egal von wo aus sie gestartet wird.

Die Konsolen-Variante `print_session.py` ist nicht in der `.exe` enthalten und
läuft weiterhin mit Python (siehe Abschnitt 7, „Alternativ“).

Nach einem Update von `print_gui.py` (z. B. durch eine neue Version aus
diesem Chat) einfach `build_exe.bat` erneut ausführen — die alte `.exe` wird
dabei überschrieben. `build_exe.bat`/`build_tmp\` selbst müssen nicht mit auf
andere Rechner kopiert werden, nur die fertige `TMU950_Druck.exe` plus
`config.ini`, `templates\` (und, falls vorhanden, `state\`).

**Virenscanner-Fehlalarm (`Trojan:Win32/Wacatac.B!ml`)**: Windows Defender
hat eine mit dem Standard-PyInstaller gebaute `.exe` nach dem Herunterladen
und Entpacken als Trojaner in Quarantäne verschoben. Das ist ein bekannter
Fehlalarm: Das `!ml` steht für eine reine Machine-Learning-Einschätzung, und
Schadsoftware nutzt denselben vorkompilierten PyInstaller-Bootloader. Deshalb
kompiliert `build_exe.bat` den Bootloader selbst und bettet
`version_info.txt` als Datei-Eigenschaften ein. Dafür müssen die
[Visual Studio Build Tools](https://visualstudio.microsoft.com/de/visual-cpp-build-tools/)
mit der Workload „Desktopentwicklung mit C++" installiert sein. Fehlen sie,
baut das Skript mit einer Warnung trotzdem, aber mit dem Standard-Bootloader.
(Wurden die Build Tools erst nachträglich installiert: `build_tmp\` löschen
und neu bauen.)

Eine Garantie gegen Fehlalarme ist das nicht, nur eine signierte `.exe`
wäre das. Meldet Defender die `.exe` trotzdem:

- In Windows-Sicherheit → Schutzverlauf den Eintrag öffnen →
  *Aktionen* → *Auf Gerät zulassen* (nur für die selbst gebaute `.exe`!).
- Die Datei als Fehlalarm melden:
  <https://www.microsoft.com/wdsi/filesubmission>
- Die `.exe` per USB-Stick statt per Download/Mail/Cloud übertragen oder
  `build_exe.bat` direkt auf dem Ziel-PC ausführen. Dateien ohne
  „Aus dem Internet"-Markierung prüft Defender deutlich weniger streng.

## 12. Bekannte Stolpersteine

- **Umlaute**: Standardmäßig wird `cp850` (klassische Epson/DOS-Codepage)
  verwendet. Erscheinen ä/ö/ü/ß falsch, in `config.ini` testweise
  `encoding = cp437` probieren.
- **Slip-Station**: Wird vor JEDEM einzelnen Ausdruck neu per `ESC c 0 4`
  gewählt (nicht nur einmal beim Verbindungsaufbau) — der Drucker fällt nach
  dem Auswurf offenbar auf die Bon/Journal-Rolle zurück, sonst würde jeder
  zweite Ausdruck auf dem Bon statt auf der Scheibe landen (so beobachtet).
- **Slip-Auswurf ist laut Handbuch nur vorwärts möglich** — der TM-U950
  wirft die Scheibe/das Band nach dem Druck aus derselben Öffnung nach vorn
  aus, ein Zurückziehen/umgekehrter Auswurf ist bei diesem Druckwerk nicht
  vorgesehen (Handbuchhinweis: „Slip paper is ejected in the forward
  direction only"). Das ist eine mechanische Eigenschaft des Druckwerks,
  keine per Software änderbare Einstellung.
- **Statusabfrage für "Zyklus fertig"**: `DLE EOT n` (klassischer
  ESC/POS-Statusbefehl) ändert sich beim TM-U950 über einen kompletten
  Einlege-/Druck-/Auswurfzyklus nachweislich NICHT (laut Handbuch die
  falsche Befehlsklasse für den Scheiben-Status). Verwendet wird
  stattdessen `GS r 3` — die Antwort "flacht" am Ende eines Zyklus auf ein
  einzelnes `0x00`-Byte ab, das ist das Signal für "fertig, weiter zur
  nächsten Scheibe" (siehe `tmu950.py: wait_for_slip_cycle`).
- **Kein Sofort-Abbruch-Befehl für laufende Druckjobs**: siehe Abschnitt 8 —
  der TM-U950 unterstützt den modernen ESC/POS-Echtzeit-Abbruchbefehl nicht,
  `ESC @` (Puffer leeren) ist der bestmögliche Ersatz.
- **USB-Seriell-Adapter**: Ein CH340-basierter Adapter zeigte sich bei uns
  instabil (Port blieb nach dem Druck hängen, nur durch Aus-/Wiedereinstecken
  lösbar — ein bekannter CH340/Windows-Treiber-Aussetzer, kein Software-Bug).
  Mit einem FTDI-basierten Adapter lief es zuverlässig.
- **PyInstaller/`.exe`**: Läuft die gebaute `.exe` auf einem ANDEREN PC nicht
  (z. B. fehlendes Modul), am einfachsten `build_exe.bat` direkt auf diesem
  Ziel-PC ausführen, statt die `.exe` von einem anderen Rechner zu kopieren —
  PyInstaller bündelt fest gegen die Python-Version, mit der gebaut wurde.

## 13. Mögliche nächste Schritte

- Weitere Profile (z. B. RWK, Gaudamen) analog zu LP/LG in `config.ini`
  ergänzen, sobald deren Bandlänge bekannt ist.
- Fehlerbehandlung, die vor dem Druck automatisch Papier-/Cover-Status
  prüft und bei Problemen abbricht statt "blind" zu drucken.
- Explizites benanntes Speichern/Laden mehrerer Wettkampf-Konfigurationen
  (über die automatische "letzte Werte als Vorschlag"-Funktion hinaus).
