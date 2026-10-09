"""Real cross-process Windows single-instance smoke test (no Tk and no HID).

Runs automatically before building the EXE. All kernel handles are cleaned up.
"""
from __future__ import annotations

import subprocess
import sys
from single_instance import SingleInstance


def child(activate):
    source = (
        'from single_instance import SingleInstance; '
        'import sys; '
        'gate=SingleInstance(); '
        'claimed=gate.acquire(activate_existing=(sys.argv[1]=="1")); '
        'print("OWNER" if claimed else "DUPLICATE"); '
        'gate.close()'
    )
    result = subprocess.run([sys.executable, '-c', source, '1' if activate else '0'],
                            capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise AssertionError(f'Child process failed: {result.stderr}')
    return result.stdout.strip()


def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows-only single-instance smoke test')
    first = SingleInstance()
    try:
        assert first.acquire(), 'Another WacomSwipe instance is running. Close it before building.'
        assert child(activate=True) == 'DUPLICATE', 'Manual second launch was not blocked.'
        assert first.consume_activation(), 'Second launch did not request activation.'
        assert not first.consume_activation(), 'Activation event did not auto-reset.'
        assert child(activate=False) == 'DUPLICATE', 'Sign-in duplicate was not blocked.'
        assert not first.consume_activation(), 'A sign-in duplicate stole focus.'
    finally:
        first.close()
    assert child(activate=True) == 'OWNER', 'Mutex was not released on exit.'
    print('INSTANCE PASS: Second process refused, activation signaled, exit releases mutex.')


if __name__ == '__main__':
    main()
