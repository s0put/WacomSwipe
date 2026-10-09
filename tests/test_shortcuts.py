"""Shortcut recording, settings migration and configurable hold-time tests."""
import json
import queue
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import shortcuts
import startup


class ShortcutTests(unittest.TestCase):
    def test_record_single_key(self):
        self.assertEqual(shortcuts.record_shortcut(ord('G'), 0), 'Shortcut: G')
        self.assertEqual(shortcuts.parse_shortcut('Shortcut: G'), (0x47,))

    def test_record_ctrl_alt_shift(self):
        value = shortcuts.record_shortcut(ord('K'), 0x4 | 0x20000 | 0x1)
        self.assertEqual(value, 'Shortcut: Ctrl+Alt+Shift+K')
        self.assertEqual(shortcuts.parse_shortcut(value), (0x11, 0x12, 0x10, 0x4B))

    def test_numlock_no_longer_generates_alt(self):
        # Tk/Windows Mod1 (0x8) can represent NumLock, NOT Alt.
        self.assertEqual(shortcuts.record_shortcut(ord('C'), 0x4 | 0x8),
                         'Shortcut: Ctrl+C')
        self.assertEqual(shortcuts.record_shortcut(ord('C'), 0x1 | 0x8),
                         'Shortcut: Shift+C')
        self.assertEqual(shortcuts.record_shortcut(ord('C'), 0x8),
                         'Shortcut: C')

    def test_physical_modifiers_override_tk_state(self):
        # The GUI supplies VKs currently held via GetAsyncKeyState.
        self.assertEqual(shortcuts.record_shortcut(0x43, 0x20000 | 0x8,
                                                   held_modifiers={0x11}),
                         'Shortcut: Ctrl+C')
        self.assertEqual(shortcuts.record_shortcut(0x43, 0x8,
                                                   held_modifiers={0x10}),
                         'Shortcut: Shift+C')
        self.assertEqual(shortcuts.record_shortcut(0x43, 0x8,
                                                   held_modifiers={0x11, 0x12}),
                         'Shortcut: Ctrl+Alt+C')
        self.assertEqual(shortcuts.record_shortcut(0x43, 0x8,
                                                   held_modifiers=set()),
                         'Shortcut: C')

    def test_record_oem_italian_keyboard(self):
        value = shortcuts.record_shortcut(0xBA, 0x4)
        self.assertEqual(value, 'Shortcut: Ctrl+VK_BA')
        self.assertEqual(shortcuts.parse_shortcut(value), (0x11, 0xBA))

    def test_modifier_alone_or_pair_can_be_assigned(self):
        self.assertIsNone(shortcuts.record_shortcut(0x11, 0))
        self.assertIsNone(shortcuts.record_shortcut(0xA4, 0x8))
        self.assertEqual(shortcuts.record_modifier_keys({0x11}), 'Shortcut: Ctrl')
        self.assertEqual(shortcuts.record_modifier_keys({0x11, 0xA0}), 'Shortcut: Ctrl+Shift')
        self.assertEqual(shortcuts.parse_shortcut('Shortcut: Ctrl+Shift'), (0x11, 0x10))

    def test_reject_malformed_shortcuts(self):
        for item in ('Shortcut: ', 'Shortcut: Ctrl+Ctrl+A',
                     'Shortcut: Alt+Ctrl+A', 'Shortcut: Win+Ctrl',
                     'Shortcut: Ctrl+VK_FF', 'Shortcut: Ctrl+VK_GG'):
            with self.subTest(item=item):
                self.assertFalse(shortcuts.is_shortcut(item))

    def test_shared_modifier_does_not_release_early(self):
        tracker = shortcuts.KeyHoldTracker()
        self.assertEqual(tracker.changes((0x11, 0x43), True), [(0x11, True), (0x43, True)])
        self.assertEqual(tracker.changes((0x11, 0x56), True), [(0x56, True)])
        self.assertEqual(tracker.changes((0x11, 0x43), False), [(0x43, False)])
        self.assertEqual(tracker.changes((0x11, 0x56), False), [(0x56, False), (0x11, False)])
        self.assertEqual(tracker.counts, {})

    def test_v06_migrates_to_v07(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder, 'settings.json')
            target.write_text(json.dumps({'settings_version': 2,
                'bindings': {'Tasto 1': core.SWIPE_TOGGLE, 'Tasto 3': 'Pagina su'},
                'swipe_enabled': False}), encoding='utf-8')
            with patch.object(core, 'settings_path', return_value=target):
                data = core.load_settings()
            self.assertEqual(data['bindings']['Pad button 1'], core.SWIPE_TOGGLE)
            self.assertEqual(data['bindings']['Pad button 3'], 'Page Up')
            self.assertEqual(data['press_delay_ms'], 450)
            self.assertFalse(data['tray_enabled'])
            self.assertFalse(data['launch_minimized'])
            self.assertFalse(data['swipe_enabled'])

    def test_shortcut_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(core, 'settings_path', return_value=Path(folder, 'settings.json')):
                data = core.defaults()
                data['bindings']['Pad button 4'] = 'Shortcut: Ctrl+Shift+S'
                data['press_delay_ms'] = 600
                data['tray_enabled'] = True
                data['launch_minimized'] = True
                core.save_settings(data)
                restored = core.load_settings()
                self.assertEqual(restored['bindings']['Pad button 4'], 'Shortcut: Ctrl+Shift+S')
                self.assertEqual(restored['press_delay_ms'], 600)
                self.assertTrue(restored['tray_enabled'])
                self.assertTrue(restored['launch_minimized'])

    def test_reject_invalid_delay_and_unsupported_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'settings.json')
            path.write_text(json.dumps({'settings_version': 3, 'press_delay_ms': 777,
                'bindings': {'Tasto 1': 'Shortcut: BAD+BAD',
                             'Penna 1': 'Shortcut: Ctrl+C'}}))
            with patch.object(core, 'settings_path', return_value=path):
                data = core.load_settings()
                self.assertEqual(data['press_delay_ms'], 450)
                self.assertEqual(data['bindings']['Pad button 1'], core.DEFAULT_BINDINGS['Pad button 1'])
                self.assertEqual(data['bindings']['Pen button 1'], core.RIGHT_MOD)

    def test_win_startup_exe_command(self):
        cmd = startup.startup_command(executable=r'C:\Apps\Wacom Swipe\WacomSwipe.exe', frozen=True)
        self.assertEqual(cmd, '"C:\\Apps\\Wacom Swipe\\WacomSwipe.exe" --autostart')


if __name__ == '__main__':
    unittest.main()
