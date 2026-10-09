@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.11 or later from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

py -3 -m pip install hidapi pystray pillow pyinstaller
if errorlevel 1 goto :fail

py -3 -m unittest discover -s tests -q
if errorlevel 1 goto :fail

py -3 single_instance_smoke_windows.py
if errorlevel 1 goto :fail

py -3 gui_smoke_windows.py
if errorlevel 1 goto :fail

py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name WacomSwipe ^
  --icon WacomSwipe.ico --add-data "WacomSwipe.ico;." ^
  --hidden-import hid --collect-all pystray --collect-all PIL WacomSwipe.py
if errorlevel 1 goto :fail

echo.
echo READY: dist\WacomSwipe.exe
pause
exit /b 0

:fail
echo Build failed. Review the errors above.
pause
exit /b 1
