@echo off
title Instalacja - Podglad KSeF
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo BLAD: nie znaleziono Pythona.
    echo Zainstaluj Python 3.9+ z https://www.python.org/downloads/
    echo Podczas instalacji zaznacz "Add python.exe to PATH".
    pause
    exit /b 1
)
echo === Python ===
%PY% --version

echo.
echo === Test ===
echo Program uzywa tylko biblioteki standardowej - nic nie trzeba instalowac.
%PY% -c "import tkinter; print('tkinter OK')"
if errorlevel 1 (
    echo BLAD: brak tkinter - zainstaluj Pythona ponownie
    echo z zaznaczona opcja "tcl/tk and IDLE".
    pause
    exit /b 1
)
%PY% podglad_ksef.py --selftest
if errorlevel 1 (
    echo BLAD testu programu.
    pause
    exit /b 1
)

echo.
echo Gotowe. Program uruchamiasz plikiem uruchom.bat
pause
