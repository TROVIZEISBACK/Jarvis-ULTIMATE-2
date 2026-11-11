"""Global push-to-talk hotkeys for macOS: ⌘H starts recording, ⌘S finishes and sends it.

A Quartz event tap sees the keys in every app. ⌘H is always swallowed (so macOS doesn't
also "Hide" the front app) and calls `on_press`. ⌘S calls `on_stop` and is swallowed only
while JARVIS is recording (or still recognising); otherwise it passes through, so Save
keeps working everywhere. Needs Accessibility permission
(System Settings → Privacy & Security → Accessibility).
"""
import threading
import time

import Quartz

KEYCODE_H = 4
KEYCODE_S = 1
OTHER_MODIFIERS = (Quartz.kCGEventFlagMaskShift | Quartz.kCGEventFlagMaskAlternate | Quartz.kCGEventFlagMaskControl)


def accessibility_trusted(prompt: bool = False) -> bool:
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))
    except Exception:
        return True  # can't tell; let the tap creation decide


class Hotkey:
    def __init__(self, on_press, on_stop=None):
        self.on_press = on_press
        self.on_stop = on_stop      # returns True if it stopped something
        self.swallow_s_until = 0.0  # keep auto-repeats of a handled ⌘S from reaching the front app
        self.tap = None
        self.ok = False

    def _callback(self, proxy, etype, event, refcon):
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self.tap, True)  # macOS turns slow taps off; turn it back on
            return event
        if etype == Quartz.kCGEventKeyDown:
            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            plain_cmd = flags & Quartz.kCGEventFlagMaskCommand and not flags & OTHER_MODIFIERS
            if code == KEYCODE_S and plain_cmd and self.on_stop:
                if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                    return None if time.time() < self.swallow_s_until else event
                try:
                    handled = self.on_stop()
                except Exception as e:
                    print(f"[hotkey] stop handler error: {e}")
                    handled = False
                if handled:
                    self.swallow_s_until = time.time() + 1.5
                    return None  # swallow: it was meant for JARVIS, not "Save"
                return event
            if code == KEYCODE_H and plain_cmd:
                if not Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                    try:
                        self.on_press()
                    except Exception as e:
                        print(f"[hotkey] handler error: {e}")
                return None  # swallow: don't hide the front app
        return event

    def _create_tap(self) -> bool:
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        self.tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, Quartz.kCGEventTapOptionDefault,
            mask, self._callback, None)
        return bool(self.tap)

    def install(self, on_ready=None) -> bool:
        """Start the tap on its own thread. Without Accessibility permission yet, keep checking
        every 2s, so granting it in System Settings switches ⌘H on without a relaunch."""
        trusted = accessibility_trusted(prompt=True)

        def run():
            if not trusted:
                print("[hotkey] ⌘H is waiting for Accessibility permission "
                      "(System Settings → Privacy & Security → Accessibility → JARVIS).")
            while not self._create_tap():
                time.sleep(2)
            source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
            Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(self.tap, True)
            self.ok = True
            print("[hotkey] ⌘H record / ⌘S send ready.")
            if on_ready:
                on_ready()
            Quartz.CFRunLoopRun()

        threading.Thread(target=run, daemon=True, name="hotkey").start()
        for _ in range(10):  # usually ready almost at once
            if self.ok:
                break
            time.sleep(0.05)
        return self.ok
