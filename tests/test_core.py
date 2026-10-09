"""Pure logic tests, runnable on every OS."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core


class CoreTests(unittest.TestCase):
    def test_usb_pen(self):
        data = bytearray(17)
        data[0], data[1] = 0x10, 0x22
        data[2:5], data[5:8] = (7600).to_bytes(3, 'little'), (4750).to_bytes(3, 'little')
        data[8:10] = (250).to_bytes(2, 'little')
        self.assertEqual(core.parse_report(data), core.Pen(7600, 4750, 250, True, True, False))

    def test_pad(self):
        self.assertEqual(core.parse_report(bytes([0x11, 0b0101])), core.Pad(5))

    def test_prefixed_report(self):
        data = bytearray(19)
        data[1], data[2] = 0x10, 0x20
        self.assertIsInstance(core.parse_report(data), core.Pen)

    def test_invalid_report(self):
        self.assertIsNone(core.parse_report(b'\xab\xcd'))

    def test_absolute_multiple_displays(self):
        self.assertEqual(core.surface_to_screen(0, 0, (-1920, -300, 1920, 1080), False), (-1920, -300))
        self.assertEqual(core.normalize_absolute(-1920, -300, (-1920, -300, 3840, 1080)), (0, 0))

    def test_ratio_crop(self):
        self.assertEqual(core.surface_to_screen(7600, 4750, (0, 0, 2560, 1440)), (1280, 720))

    def test_auto_primary_resolution(self):
        screens = [('LEFT', (-1920, 0, 1920, 1080), False), ('RIGHT', (0, 0, 2560, 1440), True)]
        self.assertEqual(core.select_monitor(core.AUTO_MONITOR, screens, (-1920, 0, 4480, 1440)), (0, 0, 2560, 1440))
        self.assertEqual(core.select_monitor(core.FULL_DESKTOP, screens, (-1920, 0, 4480, 1440)), (-1920, 0, 4480, 1440))
        self.assertEqual(core.select_monitor('LEFT (1920×1080)', screens, (-1920, 0, 4480, 1440)), (-1920, 0, 1920, 1080))

    def test_missing_manual_display_falls_back_to_primary(self):
        self.assertEqual(core.select_monitor('MISSING (999×999)', [('A', (0, 0, 100, 100), True)],
                                             (0, 0, 100, 100)), (0, 0, 100, 100))

    def test_modifier_right_without_contact(self):
        self.assertEqual(core.tip_action({'Pen button 1'}, core.DEFAULT_BINDINGS), 'Right click')
        self.assertEqual(core.choose_pen_route(True, True, False, True), 'mouse')

    def test_modifier_middle_and_priority(self):
        self.assertEqual(core.tip_action({'Pen button 2'}, core.DEFAULT_BINDINGS), 'Middle click')
        self.assertEqual(core.tip_action({'Pen button 1','Pen button 2'}, core.DEFAULT_BINDINGS), 'Right click')

    def test_mouse_edge_and_pending_swipe(self):
        self.assertEqual(core.choose_pen_route(True, True, True, False), 'mouse')
        self.assertEqual(core.choose_pen_route(True, True, False, False), 'pending')
        self.assertEqual(core.choose_pen_route(False, True, False, False), 'mouse')

    def test_gesture_threshold(self):
        self.assertFalse(core.travel_exceeds((100, 100), (110, 106)))
        self.assertTrue(core.travel_exceeds((100, 100), (100, 119)))

    def test_legacy_settings_migration(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / 'settings.json'
            path.write_text(json.dumps({'monitor': 'Desktop completo', 'keep_ratio': True,
               'bindings': {'Penna 1': 'Click destro', 'Penna 2': 'Swipe touch (tieni)',
                            'Tasto 1': 'Touch on/off', 'Tasto 3': 'Pagina su'}}))
            with patch.object(core, 'settings_path', return_value=path):
                loaded = core.load_settings()
            self.assertEqual(loaded['bindings']['Pen button 1'], core.RIGHT_MOD)
            self.assertEqual(loaded['bindings']['Pen button 2'], core.MIDDLE_MOD)
            self.assertEqual(loaded['bindings']['Pad button 1'], core.SWIPE_TOGGLE)
            self.assertEqual(loaded['bindings']['Pad button 3'], 'Page Up')
            self.assertEqual(loaded['monitor'], core.AUTO_MONITOR)

    def test_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / 'settings.json'
            with patch.object(core, 'settings_path', return_value=path):
                core.save_settings(core.defaults())
                self.assertTrue(core.load_settings()['swipe_enabled'])
                self.assertEqual(core.load_settings()['settings_version'], 4)


if __name__ == '__main__':
    unittest.main()
