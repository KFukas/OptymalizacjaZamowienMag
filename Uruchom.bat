@echo off
REM Uruchamia caly projekt: WMS API + symulator, frontend i agenta planujacego.
REM Dwuklik albo z wiersza polecen, np.:  Uruchom.bat --nowy-dzien --speed 60   (pomoc: Uruchom.bat --help)
chcp 65001 >nul
cd /d "%~dp0"
title Magazyn Centralny Alfa

if not exist ".venv\Scripts\python.exe" (
  echo [start] Pierwsze uruchomienie - tworze srodowisko Python 3.12 ^(kilka minut^)...
  py -3.12 -m venv .venv || goto :blad
  ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :blad
  ".venv\Scripts\python.exe" -m pip install -e ".[dev]" || goto :blad
)

set PYTHONUTF8=1
".venv\Scripts\python.exe" scripts\start_all.py %*
pause
exit /b 0

:blad
echo [start] Nie udalo sie przygotowac srodowiska. Wymagany Python 3.12 ^(py -3.12^) i Node.js 20+.
pause
exit /b 1
