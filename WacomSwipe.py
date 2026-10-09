"""WacomSwipe Native: small Windows desktop settings UI.

HID processing stays in background worker threads, independent from Tk.
All controls use native Tk/ttk widgets. No custom drawing or animation.
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

if sys.platform != 'win32':
    raise SystemExit('WacomSwipe runs on Windows 11.')

from core import (ACTIONS, AUTO_MONITOR, FULL_DESKTOP, KEYS, PRESS_DELAYS,
                  defaults, load_settings, save_settings)
from device_manager import TabletService
from shortcuts import (MODIFIER_CODES, is_shortcut, record_modifier_keys,
                       record_shortcut)
from single_instance import SingleInstance, is_manual_launch
from startup import (set_startup, startup_enabled, should_start_minimized,
                     should_check_startup_path, ensure_current_startup_path)
from tray import Tray
from windows_input import (dpi_awareness, monitors, pressed_keyboard_modifiers,
                           virtual_desktop)

# Set DPI awareness before initializing Tk; monitors and tablet use pixel coordinates.
dpi_awareness()


class App(tk.Tk):
    def __init__(self, *, launch_args=None, instance=None):
        super().__init__()
        # Keep the real Windows window manager frame, caption buttons, and taskbar.
        # Tk creates an OS-managed top-level HWND; NEVER use overrideredirect.
        self.title('WacomSwipe')
        self.geometry('555x465')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Unmap>', self.on_unmap)
        self._icon()
        self._instance = instance
        self._activated_externally = False
        # On Windows, ttk's default theme is normally 'vista'. Do not override it.

        try:
            settings = load_settings()
        except Exception as exc:
            settings = defaults()
            messagebox.showwarning('Settings', f'Could not load settings: {exc}', parent=self)

        self.status_events = queue.Queue(maxsize=64)
        self.tray_events = queue.Queue()
        self.service = TabletService(self.status_events)
        self.service.worker.swipe_enabled = settings['swipe_enabled']
        self.tray = Tray(self.tray_events)
        self.running = True
        self._scheduled_after = set()
        self._startup_lock = threading.Lock()
        self._startup_results = queue.Queue()
        self.screen_rects = {}

        self.bindings = {key: tk.StringVar(value=settings['bindings'][key]) for key in KEYS}
        self.monitor_var = tk.StringVar(value=settings['monitor'])
        self.ratio_var = tk.BooleanVar(value=settings['keep_ratio'])
        self.swipe_var = tk.BooleanVar(value=settings['swipe_enabled'])
        self.delay_index = tk.IntVar(value=PRESS_DELAYS.index(settings['press_delay_ms']))
        self.delay_text = tk.StringVar(value=f"{settings['press_delay_ms']} ms")
        self.tray_var = tk.BooleanVar(value=settings['tray_enabled'])
        self.launch_min_var = tk.BooleanVar(value=settings['launch_minimized'])
        try:
            autostart = startup_enabled()
        except OSError:
            autostart = False
        self.startup_var = tk.BooleanVar(value=autostart)
        # Plain Python boolean: background thread must not read Tk variables.
        self._startup_expected = bool(autostart)
        self.connection_label = tk.StringVar(value='Waiting for USB tablet…')
        self.monitor_details = tk.StringVar(value='Detecting display…')
        self.binding_boxes = {}

        self._build_ui()
        self.refresh_monitors()
        for var in [self.monitor_var, self.ratio_var, *self.bindings.values()]:
            var.trace_add('write', self.apply_config)
        self.apply_config()
        self.service.start()
        if self.tray_var.get() and not self._enable_tray():
            self.tray_var.set(False)
        self._call_later(120, self.poll)
        if self._instance is not None:
            self._call_later(100, self._poll_activation)
        self._call_later(2500, self.monitor_watch)
        # Only the Windows Task Scheduler sign-in action adds --autostart.
        # Double-clicking WacomSwipe.exe must always open a normal window.
        if should_start_minimized(self.launch_min_var.get(), launch_args):
            self._call_later(350, self._minimize_at_login)
        if should_check_startup_path(autostart, launch_args):
            # Do not block window creation or the HID worker on schtasks.exe.
            self._call_later(500, self._repair_startup_path_async)

    def _minimize_at_login(self):
        """Do not re-minimize if a new manual launch requested the GUI."""
        if not self._activated_externally:
            self.iconify()

    def _poll_activation(self):
        """Bring this instance back instead of starting another HID reader."""
        if not self.running or self._instance is None:
            return
        if self._instance.consume_activation():
            self._activated_externally = True
            self.restore()
        self._call_later(100, self._poll_activation)

    def _call_later(self, delay_ms, callback):
        """Schedule GUI work and allow pending callbacks to be canceled on exit."""
        task_id = None

        def invoke():
            self._scheduled_after.discard(task_id)
            if self.running:
                callback()

        task_id = self.after(delay_ms, invoke)
        self._scheduled_after.add(task_id)
        return task_id

    def _icon(self):
        # File is bundled with the executable by PyInstaller.
        try:
            root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
            self.iconbitmap(str(root / 'WacomSwipe.ico'))
        except (OSError, tk.TclError):
            pass

    def _build_ui(self):
        wrapper = ttk.Frame(self, padding=12)
        wrapper.pack(fill='both', expand=True)
        tabs = ttk.Notebook(wrapper)
        tabs.pack(fill='both', expand=True)
        tablet = ttk.Frame(tabs, padding=12)
        buttons = ttk.Frame(tabs, padding=12)
        prefs = ttk.Frame(tabs, padding=12)
        tabs.add(tablet, text='Tablet and mapping')
        tabs.add(buttons, text='Buttons and pen')
        tabs.add(prefs, text='Preferences')
        self._tablet_page(tablet)
        self._buttons_page(buttons)
        self._preferences_page(prefs)

    def _tablet_page(self, page):
        status = ttk.LabelFrame(page, text='Connection', padding=12)
        status.pack(fill='x')
        ttk.Label(status, textvariable=self.connection_label, wraplength=470).pack(anchor='w')
        display = ttk.LabelFrame(page, text='Absolute mapping', padding=12)
        display.pack(fill='x', pady=(12, 0))
        ttk.Label(display, text='Display:').grid(row=0, column=0, sticky='w', pady=5)
        self.display_box = ttk.Combobox(display, textvariable=self.monitor_var, state='readonly',
                                        width=36, height=7)
        self.display_box.grid(row=0, column=1, sticky='ew', padx=(10, 8), pady=5)
        ttk.Button(display, text='Refresh', command=self.refresh_monitors).grid(row=0, column=2, sticky='e')
        ttk.Checkbutton(display, text='Keep aspect ratio (center crop)',
                        variable=self.ratio_var).grid(row=1, column=0, columnspan=3, sticky='w', pady=(8, 3))
        ttk.Label(display, textvariable=self.monitor_details).grid(row=2, column=0, columnspan=3,
                                                                  sticky='w', pady=(6, 2))
        display.columnconfigure(1, weight=1)

    def _buttons_page(self, page):
        grid = ttk.Frame(page)
        grid.pack(fill='x')
        ttk.Label(grid, text='Input', font=('Segoe UI', 9, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(grid, text='Action', font=('Segoe UI', 9, 'bold')).grid(row=0, column=1, sticky='w')
        for row, key in enumerate(KEYS, start=1):
            ttk.Label(grid, text=key).grid(row=row, column=0, sticky='w', pady=5)
            value = self.bindings[key].get()
            options = list(ACTIONS)
            if is_shortcut(value) and value not in options:
                options.append(value)
            combo = ttk.Combobox(grid, textvariable=self.bindings[key], state='readonly',
                                 values=options, width=31, height=7)
            combo.grid(row=row, column=1, sticky='ew', padx=(10, 8), pady=5)
            self.binding_boxes[key] = combo
            if key.startswith('Pad button '):
                ttk.Button(grid, text='Record…', width=12,
                           command=lambda k=key: self.record_shortcut(k)).grid(row=row, column=2, pady=5)
        grid.columnconfigure(1, weight=1)

    def _preferences_page(self, page):
        swipe = ttk.LabelFrame(page, text='Swipe', padding=10)
        swipe.pack(fill='x')
        ttk.Checkbutton(swipe, text='Swipe enabled', variable=self.swipe_var,
                        command=self.toggle_swipe).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        ttk.Label(swipe, text='Hold before mouse drag:').grid(row=1, column=0, sticky='w')
        scale = tk.Scale(swipe, from_=0, to=len(PRESS_DELAYS)-1, orient='horizontal',
                         resolution=1, showvalue=False, length=205,
                         variable=self.delay_index, command=self.delay_changed)
        scale.grid(row=1, column=1, sticky='w', padx=(8, 6))
        ttk.Label(swipe, textvariable=self.delay_text, width=8).grid(row=1, column=2, sticky='w')

        startup = ttk.LabelFrame(page, text='Window and startup', padding=10)
        startup.pack(fill='x', pady=(12, 0))
        ttk.Checkbutton(startup, text='Minimize to system tray', variable=self.tray_var,
                        command=self.toggle_tray).pack(anchor='w', pady=4)
        ttk.Checkbutton(startup, text='Start WacomSwipe with Windows', variable=self.startup_var,
                        command=self.toggle_startup).pack(anchor='w', pady=4)
        ttk.Checkbutton(startup, text='Start minimized at Windows sign-in only', variable=self.launch_min_var).pack(anchor='w', pady=4)
        ttk.Button(page, text='Save settings', command=self.save).pack(anchor='w', pady=(15, 0))

    def refresh_monitors(self):
        previous = self.monitor_var.get()
        try:
            found = monitors()
            self.screen_rects = {
                AUTO_MONITOR: next((rect for _, rect, primary in found if primary), virtual_desktop()),
                FULL_DESKTOP: virtual_desktop(),
            }
            for name, rect, primary in found:
                label = f'{name} ({rect[2]}×{rect[3]})' + (' [primary]' if primary else '')
                self.screen_rects[label] = rect
            self.display_box.configure(values=list(self.screen_rects))
            if previous not in self.screen_rects:
                device_name = previous.split(' (')[0]
                previous = next((name for name in self.screen_rects if name.split(' (')[0] == device_name), AUTO_MONITOR)
            self.monitor_var.set(previous)
            r = self.screen_rects[previous]
            self.monitor_details.set(f'Mapped area: {r[2]} × {r[3]} px')
        except Exception as exc:
            self.connection_label.set(f'Monitor detection error: {exc}')

    def monitor_watch(self):
        if not self.running:
            return
        try:
            found = monitors()
            current = {AUTO_MONITOR: next((r for _, r, primary in found if primary), virtual_desktop()),
                       FULL_DESKTOP: virtual_desktop()}
            for name, rect, primary in found:
                current[f'{name} ({rect[2]}×{rect[3]})' + (' [primary]' if primary else '')] = rect
            if current != self.screen_rects:
                self.refresh_monitors()
        except Exception:
            pass
        self._call_later(2500, self.monitor_watch)

    def apply_config(self, *_):
        self.service.configure({key: var.get() for key, var in self.bindings.items()},
                               self.monitor_var.get(), self.ratio_var.get(),
                               PRESS_DELAYS[self.delay_index.get()])

    def delay_changed(self, *_):
        self.delay_text.set(f'{PRESS_DELAYS[self.delay_index.get()]} ms')
        self.apply_config()

    def toggle_swipe(self):
        self.service.set_swipe_enabled(self.swipe_var.get())

    def _enable_tray(self):
        try:
            self.tray.start()
            return True
        except Exception as exc:
            messagebox.showwarning('System tray', f'Unable to create tray icon: {exc}', parent=self)
            return False

    def toggle_tray(self):
        if self.tray_var.get():
            if not self._enable_tray():
                self.tray_var.set(False)
        else:
            self.restore()
            self.tray.stop()

    def _repair_startup_path_async(self):
        """Re-register a moved EXE without pausing the native Windows window."""
        if not self.running or not self._startup_expected:
            return

        def repair():
            try:
                # Coordinate with the toggle and Save settings. Do not repair
                # an activity that the user has just chosen to disable.
                with self._startup_lock:
                    if not self.running or not self._startup_expected:
                        return
                    changed = ensure_current_startup_path()
            except OSError as exc:
                self._startup_results.put(('error', str(exc)))
            else:
                if changed:
                    self._startup_results.put(('updated', None))

        threading.Thread(target=repair, daemon=True,
                         name='WacomSwipe startup path check').start()

    def toggle_startup(self):
        enabled = self.startup_var.get()
        try:
            with self._startup_lock:
                self._startup_expected = enabled
                set_startup(enabled)
        except OSError as exc:
            messagebox.showerror('Windows startup', str(exc), parent=self)
        finally:
            # Reflect the actual Windows registration, including legacy Run
            # entries when Task Scheduler creation is denied by policy.
            try:
                actual = startup_enabled()
                self.startup_var.set(actual)
                self._startup_expected = actual
            except OSError:
                self.startup_var.set(not enabled)
                self._startup_expected = not enabled

    def poll(self):
        if not self.running:
            return
        while True:
            try:
                status, detail = self._startup_results.get_nowait()
            except queue.Empty:
                break
            if status == 'error' and self._startup_expected:
                messagebox.showwarning(
                    'Windows startup',
                    'The Windows sign-in task could not be updated to the new location.\n\n'
                    f'{detail}\n\nYou can retry using Save settings in Preferences.',
                    parent=self)
        while True:
            try:
                event = self.tray_events.get_nowait()
            except queue.Empty:
                break
            if event == 'show':
                self.restore()
            elif event == 'exit':
                self.close()
                if not self.running:
                    return
        while True:
            try:
                self.connection_label.set(self.status_events.get_nowait())
            except queue.Empty:
                break
        # The pen input worker is completely separate from this GUI polling loop.
        if self.service.worker.online:
            name = self.service.name or 'CTL-4100WL'
            status = self.connection_label.get()
            if status.startswith('USB connected.') or status.startswith('Found '):
                self.connection_label.set(f'Connected: {name}')
        self.swipe_var.set(bool(self.service.worker.swipe_enabled))
        self._call_later(120, self.poll)

    def record_shortcut(self, key):
        dialog = tk.Toplevel(self)
        dialog.title(f'Record shortcut: {key}')
        dialog.resizable(False, False)
        dialog.transient(self)
        content = ttk.Frame(dialog, padding=16)
        content.pack(fill='both', expand=True)
        ttk.Label(content, text=f'Press a key or key combination for {key}.',
                  wraplength=345).pack(anchor='w')
        hint = tk.StringVar(value='Example: Ctrl+Shift+S, Alt+Tab, F11')
        ttk.Label(content, textvariable=hint, wraplength=350).pack(anchor='w', pady=(10, 12))
        modifier_keys = set()
        held_modifiers = set()

        def complete(action):
            self.bindings[key].set(action)
            options = list(ACTIONS)
            if is_shortcut(action) and action not in options:
                options.append(action)
            self.binding_boxes[key].configure(values=options)
            dialog.destroy()

        def key_down(event):
            try:
                if event.keycode in MODIFIER_CODES:
                    modifier_keys.add(event.keycode)
                    held_modifiers.add(event.keycode)
                    hint.set('Press another key, or release modifiers to save them alone.')
                else:
                    action = record_shortcut(event.keycode, held_modifiers=pressed_keyboard_modifiers())
                    if action:
                        complete(action)
            except (TypeError, ValueError):
                hint.set('Unsupported key; try another.')
            return 'break'

        def key_up(event):
            if event.keycode in MODIFIER_CODES and modifier_keys:
                held_modifiers.discard(event.keycode)
                if not held_modifiers:
                    action = record_modifier_keys(modifier_keys)
                    if action:
                        complete(action)
            return 'break'

        dialog.bind('<KeyPress>', key_down)
        dialog.bind('<KeyRelease>', key_up)
        footer = ttk.Frame(content)
        footer.pack(fill='x', side='bottom')
        ttk.Button(footer, text='Clear', command=lambda: complete('None')).pack(side='left')
        ttk.Button(footer, text='Cancel', command=dialog.destroy).pack(side='right')
        width, height = 395, 165
        self._center_shortcut_dialog(dialog, width, height)
        dialog.grab_set()
        self._call_later(70, lambda: dialog.focus_force() if dialog.winfo_exists() else None)

    def _center_shortcut_dialog(self, dialog, width, height):
        """Center the *actual Tk client area* even with different Win32 window frames.

        On Windows, geometry offsets and ``winfo_rootx`` can refer to
        differently decorated regions. Starting from an approximate position,
        measure both client areas after mapping and correct the displacement.
        The idle correction also handles a delayed native window placement.
        """
        self.update_idletasks()
        wanted_x = self.winfo_rootx() + (self.winfo_width() - width) // 2
        wanted_y = self.winfo_rooty() + (self.winfo_height() - height) // 2
        dialog.geometry(f'{width}x{height}+{max(0, wanted_x)}+{max(0, wanted_y)}')
        dialog.update_idletasks()

        def correct():
            if not dialog.winfo_exists():
                return
            parent_cx = self.winfo_rootx() + self.winfo_width() / 2
            parent_cy = self.winfo_rooty() + self.winfo_height() / 2
            dialog_cx = dialog.winfo_rootx() + dialog.winfo_width() / 2
            dialog_cy = dialog.winfo_rooty() + dialog.winfo_height() / 2
            dx = round(parent_cx - dialog_cx)
            dy = round(parent_cy - dialog_cy)
            if abs(dx) > 1 or abs(dy) > 1:
                dialog.geometry(f'+{dialog.winfo_x() + dx}+{dialog.winfo_y() + dy}')
                dialog.update_idletasks()

        correct()
        dialog.after_idle(correct)

    def on_unmap(self, event):
        if not self.running or event.widget is not self or not self.tray_var.get():
            return
        self.after_idle(self._hide_when_iconic)

    def _hide_when_iconic(self):
        if self.running and self.tray_var.get() and self.state() == 'iconic':
            self.withdraw()

    def restore(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def save(self):
        settings = {
            'bindings': {key: var.get() for key, var in self.bindings.items()},
            'monitor': self.monitor_var.get(),
            'keep_ratio': self.ratio_var.get(),
            'swipe_enabled': self.service.worker.swipe_enabled,
            'press_delay_ms': PRESS_DELAYS[self.delay_index.get()],
            'tray_enabled': self.tray_var.get(),
            'launch_minimized': self.launch_min_var.get(),
            'settings_version': 4,
        }
        try:
            # Also upgrades an already-enabled legacy HKCU Run entry, and
            # refreshes a moved executable's Task Scheduler command path.
            if self.startup_var.get():
                with self._startup_lock:
                    set_startup(True)
            save_settings(settings)
            messagebox.showinfo('WacomSwipe', 'Settings saved.', parent=self)
        except Exception as exc:
            messagebox.showerror('Save settings', str(exc), parent=self)

    def close(self):
        self.running = False
        if not self.service.stop():
            self.running = True
            messagebox.showwarning('WacomSwipe', 'Tablet service is still stopping. Try again.', parent=self)
            self._call_later(120, self.poll)
            return
        for task_id in tuple(self._scheduled_after):
            try:
                self.after_cancel(task_id)
            except tk.TclError:
                pass
        self._scheduled_after.clear()
        self.tray.stop()
        self.destroy()


def main(args=None):
    """Grab the single-instance mutex before creating the GUI/HID service."""
    if args is None:
        args = sys.argv[1:]
    gate = SingleInstance()
    if not gate.acquire(activate_existing=is_manual_launch(args)):
        return 0
    try:
        app = App(launch_args=args, instance=gate)
        app.mainloop()
    finally:
        # Close handles after the GUI and HID reader have stopped.
        gate.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
