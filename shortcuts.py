"""Win32 virtual-key shortcut descriptors. This module has no Windows dependencies.

Descriptors look like 'Shortcut: Ctrl+Alt+K'. Legacy Italian descriptors are
accepted on load for backward compatibility.
"""
from __future__ import annotations

PREFIX = 'Shortcut: '
LEGACY_PREFIX = 'Scorciatoia: '
MODIFIERS = {'Ctrl': 0x11, 'Alt': 0x12, 'Shift': 0x10, 'Win': 0x5B}
MODIFIER_NAMES = {
    0x10: 'Shift', 0xA0: 'Shift', 0xA1: 'Shift',
    0x11: 'Ctrl', 0xA2: 'Ctrl', 0xA3: 'Ctrl',
    0x12: 'Alt', 0xA4: 'Alt', 0xA5: 'Alt',
    0x5B: 'Win', 0x5C: 'Win',
}
MODIFIER_CODES = set(MODIFIER_NAMES)
SPECIAL = {
    'Backspace': 0x08, 'Tab': 0x09, 'Enter': 0x0D, 'Pause': 0x13,
    'CapsLock': 0x14, 'Escape': 0x1B, 'Space': 0x20,
    'PageUp': 0x21, 'PageDown': 0x22, 'End': 0x23, 'Home': 0x24,
    'Left': 0x25, 'Up': 0x26, 'Right': 0x27, 'Down': 0x28,
    'PrintScreen': 0x2C, 'Insert': 0x2D, 'Delete': 0x2E,
    **{f'F{i}': 0x6F + i for i in range(1, 25)},
    **{f'Num{i}': 0x60 + i for i in range(10)},
    'NumMultiply': 0x6A, 'NumAdd': 0x6B, 'NumSubtract': 0x6D,
    'NumDecimal': 0x6E, 'NumDivide': 0x6F,
}
NAME_TO_VK = {**SPECIAL, **{chr(c): c for c in range(ord('A'), ord('Z') + 1)}, **{str(c): ord(str(c)) for c in range(10)}}
VK_TO_NAME = {v: k for k, v in NAME_TO_VK.items()}


def normalize_descriptor(action):
    if isinstance(action, str) and action.startswith(LEGACY_PREFIX):
        return PREFIX + action[len(LEGACY_PREFIX):]
    return action


def name_for_vk(vk):
    if not 1 <= vk <= 254:
        raise ValueError('Invalid key code')
    return VK_TO_NAME.get(vk, f'VK_{vk:02X}')


def parse_shortcut(action):
    action = normalize_descriptor(action)
    if not isinstance(action, str) or not action.startswith(PREFIX):
        return None
    tokens = action[len(PREFIX):].split('+')
    if not 1 <= len(tokens) <= 5 or not tokens[-1]:
        raise ValueError('Empty shortcut')
    if all(token in MODIFIERS for token in tokens):
        if len(tokens) != len(set(tokens)) or tokens != [m for m in MODIFIERS if m in tokens]:
            raise ValueError('Invalid modifiers')
        return tuple(MODIFIERS[m] for m in tokens)
    mods = tokens[:-1]
    if len(mods) != len(set(mods)) or any(m not in MODIFIERS for m in mods):
        raise ValueError('Invalid modifiers')
    if mods != [m for m in MODIFIERS if m in mods]:
        raise ValueError('Invalid modifier order')
    keyname = tokens[-1]
    if keyname in NAME_TO_VK:
        vk = NAME_TO_VK[keyname]
    elif keyname.startswith('VK_') and len(keyname) == 5:
        try:
            vk = int(keyname[3:], 16)
        except ValueError as exc:
            raise ValueError('Invalid VK name') from exc
        if name_for_vk(vk) != keyname:
            raise ValueError('Non-canonical VK name')
    else:
        raise ValueError('Unsupported key')
    if vk in MODIFIER_CODES or vk in (0x00, 0xFF):
        raise ValueError('Main key cannot be a modifier')
    return tuple([MODIFIERS[m] for m in mods] + [vk])


def is_shortcut(action):
    try:
        return parse_shortcut(action) is not None
    except ValueError:
        return False


def record_modifier_keys(vks):
    names = {MODIFIER_NAMES[vk] for vk in vks if vk in MODIFIER_NAMES}
    if not names:
        return None
    return PREFIX + '+'.join(name for name in MODIFIERS if name in names)


def record_shortcut(vk, state=0, *, held_modifiers=None):
    """Convert a recorded keypress to a shortcut descriptor.

    On Windows, Tk's 0x0008 (Mod1) is NumLock, NOT Alt. Tk reports Alt with
    0x20000; the GUI normally supplies physical modifiers read from
    GetAsyncKeyState, which is more reliable across keyboard layouts.
    """
    vk = int(vk)
    if vk in MODIFIER_CODES:
        return None
    if held_modifiers is not None:
        names = {MODIFIER_NAMES[k] for k in held_modifiers if k in MODIFIER_NAMES}
    else:
        names = set()
        if state & 0x0004:
            names.add('Ctrl')
        if state & 0x00020000:
            names.add('Alt')
        if state & 0x0001:
            names.add('Shift')
    return PREFIX + '+'.join([m for m in MODIFIERS if m in names] + [name_for_vk(vk)])


class KeyHoldTracker:
    """Reference-count modifiers and keys shared by concurrent pad shortcuts."""
    def __init__(self):
        self.counts = {}

    def changes(self, keys, pressed):
        output = []
        for vk in (keys if pressed else reversed(keys)):
            count = self.counts.get(vk, 0)
            if pressed:
                if count == 0:
                    output.append((vk, True))
                self.counts[vk] = count + 1
            elif count:
                if count == 1:
                    output.append((vk, False))
                    del self.counts[vk]
                else:
                    self.counts[vk] = count - 1
        return output
