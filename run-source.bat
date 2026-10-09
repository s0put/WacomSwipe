@echo off
cd /d "%~dp0"
py -3 -m pip install hidapi pystray pillow
py -3 WacomSwipe.py
pause
