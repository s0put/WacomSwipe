"""USB hotplug watcher, separate from Tk and the HID input thread.

Polling HID enumeration avoids system hooks or another Windows service.
"""
from __future__ import annotations

import threading
import time
from tablet_worker import TabletWorker


class TabletService:
    def __init__(self, status_events, interval=1.0):
        self.worker = TabletWorker(status_events)
        self.events = status_events
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None
        self.path = None
        self.name = None
        self._retry_after = 0.0
        self._missing_announced = False

    def configure(self, bindings, monitor, keep_ratio, press_delay_ms=450):
        self.worker.configure(bindings, monitor, keep_ratio, press_delay_ms)

    def set_swipe_enabled(self, value=None):
        self.worker.set_swipe_enabled(value)

    @property
    def snapshot(self):
        return self.worker.snapshot

    def _announce(self, text):
        try:
            self.events.put_nowait(text)
        except Exception:
            pass

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._watch, name='Wacom USB monitor', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.4)
        worker_stopped = self.worker.stop()
        return (self.thread is None or not self.thread.is_alive()) and worker_stopped

    def _watch(self):
        while not self.stop_event.is_set():
            try:
                candidates = self.worker.detect()
            except Exception as exc:
                self._announce(f'USB scan unavailable: {exc}')
                self.stop_event.wait(2.5)
                continue

            paths = {item['path']: item for item in candidates}
            live = self.worker.thread is not None and self.worker.thread.is_alive()
            if self.path is not None and (self.path not in paths or not live):
                # Unplug, read failure or reset: release all held buttons immediately.
                if not self.worker.stop():
                    self.stop_event.wait(self.interval)
                    continue
                self.path = None
                self.name = None
                self._retry_after = time.monotonic() + (0.5 if not candidates else 1.5)
                self._announce('USB tablet disconnected; waiting for the next connection.')

            if self.path is None and candidates and time.monotonic() >= self._retry_after:
                # Stable selection: USB endpoint only, no experimental Bluetooth connection.
                found = sorted(candidates, key=lambda d: (d['product_id'], str(d['path'])))[0]
                try:
                    self.name = found.get('product_string') or 'CTL-4100WL'
                    self._announce(f'Found {self.name}. Connecting automatically…')
                    self.worker.start(found['path'], found['product_id'])
                    self.path = found['path']
                    self._missing_announced = False
                except Exception as exc:
                    self._retry_after = time.monotonic() + 2.5
                    self._announce(f'Unable to connect to the tablet: {exc}')
            elif self.path is None and not candidates and not self._missing_announced:
                self._announce('Waiting for the USB tablet. It will connect automatically.')
                self._missing_announced = True
            self.stop_event.wait(self.interval)
