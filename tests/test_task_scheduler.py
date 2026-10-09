"""Safe offline checks for Windows Task Scheduler integration."""
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import startup


class SchedulerTests(unittest.TestCase):
    def test_task_definition_is_interactive_per_user_and_has_no_delay(self):
        exe = r'C:\Users\Tester\Wacom Swipe\WacomSwipe.exe'
        xml = startup._task_xml(r'DESKTOP\Tester', exe, '--autostart', r'C:\Users\Tester\Wacom Swipe')
        root = ET.fromstring(xml)
        ns = '{' + startup.TASK_NS + '}'
        get = lambda path: root.findtext('/'.join(ns + name for name in path.split('/')))
        self.assertEqual(get('Triggers/LogonTrigger/UserId'), r'DESKTOP\Tester')
        self.assertEqual(get('Principals/Principal/LogonType'), 'InteractiveToken')
        self.assertEqual(get('Principals/Principal/RunLevel'), 'LeastPrivilege')
        self.assertEqual(get('Settings/DisallowStartIfOnBatteries'), 'false')
        self.assertEqual(get('Settings/StartWhenAvailable'), 'true')
        self.assertEqual(get('Settings/MultipleInstancesPolicy'), 'IgnoreNew')
        self.assertEqual(get('Actions/Exec/Command'), exe)
        self.assertEqual(get('Actions/Exec/Arguments'), '--autostart')
        self.assertIsNone(root.find('.//' + ns + 'Delay'))
        self.assertNotIn(b'HighestAvailable', xml)
        self.assertNotIn(b'Password', xml)

    def test_quotes_and_unicode_in_xml(self):
        xml = startup._task_xml('DESKTOP\\Café', r'C:\My & App\WacomSwipe.exe', '--autostart', r'C:\My & App')
        root = ET.fromstring(xml)
        self.assertIn('My & App', root.findtext('.//{*}Command'))
        self.assertIn('Café', root.findtext('.//{*}UserId'))

    def test_startup_parts_preserve_windows_spaces(self):
        exe = r'C:\Program Files\Wacom Swipe\WacomSwipe.exe'
        program, args, working = startup.startup_parts(executable=exe, frozen=True)
        self.assertEqual(program, exe)
        self.assertEqual(args, '--autostart')
        self.assertEqual(working, r'C:\Program Files\Wacom Swipe')

    def test_parse_utf16_and_utf8_misdeclared(self):
        data = startup._task_xml('D\\U', r'C:\WacomSwipe.exe', '--autostart', 'C:\\')
        self.assertEqual(startup._parse_task_xml(data).tag.split('}')[-1], 'Task')
        wrong_encoding = '<?xml version="1.0" encoding="UTF-16"?>\n<Task xmlns="' + startup.TASK_NS + '"><Settings><Enabled>true</Enabled></Settings></Task>'
        self.assertEqual(startup._parse_task_xml(wrong_encoding.encode('utf-8')).tag.split('}')[-1], 'Task')

    def test_existing_ours_enabled(self):
        xml = startup._task_xml('D\\U', r'C:\WacomSwipe.exe', '--autostart', 'C:\\')
        with patch.object(startup, '_run_schtasks', return_value=SimpleNamespace(returncode=0, stdout=xml)):
            self.assertEqual(startup._task_info(), (True, True))

    def test_manual_old_task_is_recognized_by_executable(self):
        data = startup._task_xml('D\\U', r'C:\WacomSwipe.exe', '--autostart', 'C:\\')
        root = ET.fromstring(data)
        root.find('.//{*}Description').text = 'Manually created earlier'
        xml = ET.tostring(root)
        with patch.object(startup, '_run_schtasks', return_value=SimpleNamespace(returncode=0, stdout=xml)):
            self.assertEqual(startup._task_info(), (True, True))

    def test_task_name_collision_not_deleted(self):
        with patch.object(startup, '_task_info', return_value=(True, False)), \
                patch.object(startup, '_run_schtasks') as command:
            with self.assertRaises(startup.StartupError):
                startup.set_startup(False)
            command.assert_not_called()

    def test_disable_removes_scheduler_and_legacy_registry(self):
        with patch.object(startup, '_task_info', return_value=(True, True)), \
                patch.object(startup, '_run_schtasks', return_value=SimpleNamespace(returncode=0)) as command, \
                patch.object(startup, '_remove_legacy') as old:
            startup.set_startup(False)
        command.assert_called_once_with('/Delete', '/TN', startup.TASK_NAME, '/F')
        old.assert_called_once()

    def test_enable_creates_and_only_then_removes_registry_entry(self):
        events = []
        def run(*args):
            self.assertEqual(args[:4], ('/Create', '/TN', startup.TASK_NAME, '/XML'))
            self.assertEqual(args[-1], '/F')
            self.assertTrue(Path(args[4]).exists())
            root = ET.fromstring(Path(args[4]).read_bytes())
            self.assertEqual(root.findtext('.//{*}Arguments'), '--autostart')
            events.append('create')
            return SimpleNamespace(returncode=0, stderr=b'', stdout=b'')
        states = iter((None, (True, True)))
        def old():
            events.append('remove-old')
        with patch.object(startup, '_task_info', side_effect=lambda: next(states)), \
                patch.object(startup, '_run_schtasks', side_effect=run), \
                patch.object(startup, '_current_user', return_value='D\\U'), \
                patch.object(startup, 'startup_parts', return_value=(r'C:\Apps\WacomSwipe.exe', '--autostart', r'C:\Apps')), \
                patch.object(startup, '_remove_legacy', side_effect=old):
            startup.set_startup(True)
        self.assertEqual(events, ['create', 'remove-old'])

    def test_scheduler_create_failure_keeps_legacy_run(self):
        with patch.object(startup, '_task_info', return_value=None), \
                patch.object(startup, '_run_schtasks', return_value=SimpleNamespace(returncode=1, stderr=b'Access denied', stdout=b'')), \
                patch.object(startup, '_current_user', return_value='D\\U'), \
                patch.object(startup, '_remove_legacy') as old:
            with self.assertRaisesRegex(startup.StartupError, 'Access denied'):
                startup.set_startup(True)
            old.assert_not_called()

    def test_registry_delete_failure_rolls_back_new_task(self):
        states = iter((None, (True, True)))
        with patch.object(startup, '_task_info', side_effect=lambda: next(states)), \
                patch.object(startup, '_run_schtasks', return_value=SimpleNamespace(returncode=0, stderr=b'', stdout=b'')), \
                patch.object(startup, '_current_user', return_value='D\\U'), \
                patch.object(startup, '_remove_legacy', side_effect=OSError('Registry blocked')), \
                patch.object(startup, '_task_delete') as delete:
            with self.assertRaisesRegex(OSError, 'Registry blocked'):
                startup.set_startup(True)
            delete.assert_called_once()

    def test_flag_only_for_windows_signin(self):
        self.assertFalse(startup.should_start_minimized(True, []))
        self.assertTrue(startup.should_start_minimized(True, ['--autostart']))
        self.assertFalse(startup.should_start_minimized(False, ['--autostart']))


if __name__ == '__main__':
    unittest.main()
