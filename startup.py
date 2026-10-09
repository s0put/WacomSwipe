"""Windows per-user sign-in startup via the native Task Scheduler.

No elevated rights or additional Python packages are required for a normal
Windows installation. Legacy HKCU Run startup is migrated on successful setup.
"""
from __future__ import annotations

import ntpath
import re
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'WacomSwipe'
TASK_NAME = 'WacomSwipe'
TASK_DESCRIPTION = 'WacomSwipe managed sign-in startup (current user)'
TASK_NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'


class StartupError(OSError):
    """A task could not be registered, queried, or removed safely."""


def _full_path(value):
    """Keep already absolute Windows paths intact, including in cross-OS tests."""
    value = str(value)
    return value if ntpath.isabs(value) else str(Path(value).resolve())


def startup_parts(executable=None, script=None, frozen=None):
    """Return (program, arguments, working_dir), with --autostart only at login."""
    executable = str(executable or sys.executable)
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if frozen:
        program = _full_path(executable)
        arguments = '--autostart'
        cwd = ntpath.dirname(program) if ntpath.isabs(program) else str(Path(program).parent)
    else:
        path = Path(executable)
        pythonw = path.with_name('pythonw.exe')
        if path.name.lower() in ('python.exe', 'python3.exe') and pythonw.exists():
            executable = str(pythonw)
        program = _full_path(executable)
        source = _full_path(script or (Path(__file__).resolve().parent / 'WacomSwipe.py'))
        arguments = subprocess.list2cmdline([source, '--autostart'])
        cwd = ntpath.dirname(source) if ntpath.isabs(source) else str(Path(source).parent)
    return program, arguments, cwd


def startup_command(executable=None, script=None, frozen=None):
    """Old HKCU Run command retained for read-only compatibility/tests."""
    program, arguments, _ = startup_parts(executable, script, frozen)
    return subprocess.list2cmdline([program]) + ' ' + arguments


def should_start_minimized(preference, args=None):
    """A double-click always shows the window, even if the preference is on."""
    if args is None:
        args = sys.argv[1:]
    return bool(preference) and '--autostart' in args


def should_check_startup_path(startup_on, args=None, frozen=None):
    """Check only a manually launched EXE, not a source run or login run.

    A developer launching WacomSwipe.py must not silently replace an installed
    EXE's Windows sign-in entry with a Python source-file command.
    """
    if args is None:
        args = sys.argv[1:]
    if frozen is None:
        frozen = getattr(sys, 'frozen', False)
    return bool(startup_on and frozen and '--autostart' not in args)


def _registry():
    import winreg
    return winreg


def _legacy_enabled():
    reg = _registry()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
            command, kind = reg.QueryValueEx(key, RUN_NAME)
            return kind == reg.REG_SZ and bool(command)
    except FileNotFoundError:
        return False


def _remove_legacy():
    """Only remove our own named value; leave all other startup values intact."""
    reg = _registry()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            try:
                reg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def _run_schtasks(*args, timeout=18):
    kwargs = dict(capture_output=True, timeout=timeout)
    if sys.platform == 'win32':
        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    try:
        return subprocess.run(['schtasks.exe', *args], **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StartupError(f'Windows Task Scheduler did not respond: {exc}') from exc


def _task_xml(user, program, arguments, working_dir):
    """Build a per-user interactive task, no delay, elevation, or password."""
    def elem(parent, tag, value=None, **attrs):
        node = ET.SubElement(parent, tag, attrs)
        if value is not None:
            node.text = value
        return node

    root = ET.Element('Task', {'xmlns': TASK_NS, 'version': '1.2'})
    registration = elem(root, 'RegistrationInfo')
    elem(registration, 'Author', 'WacomSwipe')
    elem(registration, 'Description', TASK_DESCRIPTION)

    triggers = elem(root, 'Triggers')
    trigger = elem(triggers, 'LogonTrigger')
    elem(trigger, 'Enabled', 'true')
    elem(trigger, 'UserId', user)

    principals = elem(root, 'Principals')
    principal = elem(principals, 'Principal', id='Author')
    elem(principal, 'UserId', user)
    elem(principal, 'LogonType', 'InteractiveToken')
    elem(principal, 'RunLevel', 'LeastPrivilege')

    settings = elem(root, 'Settings')
    elem(settings, 'MultipleInstancesPolicy', 'IgnoreNew')
    elem(settings, 'DisallowStartIfOnBatteries', 'false')
    elem(settings, 'StopIfGoingOnBatteries', 'false')
    elem(settings, 'StartWhenAvailable', 'true')
    elem(settings, 'AllowHardTerminate', 'true')
    elem(settings, 'Enabled', 'true')
    elem(settings, 'AllowStartOnDemand', 'true')
    elem(settings, 'ExecutionTimeLimit', 'PT0S')

    actions = elem(root, 'Actions', Context='Author')
    action = elem(actions, 'Exec')
    elem(action, 'Command', program)
    elem(action, 'Arguments', arguments)
    elem(action, 'WorkingDirectory', working_dir)
    return ET.tostring(root, encoding='utf-16', xml_declaration=True)


def _current_user():
    # The actual Windows account identity, including domain-qualified logins.
    try:
        result = subprocess.run(['whoami.exe'], capture_output=True, text=True,
                                timeout=7, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StartupError(f'Cannot identify current Windows account: {exc}') from exc
    if result.returncode or not result.stdout.strip():
        raise StartupError('Cannot identify the current Windows account for the login task.')
    return result.stdout.strip()


def _nested_text(root, *tags):
    path = './/' + '/'.join(f'{{{TASK_NS}}}{tag}' for tag in tags)
    node = root.find(path)
    return node.text if node is not None and node.text is not None else ''


def _message(raw):
    if isinstance(raw, str):
        return raw.strip()
    if not raw:
        return ''
    codec = 'mbcs' if sys.platform == 'win32' else 'utf-8'
    return raw.decode(codec, errors='replace').strip()


def _parse_task_xml(raw):
    # schtasks.exe can emit UTF-16 XML or locale-encoded console output,
    # depending on Windows version and console code page.
    if isinstance(raw, str):
        xml = raw
    elif raw.startswith((b'\xff\xfe', b'\xfe\xff')) or b'\x00' in raw[:64]:
        return ET.fromstring(raw)
    else:
        try:
            xml = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            xml = _message(raw)
    xml = re.sub(r'^\ufeff?\s*<\?xml[^>]*\?>', '', xml, count=1)
    return ET.fromstring(xml)


def _task_record():
    """Inspect the task and return its current executable and registration.

    A missing task returns None. An unrelated task is never modified by us.
    The command path comes from the task's XML, not its display name or label.
    """
    result = _run_schtasks('/Query', '/TN', TASK_NAME, '/XML')
    if result.returncode:
        # schtasks normally uses code 1 for an absent task; a later creation
        # attempt still reports permission or scheduler failures explicitly.
        return None
    try:
        root = _parse_task_xml(result.stdout)
    except ET.ParseError as exc:
        raise StartupError('The Task Scheduler returned malformed task data.') from exc
    description = _nested_text(root, 'RegistrationInfo', 'Description')
    command = _nested_text(root, 'Actions', 'Exec', 'Command').strip().strip('"')
    arguments = _nested_text(root, 'Actions', 'Exec', 'Arguments').strip()
    working_dir = _nested_text(root, 'Actions', 'Exec', 'WorkingDirectory').strip().strip('"')
    executable_name = ntpath.basename(command.replace('/', '\\')).lower()
    ours = (executable_name == 'wacomswipe.exe' or
            (executable_name in ('python.exe', 'pythonw.exe') and 'wacomswipe.py' in arguments.lower()) or
            (description == TASK_DESCRIPTION and bool(command)))
    enabled = _nested_text(root, 'Settings', 'Enabled').strip().lower() != 'false'
    return {'enabled': enabled, 'ours': ours, 'program': command,
            'arguments': arguments, 'working_dir': working_dir}


def _task_info():
    """Return None, or (enabled, ours) for backward-compatible callers."""
    record = _task_record()
    return (record['enabled'], record['ours']) if record is not None else None


def _normalized_windows_path(value):
    """Compare Windows paths regardless of letter case or separator spelling."""
    return ntpath.normcase(ntpath.normpath(str(value).strip().strip('"').replace('/', '\\')))


def ensure_current_startup_path():
    """Repair a moved executable's *enabled* sign-in task, if necessary.

    Intended for a normal/manual application launch, never the scheduled
    --autostart invocation. Do not enable a disabled task, overwrite an
    unrelated task, or recreate a task that the user deliberately removed.
    Returns True only when a task has actually been registered/updated.
    """
    record = _task_record()
    if record is None:
        # One-time migration from an old HKCU Run startup registration.
        if _legacy_enabled():
            set_startup(True)
            return True
        return False
    if not record['ours'] or not record['enabled']:
        return False

    program, arguments, cwd = startup_parts()
    if (_normalized_windows_path(record['program']) == _normalized_windows_path(program)
            and record['arguments'].strip().casefold() == arguments.strip().casefold()
            and _normalized_windows_path(record['working_dir']) == _normalized_windows_path(cwd)):
        return False

    set_startup(True)
    updated = _task_record()
    if (updated is None or not updated['ours'] or not updated['enabled']
            or _normalized_windows_path(updated['program']) != _normalized_windows_path(program)
            or updated['arguments'].strip().casefold() != arguments.strip().casefold()
            or _normalized_windows_path(updated['working_dir']) != _normalized_windows_path(cwd)):
        raise StartupError('Windows did not retain the new WacomSwipe startup path.')
    return True


def startup_enabled():
    info = _task_info()
    return bool(info is not None and info[0] and info[1]) or _legacy_enabled()


def _task_delete(*, require_ours=True):
    info = _task_info()
    if info is None:
        return
    if require_ours and not info[1]:
        raise StartupError('Another Windows task is named WacomSwipe. Rename it before enabling startup.')
    result = _run_schtasks('/Delete', '/TN', TASK_NAME, '/F')
    if result.returncode:
        detail = _message(result.stderr or result.stdout)
        raise StartupError(f'Cannot remove the WacomSwipe login task: {detail}')


def set_startup(enabled):
    """Create/remove the logon task; migrate legacy HKCU Run only on success.

    Never fall back silently to the old Run key, never request admin elevation,
    and never leave both autostart mechanisms enabled when activation succeeds.
    """
    if not enabled:
        _task_delete()
        _remove_legacy()
        return

    existing = _task_info()
    if existing is not None and not existing[1]:
        raise StartupError('A different scheduled task already uses the name WacomSwipe.')

    user = _current_user()
    program, arguments, cwd = startup_parts()
    payload = _task_xml(user, program, arguments, cwd)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.xml', prefix='wacomswipe-task-',
                                         delete=False) as handle:
            handle.write(payload)
            temporary = handle.name
        result = _run_schtasks('/Create', '/TN', TASK_NAME, '/XML', temporary, '/F')
        if result.returncode:
            detail = _message(result.stderr or result.stdout)
            raise StartupError(f'Windows could not create the login task: {detail}\n'
                               'The current account may be restricted from creating tasks.')
        info = _task_info()
        if info is None or not info[0] or not info[1]:
            raise StartupError('Windows did not confirm an enabled WacomSwipe login task.')
        # Delete the old Run entry only AFTER confirming the new task works.
        # If removal fails, roll back to prevent two launches at next login.
        try:
            _remove_legacy()
        except OSError:
            _task_delete()
            raise
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
