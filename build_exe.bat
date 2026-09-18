@echo off
REM build_exe.bat
REM ----------------------------------------------------------------------
REM Baut aus print_gui.py (der grafischen Oberflaeche) eine einzelne,
REM per Doppelklick startbare TMU950_Druck.exe (per PyInstaller, ohne
REM Konsolenfenster) - damit spaeter niemand mehr Python installieren oder
REM "pip install" ausfuehren muss. Einmal hier im Ordner doppelklicken (oder
REM in einer Eingabeaufforderung ausfuehren) - danach liegt TMU950_Druck.exe
REM direkt in diesem Ordner, neben config.ini und templates\.
REM
REM Zum NEU-Bauen (z.B. nach einem Update von print_gui.py) einfach
REM erneut ausfuehren - die alte .exe wird dabei ueberschrieben.
REM
REM Gegen Virenscanner-Fehlalarme (Windows Defender meldete die .exe als
REM "Trojan:Win32/Wacatac.B!ml", siehe README Abschnitt 11) wird PyInstaller
REM in einer eigenen Build-Umgebung (build_tmp\venv) mit SELBST kompiliertem
REM Bootloader installiert, und die .exe bekommt Datei-Eigenschaften aus
REM version_info.txt. Zum Kompilieren werden die "Visual Studio Build Tools"
REM (Workload "Desktopentwicklung mit C++") benoetigt - fehlen sie, wird mit
REM Warnung auf den mitgelieferten Standard-Bootloader zurueckgegriffen.
REM ----------------------------------------------------------------------
setlocal
cd /d "%~dp0"
set "VENV=build_tmp\venv"
set "PY=%VENV%\Scripts\python.exe"

echo.
echo === 1/4: Build-Umgebung %VENV% vorbereiten ===
REM Eine abgebrochene Installation kann pip in der venv beschaedigen -
REM dann die venv einfach komplett neu anlegen.
if exist "%PY%" (
    "%PY%" -m pip --version >nul 2>&1
    if errorlevel 1 rmdir /s /q "%VENV%"
)
if not exist "%PY%" (
    py -m venv "%VENV%"
    if errorlevel 1 goto :fehler
)

echo.
echo === 2/4: PyInstaller mit selbst kompiliertem Bootloader installieren ===
set "PYINSTALLER_COMPILE_BOOTLOADER=1"
"%PY%" -m pip install --upgrade --no-binary pyinstaller pyinstaller
if errorlevel 1 (
    echo.
    echo WARNUNG: Bootloader konnte nicht kompiliert werden - fehlen die
    echo Visual Studio Build Tools mit "Desktopentwicklung mit C++"?
    echo Es wird der Standard-Bootloader verwendet, die .exe wird dann
    echo eher von Virenscannern faelschlich als Trojaner gemeldet.
    echo.
    "%PY%" -m pip install --upgrade pyinstaller
    if errorlevel 1 goto :fehler
)

echo.
echo === 3/4: Abhaengigkeiten installieren ===
"%PY%" -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto :fehler

echo.
echo === 4/4: TMU950_Druck.exe bauen ===
"%PY%" -m PyInstaller --onefile --windowed --noupx --name TMU950_Druck ^
    --version-file "%~dp0version_info.txt" ^
    --collect-all serial ^
    --distpath . --workpath build_tmp --specpath build_tmp ^
    print_gui.py
if errorlevel 1 goto :fehler

echo.
echo ================================================================
echo FERTIG: TMU950_Druck.exe liegt jetzt in diesem Ordner.
echo Starten per Doppelklick - alles Weitere im Fenster.
echo config.ini, templates\ und state\ muessen dabei im selben Ordner
echo wie die .exe bleiben.
echo ================================================================
pause
exit /b 0

:fehler
echo.
echo Beim Bauen ist ein Fehler aufgetreten (siehe Meldungen oben).
echo Tritt er wiederholt auf: Ordner build_tmp\ loeschen und erneut bauen.
pause
exit /b 1
