"""Local executor for Claude's computer-use toolset (computer_toolset_20260801) on macOS,
Windows and Linux (X11).

Screenshots are captured at full resolution (Retina / high-DPI), then resized to the
logical screen size (what pyautogui uses), so the coordinates Claude sends back map 1:1
onto mouse positions. pyautogui is loaded on first use, so JARVIS still starts on a
machine without a desktop (a Linux server), where this tool simply reports that.
"""
import base64
import io
import math
import subprocess
import time

from PIL import Image

from . import plat


class _LazyGUI:
    """pyautogui, imported the first time it's needed (it needs a display to import on Linux)."""
    _mod = None

    def __getattr__(self, name):
        if _LazyGUI._mod is None:
            if not plat.has_display():
                raise RuntimeError("No desktop to control here (no display). Screen, mouse and keyboard "
                                   "control need JARVIS running in a desktop session.")
            import pyautogui as m
            m.FAILSAFE = True  # slam the mouse into a screen corner to abort
            m.PAUSE = 0.05
            _LazyGUI._mod = m
        return getattr(_LazyGUI._mod, name)


pyautogui = _LazyGUI()

MAX_LONG_EDGE = 2576
MAX_VISUAL_TOKENS = 4784

KEY_ALIASES = {
    "return": "enter", "enter": "enter", "kp_enter": "enter",
    "escape": "esc", "esc": "esc",
    "backspace": "backspace", "back_space": "backspace",
    "delete": "delete", "del": "delete",
    "tab": "tab", "space": "space",
    "super": plat.SUPER, "cmd": plat.SUPER, "command": plat.SUPER, "meta": plat.SUPER, "win": plat.SUPER,
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "option" if plat.MAC else "alt", "option": "option" if plat.MAC else "alt",
    "opt": "option" if plat.MAC else "alt",
    "shift": "shift",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "page_up": "pageup", "prior": "pageup", "pageup": "pageup",
    "page_down": "pagedown", "next": "pagedown", "pagedown": "pagedown",
    "home": "home", "end": "end",
    "caps_lock": "capslock", "capslock": "capslock",
    "volumeup": "volumeup", "xf86audioraisevolume": "volumeup",
    "volumedown": "volumedown", "xf86audiolowervolume": "volumedown",
    "volumemute": "volumemute", "xf86audiomute": "volumemute",
}


def _key(name: str) -> str:
    k = name.strip()
    low = k.lower()
    if low in KEY_ALIASES:
        return KEY_ALIASES[low]
    if low.startswith("f") and low[1:].isdigit():
        return low
    return low if len(k) > 1 else k


def _combo(text: str | None) -> list[str]:
    if not text:
        return []
    return [_key(p) for p in text.split("+") if p.strip()]


def screen_size() -> tuple[int, int]:
    w, h = pyautogui.size()
    return int(w), int(h)


def _fit(img: Image.Image) -> Image.Image:
    w, h = img.size
    scale = min(1.0, MAX_LONG_EDGE / max(w, h))
    while math.ceil(w * scale / 28) * math.ceil(h * scale / 28) > MAX_VISUAL_TOKENS:
        scale *= 0.95
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


class ScreenGeometry:
    """Tracks the scale between screenshot pixels and logical screen points."""

    def __init__(self):
        self.scale = 1.0  # screenshot px per logical point

    def to_screen(self, xy) -> tuple[int, int]:
        x, y = xy
        return int(round(x / self.scale)), int(round(y / self.scale))


GEOMETRY = ScreenGeometry()


def capture_screen() -> Image.Image:
    lw, lh = screen_size()
    img = pyautogui.screenshot().convert("RGB")
    img = img.resize((lw, lh), Image.LANCZOS)  # Retina -> logical points
    fitted = _fit(img)
    GEOMETRY.scale = fitted.size[0] / lw
    return fitted


def png_b64(img: Image.Image, fmt: str = "PNG") -> tuple[str, str]:
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.save(buf, format="JPEG", quality=80)
        media = "image/jpeg"
    else:
        img.save(buf, format="PNG", optimize=True)
        media = "image/png"
    return base64.standard_b64encode(buf.getvalue()).decode(), media


def _image_result(img: Image.Image) -> list[dict]:
    data, media = png_b64(img)
    return [{"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}]


def _with_modifiers(mods: list[str], fn):
    for m in mods:
        pyautogui.keyDown(m)
    try:
        fn()
    finally:
        for m in reversed(mods):
            pyautogui.keyUp(m)


def _type_text(text: str) -> None:
    if text.isascii():
        pyautogui.write(text, interval=0.01)
        return
    # Non-ASCII: go through the clipboard, then restore it.
    import pyperclip
    old = pyperclip.paste()
    pyperclip.copy(text)
    pyautogui.hotkey(*plat.PASTE)
    time.sleep(0.15)
    pyperclip.copy(old)


def run_action(name: str, inp: dict) -> list[dict] | str:
    """Execute one computer-toolset member. Returns tool_result content."""
    coord = inp.get("coordinate")
    mods = _combo(inp.get("text")) if name not in ("type", "key", "hold_key") else []

    if name == "screenshot":
        return _image_result(capture_screen())

    if name == "zoom":
        x0, y0, x1, y1 = inp["region"]
        full = capture_screen()
        # Crop from a native-resolution (Retina) capture for extra detail.
        raw = pyautogui.screenshot().convert("RGB")
        rx = raw.size[0] / full.size[0]
        ry = raw.size[1] / full.size[1]
        crop = raw.crop((int(x0 * rx), int(y0 * ry), int(x1 * rx), int(y1 * ry)))
        return _image_result(_fit(crop))

    if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
        if coord:
            x, y = GEOMETRY.to_screen(coord)
            pyautogui.moveTo(x, y, duration=0.12)
        button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
        clicks = {"double_click": 2, "triple_click": 3}.get(name, 1)
        _with_modifiers(mods, lambda: pyautogui.click(button=button, clicks=clicks, interval=0.08))
        return "OK"

    if name == "left_click_drag":
        sx, sy = GEOMETRY.to_screen(inp["start_coordinate"])
        ex, ey = GEOMETRY.to_screen(inp["coordinate"])

        def drag():
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.mouseDown()
            pyautogui.moveTo(ex, ey, duration=0.4)
            pyautogui.mouseUp()

        _with_modifiers(mods, drag)
        return "OK"

    if name == "mouse_move":
        x, y = GEOMETRY.to_screen(coord)
        pyautogui.moveTo(x, y, duration=0.12)
        return "OK"

    if name == "left_mouse_down":
        pyautogui.mouseDown()
        return "OK"
    if name == "left_mouse_up":
        pyautogui.mouseUp()
        return "OK"

    if name == "cursor_position":
        x, y = pyautogui.position()
        return f"[{int(x * GEOMETRY.scale)}, {int(y * GEOMETRY.scale)}]"

    if name == "scroll":
        if coord:
            x, y = GEOMETRY.to_screen(coord)
            pyautogui.moveTo(x, y, duration=0.08)
        amount = int(inp.get("scroll_amount", 3))
        direction = inp.get("scroll_direction", "down")

        def do_scroll():
            if direction in ("up", "down"):
                pyautogui.scroll(amount * 5 if direction == "up" else -amount * 5)
            else:
                pyautogui.hscroll(amount * 5 if direction == "right" else -amount * 5)

        _with_modifiers(mods, do_scroll)
        return "OK"

    if name == "type":
        _type_text(inp["text"])
        return "OK"

    if name == "key":
        keys = _combo(inp["text"])
        for _ in range(int(inp.get("repeat", 1))):
            pyautogui.hotkey(*keys, interval=0.03) if len(keys) > 1 else pyautogui.press(keys[0])
        return "OK"

    if name == "hold_key":
        keys = _combo(inp["text"])
        for k in keys:
            pyautogui.keyDown(k)
        time.sleep(min(float(inp.get("duration", 1)), 300))
        for k in reversed(keys):
            pyautogui.keyUp(k)
        return "OK"

    if name == "wait":
        time.sleep(min(float(inp.get("duration", 1)), 300))
        return "OK"

    raise ValueError(f"Unknown computer action: {name}")


def run_osascript(script: str, timeout: float = 60) -> str:
    if not plat.MAC:
        raise RuntimeError("AppleScript only exists on macOS; use the shell tool instead.")
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise RuntimeError(out or f"osascript exited {proc.returncode}")
    return out or "OK"
