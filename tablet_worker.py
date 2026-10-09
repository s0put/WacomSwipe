"""Background HID input engine: mouse by default, touch only for swipe gestures.

No Tk calls: Windows modal move/resize loops cannot pause tablet input.
"""
from __future__ import annotations

import queue
import threading
import time

from core import (PRODUCT_IDS, VENDOR_ID, Pad, Pen, SWIPE_TOGGLE, SWIPE_HOLD,
                  RIGHT_MOD, MIDDLE_MOD, choose_pen_route, normalize_absolute,
                  parse_report, select_monitor, surface_to_screen,
                  tip_action, travel_exceeds)
from windows_input import (Touch, button, monitors, mouse_move_absolute,
                           prefer_mouse_over_touch, virtual_desktop)

try:
    import hid
except ImportError:
    hid = None


# Most touch apps stop kinetic scrolling when a new finger touches the surface.
# Use a short grace period so regular mouse clicks resume soon after a swipe.
INERTIA_STOP_WINDOW_S = 2.5


class TabletWorker:
    def __init__(self, status_events):
        self.events = status_events
        self.stop_event = threading.Event()
        self.thread = None
        self.commands = queue.SimpleQueue()
        self.config = {'bindings': {}, 'monitor': 'Automatic (primary monitor)', 'keep_ratio': True, 'press_delay_ms': 450}
        self.snapshot = ('Swipe gesture: ON', 'Pen: not detected')
        self.swipe_enabled = True
        self.pen = None
        self.pad_mask = 0
        self.previous = set()
        self.held = set()
        self.tip_route = None
        self.tip_button = None
        self.tip_start = None
        self.tip_since = 0.0
        self.last_swipe_end = None
        self.online = False
        self.last_touch_frame = 0.0
        self._geometry_at = 0.0
        self._desktop = (0, 0, 1920, 1080)
        self._screens = []
        self._geometry_changed = False

    @staticmethod
    def detect():
        """Find only the USB vendor interface, not the Wacom Bluetooth endpoint."""
        if hid is None:
            raise RuntimeError('HID module missing. Run build-windows.bat.')
        return [d for d in hid.enumerate(VENDOR_ID, 0)
                if d['product_id'] in (PRODUCT_IDS - {0x0377})
                and d.get('usage_page') == 0xFF0D and d.get('usage') == 1]

    def configure(self, bindings, monitor, keep_ratio, press_delay_ms=450):
        # Atomic reference swap, no Tk objects cross to this worker thread.
        self.config = {'bindings': dict(bindings), 'monitor': monitor,
                       'keep_ratio': bool(keep_ratio),
                       'press_delay_ms': int(press_delay_ms)}

    def set_swipe_enabled(self, enabled=None):
        """None toggles; explicit True/False sets the runtime mode."""
        self.commands.put(('swipe', enabled))

    def _status(self, message):
        try:
            self.events.put_nowait(message)
        except queue.Full:
            pass

    def start(self, path, pid):
        if not self.stop():
            raise RuntimeError('The previous connection is still closing.')
        self.stop_event.clear()
        self.commands = queue.SimpleQueue()
        self.thread = threading.Thread(target=self._run, args=(path, pid),
                                       name='Wacom HID + input', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.7)
        return self.thread is None or not self.thread.is_alive()

    def _geometry(self):
        now = time.monotonic()
        if now - self._geometry_at > 0.8:
            self._geometry_at = now
            try:
                desktop = virtual_desktop()
                screens = monitors()
                if self._screens and (screens != self._screens or desktop != self._desktop):
                    self._geometry_changed = True
                self._desktop, self._screens = desktop, screens
            except Exception:
                pass
        return select_monitor(self.config['monitor'], self._screens, self._desktop)

    def _physical(self):
        keys = set()
        if self.pen is not None and self.pen.proximity:
            if self.pen.tip:
                keys.add('Tip')
            if self.pen.barrel1:
                keys.add('Pen button 1')
            if self.pen.barrel2:
                keys.add('Pen button 2')
        for i in range(4):
            if self.pad_mask & (1 << i):
                keys.add(f'Pad button {i + 1}')
        return keys

    def _release(self, touch):
        for action in list(self.held):
            try:
                button(action, False)
            except Exception:
                pass
        self.held.clear()
        if touch is not None:
            try:
                touch.end()
            except Exception:
                pass
        # If still physically pressed after an error, don't restart a new drag.
        self.tip_route = 'ignored' if self.pen is not None and self.pen.tip else None
        self.tip_button = None
        self.tip_start = None
        self.last_swipe_end = None

    def _commands(self, touch):
        while True:
            try:
                kind, value = self.commands.get_nowait()
            except queue.Empty:
                break
            if kind == 'swipe':
                target = (not self.swipe_enabled) if value is None else bool(value)
                if target != self.swipe_enabled:
                    self.swipe_enabled = target
                    self._release(touch)
                    self.previous = self._physical()
            self.snapshot = (self._mode_label(), self.snapshot[1])

    def _mode_label(self):
        return 'Swipe gesture: ON' if self.swipe_enabled else 'Swipe gesture: OFF'

    def _output(self, data, touch):
        if isinstance(data, Pen):
            self.pen = data
        elif isinstance(data, Pad):
            self.pad_mask = data.mask
        else:
            return

        rect = self._geometry()
        if self._geometry_changed:
            self._release(touch)
            self._geometry_changed = False

        bindings = self.config['bindings']
        physical = self._physical()
        rising = physical - self.previous
        if any(bindings.get(key) == SWIPE_TOGGLE for key in rising):
            self.swipe_enabled = not self.swipe_enabled
            self._release(touch)
        self.previous = physical

        pixel = None
        pen = self.pen
        if pen is not None and pen.proximity:
            pixel = surface_to_screen(pen.x, pen.y, rect, self.config['keep_ratio'])

        tip = 'Tip' in physical
        hold_swipe = any(bindings.get(key) == SWIPE_HOLD for key in physical)
        request_swipe = self.swipe_enabled or hold_swipe
        now = time.monotonic()
        tap = None

        if tip and self.tip_route is None and pixel is not None:
            modified = ((('Pen button 1' in physical) and bindings.get('Pen button 1') == RIGHT_MOD) or
                        (('Pen button 2' in physical) and bindings.get('Pen button 2') == MIDDLE_MOD))
            self.tip_button = tip_action(physical, bindings)
            mouse_area = (prefer_mouse_over_touch(*pixel) if request_swipe and not modified
                          and touch is not None else False)
            self.tip_route = choose_pen_route(request_swipe, touch is not None,
                                              mouse_area, modified)
            self.tip_start = pixel
            self.tip_since = now
            # The next touch after a swipe is a finger-down first, to stop
            # kinetic scrolling immediately. It is cancelled on lift so that
            # apps that honor cancellation do not also activate a button.
            if (self.tip_route == 'pending' and self.last_swipe_end is not None
                    and 0 <= now - self.last_swipe_end <= INERTIA_STOP_WINDOW_S):
                self.tip_route = 'brake'
                touch.update(*pixel)
                self.last_touch_frame = now
            # Consume the stop opportunity on the first following pen-down,
            # including when it starts on a non-client area or with modifiers.
            self.last_swipe_end = None

        if tip and self.tip_route == 'pending' and pixel is not None:
            if now - self.tip_since >= self.config['press_delay_ms'] / 1000:
                # Long press allows conventional mouse dragging/selection.
                self.tip_route = 'mouse'
            elif travel_exceeds(self.tip_start, pixel):
                self.tip_route = 'touch'
                # A real stroke begins at initial pen-down, not halfway through the drag.
                touch.update(*self.tip_start)
                self.last_touch_frame = now

        # Braking is not a separate gesture that must always end with pen-up:
        # from this *same* contact the user may start a normal mouse drag
        # by holding still for the configured delay, or swipe again by moving.
        cancel_brake_for_mouse = False
        if tip and self.tip_route == 'brake' and pixel is not None:
            if now - self.tip_since >= self.config['press_delay_ms'] / 1000:
                self.tip_route = 'mouse'
                cancel_brake_for_mouse = True
            elif travel_exceeds(self.tip_start, pixel):
                self.tip_route = 'touch'

        stopped_inertia = False
        if not tip:
            if self.tip_route == 'pending':
                tap = self.tip_button
            elif self.tip_route == 'touch':
                self.last_swipe_end = now
            elif self.tip_route == 'brake':
                stopped_inertia = True
            self.tip_route = None
            self.tip_button = None
            self.tip_start = None

        touch_contact = bool(tip and self.tip_route in ('touch', 'brake')
                             and pixel is not None)
        if touch is not None and touch.active and not touch_contact:
            if stopped_inertia or cancel_brake_for_mouse:
                # End the synthetic finger *before* mouse-down. An ordinary
                # touch UP here could activate a control or steal selection.
                touch.cancel()
            else:
                touch.end()
        if touch_contact:
            touch.update(*pixel)
            self.last_touch_frame = now
        elif pixel is not None:
            nx, ny = normalize_absolute(*pixel, self._desktop)
            mouse_move_absolute(nx, ny)

        # Pen barrel modifiers cannot generate standalone mouse events.
        pad_actions = set()
        for key in physical - {'Tip'}:
            action = bindings.get(key, 'None')
            if action not in ('None', SWIPE_TOGGLE, SWIPE_HOLD, RIGHT_MOD, MIDDLE_MOD):
                pad_actions.add(action)
        active = set() if touch_contact else pad_actions
        if tip and self.tip_route == 'mouse' and self.tip_button not in (None, 'None'):
            active.add(self.tip_button)
        for action in self.held - active:
            button(action, False)
        for action in active - self.held:
            button(action, True)
        self.held = active
        if tap and tap != 'None' and tap not in self.held:
            button(tap, True)
            button(tap, False)

        debug = (f'Pen: X={pen.x}, Y={pen.y}, P={pen.pressure} | screen: {pixel[0]}, {pixel[1]}'
                 if pixel is not None else 'Pen: not detected')
        self.snapshot = (self._mode_label(), debug)

    def _run(self, path, pid):
        device = None
        touch = None
        self.online = False
        self.pen = None
        self.pad_mask = 0
        self.previous = set()
        self.held = set()
        self.tip_route = None
        self.tip_button = None
        self.last_swipe_end = None
        self._geometry_at = 0.0
        self._screens = []
        self._geometry_changed = False
        self.snapshot = (self._mode_label(), 'Pen: not detected')
        try:
            if hid is None:
                raise RuntimeError('HID module not installed')
            device = hid.device()
            device.open_path(path)
            if pid != 0x0377:
                try:
                    device.send_feature_report(bytes((0x02, 0x02)))
                except OSError:
                    pass
            try:
                touch = Touch()
            except Exception as exc:
                self._status(f'Windows touch unavailable ({exc}). Mouse input still available.')
            self._geometry()
            self.online = True
            self._status('USB connected. Tablet ready, even while the window is minimized.')
            while not self.stop_event.is_set():
                self._commands(touch)
                packet = bytes(device.read(512, 30))
                if packet:
                    parsed = parse_report(packet)
                    if parsed is not None:
                        try:
                            self._output(parsed, touch)
                        except Exception as exc:
                            self._release(touch)
                            self._status(f'Input error: {exc}')
                # Re-evaluate display layouts and pending long-press while idle.
                self._geometry()
                if self._geometry_changed:
                    self._release(touch)
                    self._geometry_changed = False
                if (self.pen is not None and self.pen.tip and self.tip_route in ('pending', 'brake')
                        and time.monotonic() - self.tip_since >= self.config['press_delay_ms'] / 1000):
                    # Evaluate hold even when the HID device is not sending new
                    # reports (a completely stationary pen).
                    self._output(self.pen, touch)
                if (touch is not None and touch.active and
                        time.monotonic() - self.last_touch_frame > 0.055):
                    try:
                        touch.update(*touch.pos)
                        self.last_touch_frame = time.monotonic()
                    except Exception as exc:
                        self._release(touch)
                        self._status(f'Touch error: {exc}')
        except Exception as exc:
            if not self.stop_event.is_set():
                self._status(f'Device disconnected / HID error: {exc}')
        finally:
            self._release(touch)
            self.online = False
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass
            self.snapshot = (self._mode_label(), 'Pen: not detected')
            self._status('Tablet disconnected. Waiting for automatic USB reconnection.')
