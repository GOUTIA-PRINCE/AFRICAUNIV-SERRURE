@echo off
rem Verifie Python 32 bits, proRFL.dll et l'encodeur USB (l'encodeur doit biper)
cd /d "%~dp0"
py -3-32 easloc_bridge.py --selftest
pause
