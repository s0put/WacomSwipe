"""Device protocol decoding, settings and coordinate math.

Intuos V2 report layout is documented in OpenTabletDriver (LGPL-3.0).
This is an independent small implementation for the CTL-4100WL.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from shortcuts import is_shortcut, normalize_descriptor

VENDOR_ID = 0x056A
PRODUCT_IDS = {0x0376, 0x0377, 0x03C5}
MAX_X = 15200
MAX_Y = 9500

KEYS = (
    "Tip",
    "Pen button 1",
    "Pen button 2",
    "Pad button 1",
    "Pad button 2",
    "Pad button 3",
    "Pad button 4",
)
PRESS_DELAYS = (150, 250, 350, 450, 600, 800)
AUTO_MONITOR = 'Automatic (primary monitor)'
FULL_DESKTOP = 'Entire desktop'
SWIPE_TOGGLE = 'Swipe gesture on/off'
SWIPE_HOLD = 'Swipe gesture (hold)'
RIGHT_MOD = 'Right click modifier'
MIDDLE_MOD = 'Middle click modifier'
ACTIONS = (
    'None', 'Left click', 'Right click', 'Middle click',
    RIGHT_MOD, MIDDLE_MOD, SWIPE_TOGGLE, SWIPE_HOLD,
    'Enter', 'Esc', 'Space', 'Tab', 'Page Up', 'Page Down',
    'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'
)
DEFAULT_BINDINGS = {
    'Tip': 'Left click',
    'Pen button 1': RIGHT_MOD,
    'Pen button 2': MIDDLE_MOD,
    'Pad button 1': SWIPE_TOGGLE,
    'Pad button 2': 'Esc',
    'Pad button 3': 'Page Up',
    'Pad button 4': 'Page Down',
}

LEGACY_KEYS = {
    'Punta': 'Tip',
    'Penna 1': 'Pen button 1',
    'Penna 2': 'Pen button 2',
    'Tasto 1': 'Pad button 1',
    'Tasto 2': 'Pad button 2',
    'Tasto 3': 'Pad button 3',
    'Tasto 4': 'Pad button 4',
}
LEGACY_ACTIONS = {
    'Nessuno': 'None',
    'Click sinistro': 'Left click',
    'Click destro': 'Right click',
    'Click centrale': 'Middle click',
    'Invio': 'Enter',
    'Spazio': 'Space',
    'Pagina su': 'Page Up',
    'Pagina giù': 'Page Down',
    'Touch on/off': SWIPE_TOGGLE,
    'Swipe touch (tieni)': SWIPE_HOLD,
    'Swipe gesture (tieni)': SWIPE_HOLD,
    'Modificatore click destro': RIGHT_MOD,
    'Modificatore click centrale': MIDDLE_MOD,
}
LEGACY_MONITORS = {
    'Automatico (monitor principale)': AUTO_MONITOR,
    'Desktop completo': AUTO_MONITOR,  # keep the v0.9 migration semantics
}


def translate_key_name(name):
    return LEGACY_KEYS.get(name, name)


def translate_action_name(name):
    if not isinstance(name, str):
        return name
    return LEGACY_ACTIONS.get(name, normalize_descriptor(name))


def tip_action(physical, bindings):
    """Barrel buttons act as modifiers only when tip touches the surface.

    If both are pressed, right click takes priority over middle click.
    """
    if 'Pen button 1' in physical and bindings.get('Pen button 1') == RIGHT_MOD:
        return 'Right click'
    if 'Pen button 2' in physical and bindings.get('Pen button 2') == MIDDLE_MOD:
        return 'Middle click'
    action = bindings.get('Tip', 'Left click')
    return action if action not in (SWIPE_TOGGLE, SWIPE_HOLD, RIGHT_MOD, MIDDLE_MOD) else 'None'


def choose_pen_route(swipe_requested, touch_available, mouse_area, modified):
    """Touch is eligible only after sufficient movement in client content."""
    if swipe_requested and touch_available and not mouse_area and not modified:
        return 'pending'
    return 'mouse'


def travel_exceeds(start, end, distance=13):
    return ((start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2) >= distance ** 2


def select_monitor(selection, screens, desktop):
    """Resolve monitors by stable Windows display identifier, not resolution."""
    selection = LEGACY_MONITORS.get(selection, selection)
    if selection == FULL_DESKTOP:
        return desktop
    if selection == AUTO_MONITOR:
        return next((rect for _, rect, primary in screens if primary), desktop)
    name = selection.split(' (')[0]
    return next((rect for device, rect, _ in screens if device == name),
                next((rect for _, rect, primary in screens if primary), desktop))


@dataclass(frozen=True)
class Pen:
    x: int
    y: int
    pressure: int
    proximity: bool
    barrel1: bool
    barrel2: bool

    @property
    def tip(self):
        return self.proximity and self.pressure > 8


@dataclass(frozen=True)
class Pad:
    mask: int


def parse_report(report: bytes):
    """Decode pen report 0x10 / offset report 0x1e / expresskeys 0x11.

    Some Windows vendor-driver HID collections prepend an extra byte.
    No guessed Bluetooth-specific layouts are decoded.
    """
    if len(report) >= 2 and report[0] == 0 and report[1] in (0x10, 0x11, 0x1e):
        report = report[1:]
    if len(report) >= 2 and report[0] == 0x11:
        return Pad(mask=report[1] & 0x0f)
    if len(report) >= 17 and report[0] == 0x10:
        status = report[1]
        x = int.from_bytes(report[2:5], 'little')
        y = int.from_bytes(report[5:8], 'little')
        p = int.from_bytes(report[8:10], 'little')
        if not (0 <= x <= MAX_X and 0 <= y <= MAX_Y):
            return None
        return Pen(x, y, p, bool(status & 0x20), bool(status & 0x02), bool(status & 0x04))
    if len(report) >= 18 and report[0] == 0x1e:
        status = report[2]
        x = int.from_bytes(report[3:6], 'little')
        y = int.from_bytes(report[6:9], 'little')
        p = int.from_bytes(report[9:11], 'little')
        if not (0 <= x <= MAX_X and 0 <= y <= MAX_Y):
            return None
        return Pen(x, y, p, bool(report[1] & 0x20), bool(status & 0x02), bool(status & 0x04))
    return None


def surface_to_screen(x, y, rect, keep_ratio=True):
    """Map tablet absolute coordinates to a selected Win32 monitor rectangle.

    Crop the usable tablet area centrally to avoid stretching circles.
    """
    left, top, width, height = rect
    if width <= 0 or height <= 0:
        raise ValueError("Invalid monitor size")
    tx = min(1.0, max(0.0, x / MAX_X))
    ty = min(1.0, max(0.0, y / MAX_Y))
    if keep_ratio:
        target_ratio = width / height
        tablet_ratio = MAX_X / MAX_Y
        if tablet_ratio > target_ratio:
            usable = target_ratio / tablet_ratio
            tx = min(1.0, max(0.0, (tx - (1 - usable) / 2) / usable))
        else:
            usable = tablet_ratio / target_ratio
            ty = min(1.0, max(0.0, (ty - (1 - usable) / 2) / usable))
    return round(left + (width - 1) * tx), round(top + (height - 1) * ty)


def normalize_absolute(x, y, desktop):
    l, t, w, h = desktop
    return (round(65535 * (x - l) / max(1, w - 1)),
            round(65535 * (y - t) / max(1, h - 1)))


def settings_path():
    return Path(os.getenv("LOCALAPPDATA", Path.home())) / "WacomSwipe" / "settings.json"


def defaults():
    return {
        "bindings": dict(DEFAULT_BINDINGS),
        "monitor": AUTO_MONITOR,
        "keep_ratio": True,
        "swipe_enabled": True,
        "settings_version": 4,
        "press_delay_ms": 450,
        "tray_enabled": False,
        "launch_minimized": False,
    }


def _translated_bindings(bindings):
    out = {}
    if not isinstance(bindings, dict):
        return out
    for key, value in bindings.items():
        key = translate_key_name(key)
        value = translate_action_name(value)
        # Leave shortcut descriptors untouched; shortcuts module supports legacy prefix.
        out[key] = value
    return out


def load_settings():
    data = defaults()
    path = settings_path()
    if path.exists():
        user = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(user, dict):
            bindings = _translated_bindings(user.get('bindings', {}))
            if user.get('settings_version', 0) < 2:
                bindings['Pen button 1'] = RIGHT_MOD
                bindings['Pen button 2'] = MIDDLE_MOD
            updated = {
                k: v for k, v in bindings.items()
                if k in KEYS and (v in ACTIONS or (k.startswith('Pad button ') and is_shortcut(v)))
            }
            data['bindings'].update(updated)
            if isinstance(user.get('monitor'), str):
                data['monitor'] = LEGACY_MONITORS.get(user['monitor'], user['monitor'])
            data['keep_ratio'] = bool(user.get('keep_ratio', True))
            data['swipe_enabled'] = bool(user.get('swipe_enabled', True))
            delay = user.get('press_delay_ms', 450)
            data['press_delay_ms'] = delay if type(delay) is int and delay in PRESS_DELAYS else 450
            data['tray_enabled'] = bool(user.get('tray_enabled', False))
            data['launch_minimized'] = bool(user.get('launch_minimized', False))
    return data


def save_settings(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix('.json.bak').write_bytes(path.read_bytes())
    staged = path.with_suffix('.json.tmp')
    staged.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(staged, path)
