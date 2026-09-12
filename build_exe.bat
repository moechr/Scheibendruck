@echo off
REM build_exe.bat
REM ----------------------------------------------------------------------
REM Baut aus print_session.py eine einzelne, direkt startbare
REM TMU950_Druck.exe (per PyInstaller) - damit spaeter niemand mehr Python
REM installieren oder "pip install" ausfuehren muss, um die Drucksession zu
REM starten. Einmal hier im tmu950_suite-Ordner doppelklicken (oder in einer
REM Eingabeaufforderung ausfuehren) - danach liegt TMU950_Druck.exe direkt
REM in diesem Ordner, neben config.ini und templates\.
REM
REM Zum NEU-Bauen (z.B. nach einem Update von print_session.py) einfach
REM erneut ausfuehren - die alte .exe wird dabei ueberschrieben.
REM ----------------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo.
echo === 1/3: pip/PyInstaller aktualisieren ===
py -m pip install --upgrade pip
if errorlevel 1 goto :fehler

echo.
echo === 2/3: Abhaengigkeiten + PyInstaller installieren ===
py -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fehler

echo.
echo === 3/3: TMU950_Druck.exe bauen ===
py -m PyInstaller --onefile --console --name TMU950_Druck ^
    --collect-all prompt_toolkit --collect-all rich --collect-all serial ^
    --distpath . --workpath build_tmp --specpath build_tmp ^
    print_session.py
if errorlevel 1 goto :fehler

echo.
echo ================================================================
echo FERTIG: TMU950_Druck.exe liegt jetzt in diesem Ordner.
echo Aufruf z.B.:
echo   TMU950_Druck.exe COM5 templates\lp_paarung.txt
echo config.ini, templates\ und state\ muessen dabei im selben Ordner
echo wie die .exe bleiben.
echo ================================================================
pause
exit /b 0

:fehler
echo.
echo Beim Bauen ist ein Fehler aufgetreten (siehe Meldungen oben).
pause
exit /b 1
