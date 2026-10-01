@echo off
cd /d "%~dp0"
py -3 --version >nul 2>nul && (start "" pyw -3 podglad_ksef.py) || (start "" pythonw podglad_ksef.py)
