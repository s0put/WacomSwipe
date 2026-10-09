"""One WacomSwipe process per interactive Windows session.

A named Win32 mutex is acquired before the Tk window and the HID worker exist.
Subsequent manual launches signal a named auto-reset event so the original
instance can restore itself from the taskbar/system tray. An automatic
``--autostart`` launch never steals focus from the existing instance.

Both kernel objects live in the local Windows session and disappear when the
last owning handle closes; no admin privileges, files or stale lock cleanup.
"""
from __future__ import annotations

import ctypes
import sys
import time

# Fixed application-specific IDs so running copies using this version agree.
MUTEX_NAME = r'Local\WacomSwipe_CTL4100WL_F82D95C8_OneInstance'
ACTIVATE_EVENT_NAME = r'Local\WacomSwipe_CTL4100WL_F82D95C8_Activate'
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF


class Win32Objects:
    """Small native API boundary, injectable for platform-independent tests."""

    def __init__(self):
        if sys.platform != 'win32':
            raise OSError('Windows kernel objects are only available on Windows')
        dll = ctypes.WinDLL('kernel32', use_last_error=True)
        self._dll = dll
        self._create_mutex = dll.CreateMutexW
        self._create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        self._create_mutex.restype = ctypes.c_void_p
        self._create_event = dll.CreateEventW
        self._create_event.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
        self._create_event.restype = ctypes.c_void_p
        self._open_event = dll.OpenEventW
        self._open_event.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p)
        self._open_event.restype = ctypes.c_void_p
        self._set_event = dll.SetEvent
        self._set_event.argtypes = (ctypes.c_void_p,)
        self._set_event.restype = ctypes.c_int
        self._wait = dll.WaitForSingleObject
        self._wait.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        self._wait.restype = ctypes.c_uint32
        self._close = dll.CloseHandle
        self._close.argtypes = (ctypes.c_void_p,)
        self._close.restype = ctypes.c_int

    def create_mutex(self, name):
        handle = self._create_mutex(None, False, name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        return handle, error

    def create_event(self, name):
        handle = self._create_event(None, False, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def open_event(self, name):
        # A missing event is normal if another instance is still initializing.
        return self._open_event(EVENT_MODIFY_STATE, False, name)

    def set_event(self, handle):
        if not self._set_event(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def consume_event(self, handle):
        result = self._wait(handle, 0)  # Never block Tk's UI loop.
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise ctypes.WinError(ctypes.get_last_error())

    def close_handle(self, handle):
        if handle:
            self._close(handle)


class SingleInstance:
    def __init__(self, api=None):
        self.api = api if api is not None else Win32Objects()
        self.mutex_handle = None
        self.event_handle = None

    def acquire(self, *, activate_existing=True):
        """True for the first instance; False for any later launch.

        On a duplicate manual start, request activation of the original app.
        No main window or HID device is ever created by the second process.
        """
        if self.mutex_handle is not None:
            raise RuntimeError('SingleInstance.acquire() called twice')
        handle, error = self.api.create_mutex(MUTEX_NAME)
        if error == ERROR_ALREADY_EXISTS:
            self.api.close_handle(handle)
            if activate_existing:
                self._notify_running_instance()
            return False
        self.mutex_handle = handle
        try:
            self.event_handle = self.api.create_event(ACTIVATE_EVENT_NAME)
        except BaseException:
            self.close()
            raise
        return True

    def _notify_running_instance(self):
        # Mutex creation and event creation happen in sequence. Brief retry
        # handles the rare race where a second launch arrives between them.
        for attempt in range(8):
            event = self.api.open_event(ACTIVATE_EVENT_NAME)
            if event:
                try:
                    self.api.set_event(event)
                finally:
                    self.api.close_handle(event)
                return True
            if attempt < 7:
                time.sleep(0.04)
        return False

    def consume_activation(self):
        """Poll from the Tk thread; returns immediately if nothing is pending."""
        return bool(self.event_handle and self.api.consume_event(self.event_handle))

    def close(self):
        if self.event_handle is not None:
            self.api.close_handle(self.event_handle)
            self.event_handle = None
        if self.mutex_handle is not None:
            self.api.close_handle(self.mutex_handle)
            self.mutex_handle = None


def is_manual_launch(args):
    return '--autostart' not in args
