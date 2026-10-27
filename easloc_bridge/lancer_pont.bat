@echo off
rem Lance le pont Odoo <-> encodeur EASLOC (fenetre visible, pour les essais)
cd /d "%~dp0"
py -3-32 easloc_bridge.py %*
pause
