"""Headless layout smoke test for the native Tk GUI.

Runs with xvfb-run on Linux using fake Windows services. On Windows, run
WacomSwipe itself to verify real Win32 integration and the HID device.
"""
import importlib
import os
import sys
import tempfile
import tkinter as tk
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class FakeService:
    def __init__(self, events):
        self.name = 'Intuos BT S'
        self.worker = types.SimpleNamespace(swipe_enabled=True, online=True)
        self.started = False
        self.config = None
    def start(self): self.started = True
    def stop(self): return True
    def configure(self, *args): self.config = args
    def set_swipe_enabled(self, flag): self.worker.swipe_enabled = flag

class FakeTray:
    def __init__(self, queue): self.running = False
    def start(self): self.running = True
    def stop(self): self.running = False

modules = {
    'device_manager': types.SimpleNamespace(TabletService=FakeService),
    'windows_input': types.SimpleNamespace(
        dpi_awareness=lambda: None,
        monitors=lambda: [('\\\\.\\DISPLAY1', (0,0,1920,1080), True),
                          ('\\\\.\\DISPLAY2', (1920,0,2560,1440), False)],
        virtual_desktop=lambda: (0,0,4480,1440),
        pressed_keyboard_modifiers=lambda: {0x11},
    ),
    'startup': types.SimpleNamespace(set_startup=lambda x: None, startup_enabled=lambda: False,
                                     should_start_minimized=lambda pref, args=None: bool(pref) and '--autostart' in (args or []),
                                     should_check_startup_path=lambda enabled, args=None: False,
                                     ensure_current_startup_path=lambda: False),
    'tray': types.SimpleNamespace(Tray=FakeTray),
}

with tempfile.TemporaryDirectory() as tmp:
    with patch.dict(sys.modules, modules):
        previous_platform = sys.platform
        sys.platform = 'win32'
        try:
            import core
            with patch.object(core, 'settings_path', return_value=Path(tmp) / 'settings.json'):
                gui = importlib.import_module('WacomSwipe')
                app = gui.App()
                app.update()
                assert app.service.started
                assert app.winfo_width() == 555 and app.winfo_height() == 465
                assert not bool(app.resizable()[0])
                notebook = next(c for c in app.winfo_children()[0].winfo_children() if isinstance(c, gui.ttk.Notebook))
                assert len(notebook.tabs()) == 3
                for tab_name in ('Tablet and mapping', 'Buttons and pen', 'Preferences'):
                    idx = next(i for i, tab in enumerate(notebook.tabs()) if notebook.tab(tab, 'text') == tab_name)
                    notebook.select(idx)
                    app.update()
                    assert notebook.winfo_ismapped()
                    if tab_name == 'Buttons and pen':
                        assert len(app.binding_boxes) == 7
                        for field in app.binding_boxes.values():
                            assert field.winfo_ismapped()
                            assert field.winfo_rootx() + field.winfo_width() <= app.winfo_rootx() + app.winfo_width()
                            assert field.cget('height') == 7
                    if tab_name == 'Preferences':
                        widgets = list(notebook.winfo_children()[idx].winfo_children())
                        assert widgets
                app.record_shortcut('Pad button 1')
                app.update()
                dialogs = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
                assert len(dialogs) == 1
                d = dialogs[0]
                assert abs((d.winfo_rootx()+d.winfo_width()/2) - (app.winfo_rootx()+app.winfo_width()/2)) < 12
                assert abs((d.winfo_rooty()+d.winfo_height()/2) - (app.winfo_rooty()+app.winfo_height()/2)) < 12
                d.destroy()
                with patch.object(gui.messagebox, "showinfo", return_value=None):
                    app.save()
                assert (Path(tmp)/'settings.json').exists()
                app.close()
                print('PASS: Tk native UI opens, 3 tabs, all bindings, menu heights, preferences, centered shortcut dialog, saves, closes')

                # Integration test: the same saved preference must NOT minimize
                # a manual launch, but MUST minimize a Windows-sign-in launch.
                settings = core.defaults()
                settings['launch_minimized'] = True
                core.save_settings(settings)
                for args, expected in (([], 0), (['--autostart'], 1)):
                    with patch.object(gui.App, 'iconify', autospec=True) as mock_iconify:
                        instance = gui.App(launch_args=args)
                        instance.after(430, instance.quit)
                        instance.mainloop()
                        assert mock_iconify.call_count == expected, (
                            f'launch_args={args}: expected iconify calls={expected}, '
                            f'got {mock_iconify.call_count}'
                        )
                        instance.close()
                print('PASS: manual launch stays visible; Windows-sign-in launch minimizes only when enabled')
        finally:
            sys.platform = previous_platform
