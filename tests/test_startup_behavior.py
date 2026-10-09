"""Regression coverage for Windows-sign-in-only minimized launches."""
import sys
import unittest
from unittest.mock import patch

from startup import should_start_minimized, startup_command


class StartupBehaviorTests(unittest.TestCase):
    def test_double_click_always_opens_normally(self):
        for pref in (False, True):
            with self.subTest(pref=pref):
                self.assertFalse(should_start_minimized(pref, []))
                self.assertFalse(should_start_minimized(pref, ['file.txt']))

    def test_windows_login_minimize_when_enabled(self):
        self.assertTrue(should_start_minimized(True, ['--autostart']))
        self.assertTrue(should_start_minimized(True, ['--autostart', '--other']))

    def test_windows_login_normal_when_disabled(self):
        self.assertFalse(should_start_minimized(False, ['--autostart']))

    def test_flag_must_be_exact(self):
        self.assertFalse(should_start_minimized(True, ['--autostart-something']))
        self.assertFalse(should_start_minimized(True, ['--AUTOSTART']))

    def test_default_arguments_follow_process_command_line(self):
        with patch.object(sys, 'argv', ['WacomSwipe.exe']):
            self.assertFalse(should_start_minimized(True))
        with patch.object(sys, 'argv', ['WacomSwipe.exe', '--autostart']):
            self.assertTrue(should_start_minimized(True))

    def test_windows_run_command_contains_flag(self):
        exe = r'C:\Program Files\WacomSwipe\WacomSwipe.exe'
        cmd = startup_command(executable=exe, frozen=True)
        self.assertEqual(cmd, f'"{exe}" --autostart')

    def test_unfrozen_python_startup_command_contains_flag(self):
        cmd = startup_command(executable=r'C:\Python311\python.exe',
                              script=r'C:\Apps\WacomSwipe.py', frozen=False)
        self.assertTrue(cmd.endswith('--autostart'))
        self.assertIn('WacomSwipe.py', cmd)


if __name__ == '__main__':
    unittest.main()
