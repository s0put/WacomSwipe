"""A small Windows tray icon. Never touch Tk widgets from this thread."""
from __future__ import annotations

import threading


class Tray:
    def __init__(self, messages):
        self.messages = messages
        self.icon = None
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        import pystray
        from PIL import Image, ImageDraw

        image = Image.new('RGBA', (64, 64), (0, 0, 0, 255))
        dc = ImageDraw.Draw(image)
        dc.rounded_rectangle((10, 10, 54, 54), radius=14, outline=(255, 255, 255, 255), width=3)
        dc.line((22, 42, 42, 20), fill=(255, 255, 255, 255), width=4)
        dc.ellipse((18, 39, 26, 47), fill=(255, 255, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem('Open WacomSwipe', lambda _icon, _item: self.messages.put('show'), default=True),
            pystray.MenuItem('Quit', lambda _icon, _item: self.messages.put('exit')),
        )
        self.icon = pystray.Icon('WacomSwipe', image, 'WacomSwipe', menu)
        self.thread = threading.Thread(target=self.icon.run, daemon=True, name='WacomSwipe tray')
        self.thread.start()

    def stop(self):
        if self.icon is not None:
            self.icon.stop()
            self.icon = None
        self.thread = None
