# WacomSwipe 1.0

A lightweight Windows 11 utility for the **Wacom Intuos CTL-4100WL**, combining precise pen control with touch-style scrolling.

## Features

- Automatic USB detection and reconnection.
- Absolute pen mapping to the primary monitor, a selected display, or the entire desktop; optional aspect-ratio preservation.
- Touch-like swipes with kinetic scrolling, tap-to-stop, and configurable hold-to-drag.
- Left, right, and middle mouse clicks using the pen and side buttons.
- Four configurable tablet buttons with keyboard shortcut recording.
- System tray, single-instance protection, and automatic Windows sign-in startup with optional minimized launch.

## Build

On Windows 11, install **Python 3.11+** and run `build-windows.bat`. The script runs the tests and creates `dist\WacomSwipe.exe`.

To run from source, use `run-source.bat`.
