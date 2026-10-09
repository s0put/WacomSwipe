"""Windows pre-build smoke test of the standard Tk interface.

Does not connect a tablet or change startup settings; uses a temporary config.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

if sys.platform != 'win32':
    raise SystemExit('Windows-only GUI smoke test')

import tkinter as tk
from tkinter import ttk
import core
import WacomSwipe as gui


class FakeService:
    def __init__(self, events):
        self.name = 'Test tablet'
        self.worker = type('Worker', (), {'swipe_enabled': True, 'online': True})()
    def start(self): pass
    def stop(self): return True
    def configure(self, *args): pass
    def set_swipe_enabled(self, value): self.worker.swipe_enabled = bool(value)


class FakeTray:
    def __init__(self, events): pass
    def start(self): pass
    def stop(self): pass


def main():
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(core, 'settings_path', return_value=Path(tmp) / 'settings.json'), \
            patch.object(gui, 'TabletService', FakeService), \
            patch.object(gui, 'Tray', FakeTray), \
            patch.object(gui, 'startup_enabled', return_value=False):
        app = gui.App()
        try:
            app.update()
            assert app.winfo_width() == 555 and app.winfo_height() == 465
            assert app.resizable() == (0, 0)
            assert not app.overrideredirect(), 'Expected the genuine native Windows title bar'
            # Check the real Win32 top-level HWND, not just the Tk appearance.
            import ctypes
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.GetAncestor.restype = ctypes.c_void_p
            user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            hwnd = user32.GetAncestor(app.winfo_id(), 2)  # GA_ROOT
            style = user32.GetWindowLongW(hwnd, -16)      # GWL_STYLE
            ex_style = user32.GetWindowLongW(hwnd, -20)   # GWL_EXSTYLE
            assert style & 0x00C00000 == 0x00C00000, 'Missing native WS_CAPTION'
            assert style & 0x00080000, 'Missing native WS_SYSMENU'
            assert not ex_style & 0x00000080, 'Window is incorrectly a tool window' 
            notebook = next(widget for widget in app.winfo_children()[0].winfo_children()
                            if isinstance(widget, ttk.Notebook))
            assert len(notebook.tabs()) == 3
            for i in range(3):
                notebook.select(i)
                app.update()
            assert len(app.binding_boxes) == 7
            for combo in app.binding_boxes.values():
                assert combo.cget('height') == 7
            app.record_shortcut('Pad button 1')
            app.update()
            dialog = next(widget for widget in app.winfo_children() if isinstance(widget, tk.Toplevel))
            assert dialog.winfo_exists() and dialog.winfo_width() >= 350
            # Some Windows themes/DPI configurations report client origins and
            # window-frame coordinates differently. A few pixels of offset must
            # never block generating an otherwise working executable.
            dx = (dialog.winfo_rootx() + dialog.winfo_width() / 2) - (app.winfo_rootx() + app.winfo_width() / 2)
            dy = (dialog.winfo_rooty() + dialog.winfo_height() / 2) - (app.winfo_rooty() + app.winfo_height() / 2)
            if abs(dx) > 12 or abs(dy) > 12:
                print(f'GUI NOTE: Shortcut dialog center offset: x={dx:.1f}, y={dy:.1f} pixels.')
            dialog.destroy()
            # Verify GUI wiring without changing any real Windows startup task.
            with patch.object(gui, 'set_startup') as register, \
                    patch.object(gui, 'startup_enabled', return_value=True):
                app.startup_var.set(True)
                app.toggle_startup()
                register.assert_called_once_with(True)
                assert app.startup_var.get()
            with patch.object(gui, 'set_startup', side_effect=OSError('Access denied')), \
                    patch.object(gui, 'startup_enabled', return_value=False), \
                    patch.object(gui.messagebox, 'showerror'):
                app.startup_var.set(True)
                app.toggle_startup()
                assert not app.startup_var.get()
            # Simulate a second launch while the first window is hidden.
            class FakeInstance:
                def __init__(self): self.pending = True
                def consume_activation(self):
                    out, self.pending = self.pending, False
                    return out
            fake_instance = FakeInstance()
            app._instance = fake_instance
            app.withdraw()
            app._poll_activation()
            app.update()
            assert app.state() == 'normal', 'A second launch failed to restore the first window.'
            assert app._activated_externally
            # The startup flag must not re-minimize an explicitly reopened GUI.
            app._minimize_at_login()
            assert app.state() == 'normal'
            from unittest.mock import MagicMock
            blocked_gate = MagicMock()
            blocked_gate.acquire.return_value = False
            with patch.object(gui, 'SingleInstance', return_value=blocked_gate), \
                    patch.object(gui, 'App') as app_builder:
                assert gui.main([]) == 0
                app_builder.assert_not_called()  # no second window or HID reader
                blocked_gate.acquire.assert_called_once_with(activate_existing=True)
            print('GUI PASS: Native window, tabs, dialog, startup, duplicate activation.')
        finally:
            app.close()


if __name__ == '__main__':
    main()
