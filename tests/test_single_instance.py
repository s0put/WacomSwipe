"""Single-instance Win32 kernel object behavior without Windows side effects."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from single_instance import (ACTIVATE_EVENT_NAME, ERROR_ALREADY_EXISTS,
                             MUTEX_NAME, SingleInstance, is_manual_launch)


class FakeKernelObjects:
    """Behaves like session-scoped named mutex and auto-reset event handles."""

    def __init__(self):
        self.mutex_count = 0
        self.event_handles = 0
        self.event_exists = False
        self.pending = False
        self.next_handle = 1
        self.handles = {}
        self.open_missing_attempts = 0
        self.event_creation_error = None
        self.signals = 0

    def _new_handle(self, kind):
        n = self.next_handle
        self.next_handle += 1
        self.handles[n] = kind
        return n

    def create_mutex(self, name):
        assert name == MUTEX_NAME
        existed = self.mutex_count > 0
        self.mutex_count += 1
        return self._new_handle('mutex'), ERROR_ALREADY_EXISTS if existed else 0

    def create_event(self, name):
        assert name == ACTIVATE_EVENT_NAME
        if self.event_creation_error:
            raise self.event_creation_error
        self.event_exists = True
        self.event_handles += 1
        return self._new_handle('event')

    def open_event(self, name):
        assert name == ACTIVATE_EVENT_NAME
        if self.open_missing_attempts:
            self.open_missing_attempts -= 1
            return None
        if not self.event_exists:
            return None
        self.event_handles += 1
        return self._new_handle('event')

    def set_event(self, handle):
        assert self.handles[handle] == 'event'
        self.pending = True
        self.signals += 1

    def consume_event(self, handle):
        assert self.handles[handle] == 'event'
        if self.pending:
            self.pending = False
            return True
        return False

    def close_handle(self, handle):
        kind = self.handles.pop(handle)
        if kind == 'mutex':
            self.mutex_count -= 1
        else:
            self.event_handles -= 1
            if self.event_handles == 0:
                self.event_exists = False
                self.pending = False


class SingleInstanceTests(unittest.TestCase):
    def test_first_launch_owns_mutex_before_gui(self):
        api = FakeKernelObjects()
        first = SingleInstance(api)
        self.assertTrue(first.acquire())
        self.assertEqual(api.mutex_count, 1)
        self.assertEqual(api.event_handles, 1)
        self.assertFalse(first.consume_activation())
        first.close()
        self.assertEqual(api.mutex_count, 0)
        self.assertFalse(api.event_exists)

    def test_second_launch_cannot_open_second_hid_instance(self):
        api = FakeKernelObjects()
        first, second = SingleInstance(api), SingleInstance(api)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        self.assertIsNone(second.mutex_handle)
        self.assertIsNone(second.event_handle)
        self.assertEqual(api.mutex_count, 1)
        self.assertEqual(api.signals, 1)
        self.assertTrue(first.consume_activation())
        self.assertFalse(first.consume_activation())
        second.close()
        first.close()

    def test_three_more_launches_coalesce_into_first_instance(self):
        api = FakeKernelObjects()
        first = SingleInstance(api)
        self.assertTrue(first.acquire())
        for _ in range(3):
            second = SingleInstance(api)
            self.assertFalse(second.acquire())
            second.close()
        self.assertEqual(api.mutex_count, 1)
        self.assertEqual(api.signals, 3)
        self.assertTrue(first.consume_activation())
        first.close()

    def test_scheduled_auto_start_is_quiet_duplicate(self):
        api = FakeKernelObjects()
        first, login = SingleInstance(api), SingleInstance(api)
        self.assertTrue(first.acquire())
        self.assertFalse(login.acquire(activate_existing=False))
        self.assertFalse(first.consume_activation())
        self.assertEqual(api.signals, 0)
        first.close()

    def test_new_process_allowed_after_first_quits(self):
        api = FakeKernelObjects()
        first = SingleInstance(api)
        self.assertTrue(first.acquire())
        first.close()
        next_launch = SingleInstance(api)
        self.assertTrue(next_launch.acquire())
        next_launch.close()

    def test_early_activation_before_first_gui_can_poll(self):
        api = FakeKernelObjects()
        first, second = SingleInstance(api), SingleInstance(api)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        # Event should remain signaled until GUI begins polling.
        self.assertTrue(first.consume_activation())
        first.close()

    def test_race_in_event_creation_retries(self):
        api = FakeKernelObjects()
        first, second = SingleInstance(api), SingleInstance(api)
        self.assertTrue(first.acquire())
        api.open_missing_attempts = 3
        with patch('single_instance.time.sleep') as sleep:
            self.assertFalse(second.acquire())
        self.assertEqual(sleep.call_count, 3)
        self.assertTrue(first.consume_activation())
        first.close()

    def test_event_failure_releases_mutex(self):
        api = FakeKernelObjects()
        api.event_creation_error = OSError('permission denied')
        gate = SingleInstance(api)
        with self.assertRaisesRegex(OSError, 'permission denied'):
            gate.acquire()
        self.assertEqual(api.mutex_count, 0)
        self.assertEqual(api.event_handles, 0)

    def test_close_idempotent(self):
        api = FakeKernelObjects()
        gate = SingleInstance(api)
        self.assertTrue(gate.acquire())
        gate.close()
        gate.close()
        self.assertEqual(api.mutex_count, 0)

    def test_cannot_acquire_twice(self):
        api = FakeKernelObjects()
        gate = SingleInstance(api)
        self.assertTrue(gate.acquire())
        with self.assertRaises(RuntimeError):
            gate.acquire()
        gate.close()

    def test_manual_vs_login_arguments(self):
        self.assertTrue(is_manual_launch([]))
        self.assertTrue(is_manual_launch(['--autostart-other']))
        self.assertFalse(is_manual_launch(['--autostart']))


if __name__ == '__main__':
    unittest.main()
