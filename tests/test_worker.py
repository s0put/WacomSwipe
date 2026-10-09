"""Integration tests with fake HID and Windows inputs; no hardware required."""
import importlib
import queue
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import DEFAULT_BINDINGS, SWIPE_TOGGLE, MAX_X, MAX_Y


class FakeTouch:
    def __init__(self):
        self.active = False
        self.pos = None
        self.events = []

    def update(self, x, y):
        self.active = True
        self.pos = (x, y)
        self.events.append(('touch', x, y))

    def end(self):
        if self.active:
            self.events.append(('end',))
        self.active = False
        self.pos = None

    def cancel(self):
        if self.active:
            self.events.append(('cancel',))
        self.active = False
        self.pos = None


class FakeHIDDevice:
    def __init__(self, reports):
        self.reports = reports
        self.closed = False

    def open_path(self, path):
        pass

    def send_feature_report(self, packet):
        return len(packet)

    def read(self, amount, timeout):
        try:
            return self.reports.get(timeout=0.01)
        except queue.Empty:
            return []

    def close(self):
        self.closed = True


def pen(pressed=False, x=MAX_X // 2, y=MAX_Y // 2, p1=False, p2=False):
    from core import Pen
    return Pen(x, y, 400 if pressed else 0, True, p1, p2)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.actions = []
        self.touches = []
        fake_windows = types.ModuleType('windows_input')
        fake_windows.Touch = self.make_touch
        fake_windows.button = lambda act, press: self.actions.append((act, press))
        fake_windows.mouse_move_absolute = lambda x, y: self.actions.append(('move', x, y))
        fake_windows.monitors = lambda: [('MON1', (0, 0, 1920, 1080), True)]
        fake_windows.virtual_desktop = lambda: (0, 0, 1920, 1080)
        fake_windows.prefer_mouse_over_touch = lambda x, y: x < 25 or x > 1894
        self.patch_sys = patch.dict(sys.modules, {'windows_input': fake_windows})
        self.patch_sys.start()
        sys.modules.pop('tablet_worker', None)
        self.module = importlib.import_module('tablet_worker')
        self.worker = self.module.TabletWorker(queue.Queue())
        self.worker.configure(DEFAULT_BINDINGS, 'Automatico (monitor principale)', False)
        self.touch = self.make_touch()

    def tearDown(self):
        sys.modules.pop('tablet_worker', None)
        self.patch_sys.stop()

    def make_touch(self):
        t = FakeTouch()
        self.touches.append(t)
        return t

    def send(self, *reports):
        for report in reports:
            self.worker._output(report, self.touch)

    def test_touch_tap_is_normal_mouse_click(self):
        self.send(pen(True), pen(False))
        self.assertEqual([x for x in self.actions if x[0] == 'Left click'],
                         [('Left click', True), ('Left click', False)])
        self.assertFalse(self.touch.events)

    def test_swipe_sends_touch_but_no_click(self):
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.assertGreaterEqual(len([e for e in self.touch.events if e[0] == 'touch']), 2)
        self.assertIn(('end',), self.touch.events)
        self.assertNotIn(('Left click', True), self.actions)

    def test_tap_after_swipe_stops_inertia_without_mouse_click(self):
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.assertIn(('end',), self.touch.events)
        self.actions.clear()
        self.touch.events.clear()
        # New pen-down immediately contacts the screen, stopping inertia.
        self.send(pen(True))
        self.assertTrue(self.touch.active)
        self.assertEqual(self.worker.tip_route, 'brake')
        self.assertTrue(any(e[0] == 'touch' for e in self.touch.events))
        self.send(pen(False))
        self.assertIn(('cancel',), self.touch.events)
        self.assertFalse(self.touch.active)
        self.assertNotIn(('Left click', True), self.actions)
        # The following tap is again a normal mouse click.
        self.actions.clear()
        self.send(pen(True), pen(False))
        self.assertEqual([e for e in self.actions if e[0] == 'Left click'],
                         [('Left click', True), ('Left click', False)])

    def test_braking_touch_can_become_mouse_selection_on_same_contact(self):
        # No extra lift/tap: stop inertia, keep the tip down, then select text.
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.actions.clear()
        self.touch.events.clear()
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'brake')
        self.assertTrue(self.touch.active)
        # Before the configured delay, touch remains active and no click occurs.
        self.worker.tip_since -= 0.2
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'brake')
        self.assertNotIn(('Left click', True), self.actions)
        self.worker.tip_since -= 0.3
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'mouse')
        self.assertIn(('cancel',), self.touch.events)
        self.assertFalse(self.touch.active)
        self.assertIn(('Left click', True), self.actions)
        # The left mouse button must remain down for the *same* pen contact.
        self.send(pen(True, y=MAX_Y // 2 + 500))
        self.assertEqual(self.worker.tip_route, 'mouse')
        self.assertEqual([e for e in self.actions if e[0] == 'Left click'],
                         [('Left click', True)])
        self.send(pen(False))
        self.assertEqual([e for e in self.actions if e[0] == 'Left click'],
                         [('Left click', True), ('Left click', False)])

    def test_brake_touch_cancel_happens_before_mouse_down(self):
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.send(pen(True))
        order = []
        original_cancel = self.touch.cancel
        original_button = self.module.button
        def capture_cancel():
            order.append('touch-cancel')
            original_cancel()
        def capture_button(action, pressed):
            if action == 'Left click' and pressed:
                order.append('mouse-down')
            original_button(action, pressed)
        self.touch.cancel = capture_cancel
        self.module.button = capture_button
        try:
            self.worker.tip_since -= 1.0
            self.send(pen(True))
            self.assertEqual(order, ['touch-cancel', 'mouse-down'])
        finally:
            self.module.button = original_button

    def test_next_contact_after_braking_tap_can_long_press_and_drag(self):
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.send(pen(True), pen(False))  # tap to stop inertia
        self.actions.clear()
        self.touch.events.clear()
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'pending')
        self.worker.tip_since -= 0.6
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 500), pen(False))
        self.assertIn(('Left click', True), self.actions)
        self.assertIn(('Left click', False), self.actions)
        self.assertFalse(self.touch.events)

    def test_brake_respects_slider_time_before_selecting(self):
        self.worker.configure(DEFAULT_BINDINGS, 'Automatico (monitor principale)', False, 800)
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.send(pen(True))
        self.worker.tip_since -= 0.6
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'brake')
        self.assertNotIn(('Left click', True), self.actions)
        self.worker.tip_since -= 0.25
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'mouse')
        self.assertIn(('Left click', True), self.actions)

    def test_drag_after_braking_becomes_new_swipe(self):
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'brake')
        self.send(pen(True, y=MAX_Y // 2 + 1100))
        self.assertEqual(self.worker.tip_route, 'touch')
        self.send(pen(False))
        self.assertEqual(self.touch.events[-1], ('end',))
        self.assertIsNotNone(self.worker.last_swipe_end)

    def test_late_tap_after_swipe_is_regular_mouse_click(self):
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.worker.last_swipe_end -= 5.0
        self.actions.clear()
        self.touch.events.clear()
        self.send(pen(True), pen(False))
        self.assertIn(('Left click', True), self.actions)
        self.assertNotIn(('cancel',), self.touch.events)

    def test_nonclient_or_modifier_after_swipe_uses_mouse(self):
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.send(pen(True, x=3), pen(False, x=3))
        self.assertNotIn(('cancel',), self.touch.events)
        self.assertIn(('Left click', True), self.actions)
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.actions.clear()
        self.send(pen(True, p1=True), pen(False, p1=True))
        self.assertIn(('Right click', True), self.actions)
        self.assertNotIn(('cancel',), self.touch.events)

    def test_off_mode_immediate_mouse_drag(self):
        self.worker.swipe_enabled = False
        self.send(pen(True), pen(True, y=MAX_Y // 2 + 1100), pen(False))
        self.assertEqual([e for e in self.actions if e[0] == 'Left click'],
                         [('Left click', True), ('Left click', False)])
        self.assertFalse(self.touch.events)

    def test_held_pen1_does_not_click_alone(self):
        self.send(pen(False, p1=True))
        self.assertFalse(any(e[0].startswith('Click') for e in self.actions))

    def test_pen1_tip_right_mouse_even_with_swipe_enabled(self):
        self.send(pen(False, p1=True), pen(True, p1=True), pen(False, p1=True))
        self.assertEqual([e for e in self.actions if e[0] == 'Right click'],
                         [('Right click', True), ('Right click', False)])
        self.assertFalse(self.touch.events)

    def test_pen2_tip_middle_mouse_even_with_swipe_enabled(self):
        self.send(pen(True, p2=True), pen(False, p2=True))
        self.assertEqual([e for e in self.actions if e[0] == 'Middle click'],
                         [('Middle click', True), ('Middle click', False)])
        self.assertFalse(self.touch.events)

    def test_both_modifiers_right_wins(self):
        self.send(pen(True, p1=True, p2=True), pen(False, p1=True, p2=True))
        self.assertIn(('Right click', True), self.actions)
        self.assertNotIn(('Middle click', True), self.actions)

    def test_resize_at_left_edge_never_injects_touch(self):
        self.send(pen(True, x=5), pen(True, x=600), pen(False, x=600))
        self.assertIn(('Left click', True), self.actions)
        self.assertFalse(self.touch.events)

    def test_resize_at_right_edge_never_injects_touch(self):
        self.send(pen(True, x=MAX_X-1), pen(True, x=MAX_X - 600), pen(False))
        self.assertIn(('Left click', True), self.actions)
        self.assertFalse(self.touch.events)

    def test_discrete_hold_delay_changes_drag_routing(self):
        self.worker.configure(DEFAULT_BINDINGS, 'Automatico (monitor principale)', False, 800)
        self.send(pen(True))
        self.worker.tip_since -= 0.5
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'pending')
        self.worker.tip_since -= 0.35
        self.send(pen(True))
        self.assertEqual(self.worker.tip_route, 'mouse')
        self.assertIn(('Left click', True), self.actions)

    def test_shortcut_assigned_to_pad_is_key_action(self):
        from core import Pad
        custom = dict(DEFAULT_BINDINGS)
        custom['Pad button 2'] = 'Shortcut: Ctrl+Shift+S'
        self.worker.configure(custom, 'Automatico (monitor principale)', False, 450)
        self.send(Pad(0b0010), Pad(0))
        self.assertEqual([event for event in self.actions if event[0].startswith('Shortcut:')],
                         [('Shortcut: Ctrl+Shift+S', True),
                          ('Shortcut: Ctrl+Shift+S', False)])

    def test_long_press_allows_drag(self):
        self.send(pen(True))
        self.worker.tip_since -= 1.0
        self.send(pen(True, y=MAX_Y // 2 + 600), pen(False))
        self.assertIn(('Left click', True), self.actions)
        self.assertFalse(self.touch.events)

    def test_toggle_binding_on_pad(self):
        self.assertTrue(self.worker.swipe_enabled)
        from core import Pad
        self.send(Pad(1))
        self.assertFalse(self.worker.swipe_enabled)
        self.send(Pad(1))
        self.assertFalse(self.worker.swipe_enabled)
        self.send(Pad(0), Pad(1))
        self.assertTrue(self.worker.swipe_enabled)

    def test_changing_mode_releases_active_click(self):
        self.worker.swipe_enabled = False
        self.send(pen(True))
        self.assertIn(('Left click', True), self.actions)
        self.worker.set_swipe_enabled(True)
        self.worker._commands(self.touch)
        self.assertIn(('Left click', False), self.actions)
        self.assertTrue(self.worker.swipe_enabled)

    def test_usb_only_filter(self):
        available = [{'product_id': 0x0376, 'usage_page': 0xFF0D, 'usage': 1, 'path': b'usb'},
                     {'product_id': 0x0377, 'usage_page': 0xFF0D, 'usage': 1, 'path': b'bt'},
                     {'product_id': 0x0376, 'usage_page': 0x01, 'usage': 1, 'path': b'mouse'}]
        self.module.hid = types.SimpleNamespace(enumerate=lambda vid, pid: available)
        self.assertEqual([d['path'] for d in self.worker.detect()], [b'usb'])

    def test_without_gui_events_input_remains_active(self):
        self.worker.swipe_enabled = False
        packets = queue.Queue()
        device = FakeHIDDevice(packets)
        self.module.hid = types.SimpleNamespace(device=lambda: device)
        self.worker.start(b'fake', 0x0376)
        from core import Pen
        def packet(pressed):
            b = bytearray(17)
            b[0], b[1] = 0x10, 0x20
            b[2:5] = (7600).to_bytes(3, 'little')
            b[5:8] = (4750).to_bytes(3, 'little')
            b[8:10] = (400 if pressed else 0).to_bytes(2, 'little')
            return b
        packets.put(packet(True))
        packets.put(packet(False))
        time.sleep(0.08)
        self.assertTrue(self.worker.stop())
        self.assertTrue(device.closed)
        self.assertIn(('Left click', True), self.actions)
        self.assertIn(('Left click', False), self.actions)


class WatcherTests(unittest.TestCase):
    def test_usb_auto_connect_disconnect_reconnect(self):
        available = []
        class FakeThread:
            def __init__(self): self.active = True
            def is_alive(self): return self.active
        class FakeWorker:
            def __init__(self, events):
                self.thread = None
                self.connects = []
                self.stops = 0
                self.snapshot = ('Swipe gesture: ON', 'Pen: not detected')
            def detect(self): return list(available)
            def configure(self, *args): pass
            def stop(self):
                self.stops += 1
                if self.thread: self.thread.active = False
                return True
            def start(self, path, pid):
                self.thread = FakeThread()
                self.connects.append(path)
            def set_swipe_enabled(self, flag=None): pass
        mock_worker_module = types.ModuleType('tablet_worker')
        mock_worker_module.TabletWorker = FakeWorker
        with patch.dict(sys.modules, {'tablet_worker': mock_worker_module}):
            sys.modules.pop('device_manager', None)
            from device_manager import TabletService
            service = TabletService(queue.Queue(), interval=0.015)
            service.start()
            try:
                available.append({'path': b'usb1', 'product_id': 0x0376, 'product_string': 'Intuos'})
                time.sleep(0.08)
                self.assertEqual(service.worker.connects, [b'usb1'])
                available.clear()
                time.sleep(0.065)
                self.assertIsNone(service.path)
                # Wait for the anti-retry delay then reinsert.
                service._retry_after = 0
                available.append({'path': b'usb2', 'product_id': 0x0376, 'product_string': 'Intuos'})
                time.sleep(0.08)
                self.assertEqual(service.worker.connects, [b'usb1', b'usb2'])
            finally:
                self.assertTrue(service.stop())


if __name__ == '__main__':
    unittest.main()
