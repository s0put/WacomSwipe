"""No Windows changes: simulate scheduled tasks to verify path self-repair."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import startup


EXE_OLD = r'C:\Old Folder\WacomSwipe.exe'
EXE_NEW = r'C:\Moved Folder\WacomSwipe.exe'
ARGS = '--autostart'


def response(program=EXE_OLD, arguments=ARGS, directory=None, *, enabled=True, description=None):
    xml = startup._task_xml('DESKTOP\\User', program, arguments,
                            directory or program.rsplit('\\', 1)[0])
    if not enabled or description is not None:
        import xml.etree.ElementTree as ET
        node = ET.fromstring(xml)
        if not enabled:
            node.find('.//{*}Settings/{*}Enabled').text = 'false'
        if description is not None:
            node.find('.//{*}RegistrationInfo/{*}Description').text = description
        xml = ET.tostring(node, encoding='utf-16', xml_declaration=True)
    return SimpleNamespace(returncode=0, stdout=xml)


class StartupRepairTests(unittest.TestCase):
    def test_compare_current_path_no_write(self):
        with patch.object(startup, '_run_schtasks', return_value=response(program=EXE_NEW)) as runner, \
                patch.object(startup, 'startup_parts', return_value=(EXE_NEW, ARGS, r'C:\Moved Folder')), \
                patch.object(startup, 'set_startup') as setter:
            self.assertFalse(startup.ensure_current_startup_path())
            setter.assert_not_called()
            runner.assert_called_once_with('/Query', '/TN', startup.TASK_NAME, '/XML')

    def test_moving_exe_updates_task_only_once(self):
        with patch.object(startup, '_run_schtasks', side_effect=[response(program=EXE_OLD), response(program=EXE_NEW)]) as run, \
                patch.object(startup, 'startup_parts', return_value=(EXE_NEW, ARGS, r'C:\Moved Folder')), \
                patch.object(startup, 'set_startup') as setter:
            self.assertTrue(startup.ensure_current_startup_path())
            setter.assert_called_once_with(True)
            self.assertEqual(run.call_count, 2)

    def test_working_directory_change_also_repaired(self):
        with patch.object(startup, '_run_schtasks', side_effect=[response(program=EXE_NEW, directory=r'C:\Old Folder'), response(program=EXE_NEW)]), \
                patch.object(startup, 'startup_parts', return_value=(EXE_NEW, ARGS, r'C:\Moved Folder')), \
                patch.object(startup, 'set_startup') as setter:
            self.assertTrue(startup.ensure_current_startup_path())
            setter.assert_called_once_with(True)

    def test_case_and_separator_differences_not_a_move(self):
        with patch.object(startup, '_run_schtasks', return_value=response(program=r'c:/moved folder/WACOMSWIPE.EXE', directory=r'c:/moved folder')), \
                patch.object(startup, 'startup_parts', return_value=(EXE_NEW, ARGS, r'C:\Moved Folder')), \
                patch.object(startup, 'set_startup') as setter:
            self.assertFalse(startup.ensure_current_startup_path())
            setter.assert_not_called()

    def test_no_task_no_legacy_no_write(self):
        with patch.object(startup, '_run_schtasks', return_value=SimpleNamespace(returncode=1)), \
                patch.object(startup, '_legacy_enabled', return_value=False), \
                patch.object(startup, 'set_startup') as setter:
            self.assertFalse(startup.ensure_current_startup_path())
            setter.assert_not_called()

    def test_old_registry_entry_migrates(self):
        with patch.object(startup, '_run_schtasks', return_value=SimpleNamespace(returncode=1)), \
                patch.object(startup, '_legacy_enabled', return_value=True), \
                patch.object(startup, 'set_startup') as setter:
            self.assertTrue(startup.ensure_current_startup_path())
            setter.assert_called_once_with(True)

    def test_intentionally_disabled_task_not_reenabled(self):
        with patch.object(startup, '_run_schtasks', return_value=response(enabled=False)), \
                patch.object(startup, 'set_startup') as setter:
            self.assertFalse(startup.ensure_current_startup_path())
            setter.assert_not_called()

    def test_unrelated_task_same_name_not_modified(self):
        with patch.object(startup, '_run_schtasks', return_value=response(program=r'C:\Other\Other.exe', description='Unrelated task')), \
                patch.object(startup, 'set_startup') as setter:
            self.assertFalse(startup.ensure_current_startup_path())
            setter.assert_not_called()

    def test_task_argument_changed_repaired(self):
        with patch.object(startup, '_run_schtasks', side_effect=[response(program=EXE_NEW, arguments=''), response(program=EXE_NEW)]), \
                patch.object(startup, 'startup_parts', return_value=(EXE_NEW, ARGS, r'C:\Moved Folder')), \
                patch.object(startup, 'set_startup') as setter:
            self.assertTrue(startup.ensure_current_startup_path())
            setter.assert_called_once_with(True)

    def test_registration_failed_verification_is_error(self):
        with patch.object(startup, '_run_schtasks', side_effect=[response(program=EXE_OLD), response(program=EXE_OLD)]), \
                patch.object(startup, 'startup_parts', return_value=(EXE_NEW, ARGS, r'C:\Moved Folder')), \
                patch.object(startup, 'set_startup'):
            with self.assertRaisesRegex(startup.StartupError, 'did not retain'):
                startup.ensure_current_startup_path()

    def test_update_failure_exposed_to_user(self):
        with patch.object(startup, '_run_schtasks', return_value=response(program=EXE_OLD)), \
                patch.object(startup, 'startup_parts', return_value=(EXE_NEW, ARGS, r'C:\Moved Folder')), \
                patch.object(startup, 'set_startup', side_effect=startup.StartupError('Access denied')):
            with self.assertRaisesRegex(startup.StartupError, 'Access denied'):
                startup.ensure_current_startup_path()

    def test_check_only_manually_launched_exe(self):
        self.assertTrue(startup.should_check_startup_path(True, [], frozen=True))
        self.assertFalse(startup.should_check_startup_path(True, ['--autostart'], frozen=True))
        self.assertFalse(startup.should_check_startup_path(False, [], frozen=True))
        self.assertFalse(startup.should_check_startup_path(True, [], frozen=False))


if __name__ == '__main__':
    unittest.main()
