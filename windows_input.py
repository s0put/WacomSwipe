"""Windows 11 input: SendInput (absolute mouse and keys) + native touch injection.
Only Windows built-in DLLs, no virtual kernel driver or external tablet program.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from shortcuts import KeyHoldTracker, parse_shortcut

user32 = C.WinDLL('user32', use_last_error=True)


WM_NCHITTEST = 0x0084
GA_ROOT = 2
HTNOWHERE = 0
HTCLIENT = 1
HTCAPTION = 2
HTSYSMENU = 3
HTMINBUTTON = 8
HTMAXBUTTON = 9
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17
HTCLOSE = 20
HTHELP = 21
NONCLIENT_MOUSE_FALLBACK = {
    HTCAPTION, HTSYSMENU, HTMINBUTTON, HTMAXBUTTON, HTCLOSE, HTHELP,
    HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT,
}


class POINT(C.Structure):
    _fields_ = [('x', C.c_int32), ('y', C.c_int32)]


class RECT(C.Structure):
    _fields_ = [('left', C.c_int32), ('top', C.c_int32), ('right', C.c_int32), ('bottom', C.c_int32)]


class MONITORINFOEX(C.Structure):
    _fields_ = [('cbSize', C.c_uint32), ('rcMonitor', RECT), ('rcWork', RECT),
                ('dwFlags', C.c_uint32), ('szDevice', C.c_wchar * 32)]


class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', C.c_int32), ('dy', C.c_int32), ('mouseData', C.c_uint32),
                ('dwFlags', C.c_uint32), ('time', C.c_uint32), ('dwExtraInfo', C.c_size_t)]


class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', C.c_uint16), ('wScan', C.c_uint16), ('dwFlags', C.c_uint32),
                ('time', C.c_uint32), ('dwExtraInfo', C.c_size_t)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [('uMsg', C.c_uint32), ('wParamL', C.c_uint16), ('wParamH', C.c_uint16)]


class INPUTUNION(C.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(C.Structure):
    _fields_ = [('type', C.c_uint32), ('u', INPUTUNION)]


class POINTER_INFO(C.Structure):
    _fields_ = [
        ('pointerType', C.c_uint32), ('pointerId', C.c_uint32), ('frameId', C.c_uint32),
        ('pointerFlags', C.c_uint32), ('sourceDevice', C.c_void_p), ('hwndTarget', C.c_void_p),
        ('ptPixelLocation', POINT), ('ptHimetricLocation', POINT),
        ('ptPixelLocationRaw', POINT), ('ptHimetricLocationRaw', POINT),
        ('dwTime', C.c_uint32), ('historyCount', C.c_uint32), ('InputData', C.c_int32),
        ('dwKeyStates', C.c_uint32), ('PerformanceCount', C.c_uint64),
        ('ButtonChangeType', C.c_uint32)
    ]


class POINTER_TOUCH_INFO(C.Structure):
    _fields_ = [('pointerInfo', POINTER_INFO), ('touchFlags', C.c_uint32),
                ('touchMask', C.c_uint32), ('rcContact', RECT), ('rcContactRaw', RECT),
                ('orientation', C.c_uint32), ('pressure', C.c_uint32)]


user32.SendInput.argtypes = [C.c_uint32, C.POINTER(INPUT), C.c_int]
user32.SendInput.restype = C.c_uint32
user32.InitializeTouchInjection.argtypes = [C.c_uint32, C.c_uint32]
user32.InitializeTouchInjection.restype = W.BOOL
user32.InjectTouchInput.argtypes = [C.c_uint32, C.POINTER(POINTER_TOUCH_INFO)]
user32.InjectTouchInput.restype = W.BOOL
user32.GetSystemMetrics.argtypes = [C.c_int]
user32.GetSystemMetrics.restype = C.c_int

# Physical current key state: the high bit indicates a pressed modifier.
# Never rely on the low (pressed-since-last-check) bit.
user32.GetAsyncKeyState.argtypes = [C.c_int]
user32.GetAsyncKeyState.restype = C.c_short


def pressed_keyboard_modifiers():
    """Currently held Win32 modifier VKs, independent from Tk modifier masks."""
    return {vk for vk in (0x11, 0x12, 0x10, 0x5B, 0x5C)
            if user32.GetAsyncKeyState(vk) & 0x8000}


def dpi_awareness():
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = W.BOOL
        if user32.SetProcessDpiAwarenessContext(C.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        shcore = C.WinDLL('shcore')
        shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def monitors():
    callback_t = C.WINFUNCTYPE(W.BOOL, C.c_void_p, C.c_void_p, C.POINTER(RECT), C.c_ssize_t)
    user32.GetMonitorInfoW.argtypes = [C.c_void_p, C.POINTER(MONITORINFOEX)]
    user32.GetMonitorInfoW.restype = W.BOOL
    user32.EnumDisplayMonitors.argtypes = [C.c_void_p, C.c_void_p, callback_t, C.c_ssize_t]
    user32.EnumDisplayMonitors.restype = W.BOOL
    found = []

    def callback(handle, hdc, rect, lparam):
        info = MONITORINFOEX()
        info.cbSize = C.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(handle, C.byref(info)):
            r = info.rcMonitor
            found.append((info.szDevice, (r.left, r.top, r.right-r.left, r.bottom-r.top),
                          bool(info.dwFlags & 1)))
        return True

    ref = callback_t(callback)
    if not user32.EnumDisplayMonitors(None, None, ref, 0):
        raise C.WinError(C.get_last_error())
    found.sort(key=lambda r: (not r[2], r[0]))
    return found


def virtual_desktop():
    # SM_X/Y/CX/CYVIRTUALSCREEN
    return tuple(user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))


def _send(item):
    if user32.SendInput(1, C.byref(item), C.sizeof(INPUT)) != 1:
        raise C.WinError(C.get_last_error())


def mouse_move_absolute(norm_x, norm_y):
    inp = INPUT()
    inp.type = 0
    inp.u.mi = MOUSEINPUT(norm_x, norm_y, 0, 0x8000 | 0x4000 | 0x0001, 0, 0)
    _send(inp)


MOUSE_FLAGS = {'Left click': (0x0002, 0x0004),
               'Right click': (0x0008, 0x0010),
               'Middle click': (0x0020, 0x0040)}
KEY_VK = {'Enter': 0x0D, 'Esc': 0x1B, 'Space': 0x20, 'Tab': 0x09,
          'Page Up': 0x21, 'Page Down': 0x22,
          **{f'F{i}': 0x70+i-1 for i in range(5, 13)}}


# Reference counting keeps shared Ctrl/Alt/Shift keys held if two pads overlap.
_key_tracker = KeyHoldTracker()


def _key_event(vk, pressed):
    item = INPUT()
    item.type = 1
    item.u.ki = KEYBDINPUT(vk, 0, 0 if pressed else 0x0002, 0, 0)
    _send(item)


def button(action, pressed):
    if action in MOUSE_FLAGS:
        flag = MOUSE_FLAGS[action][0 if pressed else 1]
        item = INPUT()
        item.type = 0
        item.u.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
        _send(item)
        return
    keys = (KEY_VK[action],) if action in KEY_VK else parse_shortcut(action)
    if keys is None:
        return
    for vk, is_down in _key_tracker.changes(keys, pressed):
        _key_event(vk, is_down)


user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = C.c_void_p
user32.GetAncestor.argtypes = [C.c_void_p, C.c_uint]
user32.GetAncestor.restype = C.c_void_p
user32.GetWindowRect.argtypes = [C.c_void_p, C.POINTER(RECT)]
user32.GetWindowRect.restype = W.BOOL
user32.GetWindowLongW.argtypes = [C.c_void_p, C.c_int]
user32.GetWindowLongW.restype = C.c_int32
user32.SendMessageTimeoutW.argtypes = [C.c_void_p, C.c_uint, C.c_size_t,
                                      C.c_ssize_t, C.c_uint, C.c_uint,
                                      C.POINTER(C.c_size_t)]
user32.SendMessageTimeoutW.restype = C.c_size_t

try:
    user32.GetDpiForWindow.argtypes = [C.c_void_p]
    user32.GetDpiForWindow.restype = C.c_uint
except AttributeError:
    pass


def window_nonclient_hit(x, y):
    """Return WM_NCHITTEST code for the root window under a screen point.

    Used to avoid touch injection on title bars, caption buttons and resize borders,
    where many desktop apps expect ordinary mouse behaviour.
    """
    pt = POINT(int(x), int(y))
    hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return HTNOWHERE
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    # WM_NCHITTEST requires SCREEN coordinates in lParam, NOT client coordinates.
    lparam = (int(y) & 0xFFFF) << 16 | (int(x) & 0xFFFF)
    result = C.c_size_t()
    # Never wait indefinitely for an app that is hung or not processing messages.
    if user32.SendMessageTimeoutW(root, WM_NCHITTEST, 0, lparam,
                                  0x0001 | 0x0002, 70, C.byref(result)):
        return int(result.value)
    return HTNOWHERE


def _custom_title_band(x, y):
    """Also handle title bars rendered in the CLIENT area (e.g. custom UIs)."""
    hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
    if not hwnd:
        return False
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    style = user32.GetWindowLongW(root, -16)  # GWL_STYLE
    if not style & (0x00C00000 | 0x00080000):  # WS_CAPTION or WS_SYSMENU
        return False
    bounds = RECT()
    if not user32.GetWindowRect(root, C.byref(bounds)):
        return False
    try:
        dpi = user32.GetDpiForWindow(root) or 96
    except AttributeError:
        dpi = 96
    title_height = round(48 * dpi / 96)
    return (bounds.left <= x < bounds.right and
            bounds.top <= y < bounds.top + title_height)


def _near_resize_edge(x, y):
    """Conservative mouse zone for custom-window resize borders.

    Electron / custom chrome may return HTCLIENT even on resizable edges.
    Only apply to actual resizable top-level windows, and only at pen-down.
    """
    hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
    if not hwnd:
        return False
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    style = user32.GetWindowLongW(root, -16)
    if not style & 0x00040000:  # WS_THICKFRAME
        return False
    bounds = RECT()
    if not user32.GetWindowRect(root, C.byref(bounds)):
        return False
    try:
        dpi = user32.GetDpiForWindow(root) or 96
    except AttributeError:
        dpi = 96
    edge = max(9, round(18 * dpi / 96))
    return (bounds.left - 3 <= x < bounds.right + 3 and
            bounds.top - 3 <= y < bounds.bottom + 3 and
            (x - bounds.left <= edge or bounds.right - x <= edge or
             y - bounds.top <= edge or bounds.bottom - y <= edge))


def prefer_mouse_over_touch(x, y):
    """True if a contact should behave like a real mouse from its beginning."""
    try:
        return (_custom_title_band(x, y) or
                _near_resize_edge(x, y) or
                window_nonclient_hit(x, y) in NONCLIENT_MOUSE_FALLBACK)
    except Exception:
        # If hit testing is unavailable, do not block ordinary mouse use.
        return False


class Touch:
    """One contact: DOWN -> UPDATE(s) -> UP, in screen pixels."""
    def __init__(self):
        if not user32.InitializeTouchInjection(1, 3):  # TOUCH_FEEDBACK_NONE
            raise C.WinError(C.get_last_error())
        self.active = False
        self.pos = None

    def _send(self, x, y, flags):
        touch = POINTER_TOUCH_INFO()
        touch.pointerInfo.pointerType = 0x00000002  # PT_TOUCH
        touch.pointerInfo.pointerId = 0
        touch.pointerInfo.pointerFlags = flags
        touch.pointerInfo.ptPixelLocation = POINT(x, y)
        touch.touchFlags = 0
        touch.touchMask = 0x0001 | 0x0002 | 0x0004  # contact area, orientation, pressure
        touch.rcContact = RECT(x-2, y-2, x+2, y+2)
        touch.orientation = 90
        touch.pressure = 32000
        if not user32.InjectTouchInput(1, C.byref(touch)):
            raise C.WinError(C.get_last_error())

    def update(self, x, y):
        if not self.active:
            self._send(x, y, 0x00010000 | 0x0002 | 0x0004)  # DOWN, range, contact
            self.active = True
            self.pos = (x, y)
        else:
            self._send(x, y, 0x00020000 | 0x0002 | 0x0004)  # UPDATE, including stationary contact
            self.pos = (x, y)

    def end(self):
        if self.active:
            x, y = self.pos
            try:
                self._send(x, y, 0x00040000)  # UP, same coords as previous UPDATE
            finally:
                self.active = False
                self.pos = None

    def cancel(self):
        """Abandon a stationary touch used to interrupt scrolling inertia.

        Applications that honor POINTER_FLAG_CANCELED should not treat the
        interrupted contact as a completed tap/click.
        """
        if self.active:
            x, y = self.pos
            try:
                # Keep the final UP at the exact position of the last UPDATE.
                self._send(x, y, 0x00020000 | 0x0002 | 0x0004)
                self._send(x, y, 0x00040000 | 0x00008000)  # UP | CANCELED
            finally:
                self.active = False
                self.pos = None
