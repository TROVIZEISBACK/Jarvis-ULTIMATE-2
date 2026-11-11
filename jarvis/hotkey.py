"""Global push-to-talk keys: start / cancel a recording and send it, from any app.

macOS: ⌘H / ⌘S through a Quartz event tap (hotkey_mac.py).
Windows and Linux (X11): Ctrl+Alt+J starts (and, pressed again, cancels) and Ctrl+Alt+S sends,
through pynput. Without a desktop session (or on Wayland, where apps can't watch global keys)
the mic button in the JARVIS window does the same job.
"""
from . import plat

if plat.MAC:
    from .hotkey_mac import Hotkey  # noqa: F401
else:
    class Hotkey:
        def __init__(self, on_press, on_stop=None):
            self.on_press = on_press
            self.on_stop = on_stop
            self.ok = False
            self.listener = None

        def _safe(self, fn):
            def run():
                try:
                    fn()
                except Exception as e:
                    print(f"[hotkey] handler error: {e}")
            return run

        def install(self, on_ready=None) -> bool:
            if plat.LINUX and not plat.has_display():
                raise RuntimeError("no desktop session")
            from pynput import keyboard
            keys = {"<ctrl>+<alt>+j": self._safe(self.on_press)}
            if self.on_stop:
                keys["<ctrl>+<alt>+s"] = self._safe(self.on_stop)
            self.listener = keyboard.GlobalHotKeys(keys)
            self.listener.daemon = True
            self.listener.start()
            self.ok = True
            print(f"[hotkey] {plat.KEYS['listen']} record / {plat.KEYS['send']} send ready.")
            if on_ready:
                on_ready()
            return True
