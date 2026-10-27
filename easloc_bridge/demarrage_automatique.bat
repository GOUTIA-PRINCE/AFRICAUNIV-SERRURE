@echo off
rem Demarre le pont automatiquement (sans fenetre) a chaque ouverture de session Windows
cd /d "%~dp0"
for /f "delims=" %%i in ('py -3-32 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set PYW=%%i
if not exist "%PYW%" (
  echo Python 32 bits introuvable. Installez "Windows installer (32-bit)" depuis python.org.
  pause
  exit /b 1
)
schtasks /Create /F /TN "EASLOC Pont Odoo" /TR "\"%PYW%\" \"%~dp0easloc_bridge.py\"" /SC ONLOGON /RL LIMITED
schtasks /Run /TN "EASLOC Pont Odoo"
echo.
echo Tache "EASLOC Pont Odoo" creee et demarree. Journal : %~dp0easloc_bridge.log
pause
