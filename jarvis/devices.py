"""Devices connected to this computer by cable: find them and control them (macOS, Windows, Linux).

- Android phones and tablets (USB, through adb): see the screen, tap, swipe, type, press keys,
  open apps and links, run shell commands, copy files both ways.
- Drives: list and eject (files are handled with the normal shell tool).
- Audio devices (USB headsets, HDMI/DisplayPort monitor speakers...): choose the output or input
  (macOS and Linux; Windows lists them, and switching is done in Sound settings).
- Serial devices (Arduino, ESP32, micro:bit...): send text and read the reply.
- Everything else on USB, plus displays, printers and cameras, is listed so JARVIS knows it's there.

iPhones and iPads are detected but can't be driven over the cable: Apple provides no way to.
"""
import asyncio
import base64
import ctypes
import ctypes.util
import io
import json
import os
import plistlib
import re
import shlex
import shutil
import struct
import subprocess
import time
from pathlib import Path

from . import plat

# ============================================================== Android (adb)

ADB_PLACES = ["/opt/homebrew/bin/adb", "/usr/local/bin/adb", "/usr/bin/adb",
              str(Path.home() / "Library/Android/sdk/platform-tools/adb"),
              str(Path.home() / "Android/Sdk/platform-tools/adb"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe")]
ADB_INSTALL = {"mac": "brew install --cask android-platform-tools",
               "windows": "winget install Google.PlatformTools",
               "linux": "sudo apt install adb (or your distribution's android-tools package)"}[plat.NAME]
MAX_EDGE = 1280          # screenshots are scaled so the long side is at most this
_scale: dict[str, float] = {}   # device serial -> screenshot pixels per device pixel

KEYS = {
    "home": "HOME", "back": "BACK", "recents": "APP_SWITCH", "power": "POWER", "wake": "WAKEUP", "sleep": "SLEEP",
    "volume_up": "VOLUME_UP", "volume_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE", "enter": "ENTER", "tab": "TAB",
    "delete": "DEL", "escape": "ESCAPE", "menu": "MENU", "search": "SEARCH", "camera": "CAMERA",
    "play_pause": "MEDIA_PLAY_PAUSE", "next": "MEDIA_NEXT", "previous": "MEDIA_PREVIOUS",
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT", "notifications": "NOTIFICATION",
}
# adb shell commands that need a spoken yes first
DANGEROUS_ADB = [
    r"\brm\s+-", r"\bpm\s+(uninstall|clear)\b", r"\bcmd\s+package\s+(uninstall|clear)\b", r"\breboot\b", r"\bwipe\b",
    r"\bformat\b", r"factory", r"\bsvc\s+power\b", r"\bsettings\s+put\s+(secure|global)\b", r"\bdd\s+if=",
    r"\bam\s+start\b.*\b(CALL|SENDTO|SEND)\b", r"service\s+call\s+(isms|phone)", r"\bcontent\s+delete\b",
]


class DeviceError(Exception):
    pass


def adb_path() -> str | None:
    return shutil.which("adb") or next((p for p in ADB_PLACES if p and os.path.isfile(p) and os.access(p, os.X_OK)), None)


async def _adb(*args: str, serial: str | None = None, timeout: float = 30, binary: bool = False):
    adb = adb_path()
    if not adb:
        raise DeviceError(f"Android control needs adb, which isn't installed. Offer to install it with: {ADB_INSTALL}")
    cmd = [adb, *(["-s", serial] if serial else []), *args]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise DeviceError(f"The device didn't answer within {timeout:.0f}s.")
    if proc.returncode != 0 and not out:
        raise DeviceError((err or out).decode(errors="replace").strip()[:400] or f"adb exited with {proc.returncode}")
    return out if binary else out.decode(errors="replace").strip()


async def android_devices() -> list[dict]:
    """Android devices on USB: [{serial, state, model}]. state is 'device' when ready,
    'unauthorized' until the user taps Allow on the phone."""
    if not adb_path():
        return []
    try:
        text = await _adb("devices", "-l", timeout=10)
    except DeviceError:
        return []
    found = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            found.append({"serial": parts[0], "state": parts[1], "model": props.get("model", "").replace("_", " "),
                          "wired": "usb" in props or not re.search(r":\d+$|_adb-tls", parts[0])})
    return found


async def _pick(serial: str | None) -> str:
    devs = await android_devices()
    if serial:
        devs = [d for d in devs if d["serial"] == serial]
    ready = [d for d in devs if d["state"] == "device"]
    if ready:
        return ready[0]["serial"]
    if any(d["state"] == "unauthorized" for d in devs):
        raise DeviceError("The phone is connected but hasn't allowed this computer yet. Ask the user to unlock it and tap "
                          "Allow on the \"Allow USB debugging?\" prompt, then try again.")
    raise DeviceError(
        "No Android device is connected. Ask the user to plug it in with a data cable and turn on USB debugging: "
        "Settings > About phone > tap Build number 7 times, then Settings > System > Developer options > USB debugging.")


def _xy(inp: dict, serial: str, *names: str) -> list[int]:
    s = _scale.get(serial, 1.0)
    out = []
    for n in names:
        if not isinstance(inp.get(n), (int, float)):
            raise DeviceError(f"'{n}' is needed (pixels in the latest screenshot)")
        out.append(round(inp[n] / s))
    return out


async def android(inp: dict):
    """Run one Android action. Returns text, or a tool content list for screenshots."""
    from PIL import Image
    action = inp.get("action")
    if action == "devices":
        devs = await android_devices()
        if not adb_path():
            return f"adb isn't installed. Offer to install it with: {ADB_INSTALL}"
        return json.dumps(devs) if devs else "No Android device connected (USB debugging must be on)."
    serial = await _pick(inp.get("serial"))
    sh = lambda *a, **k: _adb("shell", *a, serial=serial, **k)

    if action == "screenshot":
        png = await _adb("exec-out", "screencap", "-p", serial=serial, binary=True, timeout=20)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        scale = min(1.0, MAX_EDGE / max(img.size))
        _scale[serial] = scale
        if scale < 1:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
            {"type": "text", "text": f"Phone screen, {img.width}x{img.height} px. Give tap and swipe coordinates in these pixels."},
        ]
    if action == "tap":
        x, y = _xy(inp, serial, "x", "y")
        await sh("input", "tap", str(x), str(y))
        return "Tapped."
    if action == "long_press":
        x, y = _xy(inp, serial, "x", "y")
        await sh("input", "swipe", str(x), str(y), str(x), str(y), str(int(inp.get("duration_ms") or 800)))
        return "Long-pressed."
    if action == "swipe":
        x, y, x2, y2 = _xy(inp, serial, "x", "y", "x2", "y2")
        await sh("input", "swipe", str(x), str(y), str(x2), str(y2), str(int(inp.get("duration_ms") or 300)))
        return "Swiped."
    if action == "type":
        text = str(inp.get("text") or "")
        if not text:
            raise DeviceError("'text' is needed")
        if not text.isascii():
            raise DeviceError("Android's key input only takes plain English characters; paste other text another way.")
        await sh("input", "text", shlex.quote(text.replace(" ", "%s")))
        return "Typed."
    if action == "key":
        key = str(inp.get("key") or "").strip()
        code = KEYS.get(key.lower()) or (key.upper().removeprefix("KEYCODE_") if re.fullmatch(r"[A-Za-z0-9_]+", key) else None)
        if not code:
            raise DeviceError(f"Unknown key {key!r}. Known: {', '.join(KEYS)}")
        await sh("input", "keyevent", f"KEYCODE_{code}")
        return f"Pressed {key}."
    if action == "open_url":
        url = str(inp.get("url") or "")
        if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
            raise DeviceError("'url' must be a full link, e.g. https://example.com")
        return await sh("am", "start", "-a", "android.intent.action.VIEW", "-d", shlex.quote(url))
    if action in ("apps", "open_app"):
        packages = [l.removeprefix("package:") for l in (await sh("pm", "list", "packages")).splitlines()]
        if action == "apps":
            third = [l.removeprefix("package:") for l in (await sh("pm", "list", "packages", "-3")).splitlines()]
            return "Installed apps (package names): " + ", ".join(sorted(third))
        want = re.sub(r"[^a-z0-9.]", "", str(inp.get("app") or "").lower())
        if not want:
            raise DeviceError("'app' is needed: an app name or package")
        hits = [p for p in packages if p.lower() == want] or [p for p in packages if want in p.lower().replace("_", "")]
        if not hits:
            raise DeviceError(f"No installed app matches {inp.get('app')!r}. Use action 'apps' to see what's installed.")
        if len(hits) > 1:
            hits.sort(key=len)
            best = [p for p in hits if p.lower().split(".")[-1] == want]
            if len(best) != 1 and len(hits) > 6:
                return "Several apps match; pick one package: " + ", ".join(hits[:20])
            hits = best or hits
        out = await sh("monkey", "-p", hits[0], "-c", "android.intent.category.LAUNCHER", "1")
        if "No activities found" in out:
            raise DeviceError(f"{hits[0]} has no screen to open.")
        return f"Opened {hits[0]}."
    if action == "info":
        model, release, battery, power = await asyncio.gather(
            sh("getprop", "ro.product.model"), sh("getprop", "ro.build.version.release"),
            sh("dumpsys", "battery"), sh("dumpsys", "power"))
        level = re.search(r"level: (\d+)", battery)
        charging = re.search(r"status: (\d+)", battery)
        awake = re.search(r"mWakefulness=(\w+)", power)
        size = await sh("wm", "size")
        return json.dumps({"model": model, "android": release, "battery": int(level.group(1)) if level else None,
                           "charging": bool(charging and charging.group(1) in ("2", "5")),
                           "screen": awake.group(1) if awake else "unknown", "resolution": size.split(":")[-1].strip()})
    if action == "shell":
        cmd = str(inp.get("command") or "")
        if not cmd:
            raise DeviceError("'command' is needed")
        out = await sh(cmd, timeout=60)
        return (out[:6000] + "\n…" if len(out) > 6000 else out) or "(no output)"
    if action == "push":
        local, remote = Path(str(inp.get("local") or "")).expanduser(), str(inp.get("remote") or "/sdcard/Download/")
        if not local.exists():
            raise DeviceError(f"{local} doesn't exist on this computer")
        return await _adb("push", str(local), remote, serial=serial, timeout=300)
    if action == "pull":
        remote = str(inp.get("remote") or "")
        if not remote:
            raise DeviceError("'remote' is needed: the file's path on the phone, e.g. /sdcard/DCIM/Camera/photo.jpg")
        local = Path(str(inp.get("local") or Path.home() / "Downloads")).expanduser()
        return await _adb("pull", remote, str(local), serial=serial, timeout=300)
    raise DeviceError(f"Unknown action {action!r}")


# ============================================================== audio devices (CoreAudio)

class _Addr(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


def _cc(s: str) -> int:
    return struct.unpack(">I", s.encode())[0]


_GLOBAL, _OUT, _IN = _cc("glob"), _cc("outp"), _cc("inpt")
_TRANSPORT = {"bltn": "built-in", "usb ": "USB", "hdmi": "HDMI", "dprt": "DisplayPort", "thun": "Thunderbolt",
              "blue": "Bluetooth", "blea": "Bluetooth", "airp": "AirPlay", "virt": "virtual", "grup": "aggregate",
              "1394": "FireWire", "pci ": "PCI", "avb ": "AVB", "ccwd": "Continuity (wired)", "ccwl": "Continuity"}
_WIRED_AUDIO = {"USB", "HDMI", "DisplayPort", "Thunderbolt", "FireWire", "Continuity (wired)"}
_audio = None


def _coreaudio():
    global _audio
    if _audio is None:
        ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        P, u32 = ctypes.POINTER, ctypes.c_uint32
        ca.AudioObjectGetPropertyDataSize.argtypes = [u32, P(_Addr), u32, ctypes.c_void_p, P(u32)]
        ca.AudioObjectGetPropertyData.argtypes = [u32, P(_Addr), u32, ctypes.c_void_p, P(u32), ctypes.c_void_p]
        ca.AudioObjectSetPropertyData.argtypes = [u32, P(_Addr), u32, ctypes.c_void_p, u32, ctypes.c_void_p]
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, u32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        _audio = (ca, cf)
    return _audio


def _prop_size(obj: int, selector: str, scope: int = _GLOBAL) -> int:
    ca, _ = _coreaudio()
    size = ctypes.c_uint32(0)
    addr = _Addr(_cc(selector), scope, 0)
    return size.value if ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(addr), 0, None, ctypes.byref(size)) == 0 else 0


def _prop(obj: int, selector: str, buf, scope: int = _GLOBAL) -> bool:
    ca, _ = _coreaudio()
    size = ctypes.c_uint32(ctypes.sizeof(buf))
    addr = _Addr(_cc(selector), scope, 0)
    return ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(buf)) == 0


def audio_devices() -> list[dict]:
    """Every sound device: name, how it's connected, whether it plays/records, and which are in use."""
    if not plat.MAC:
        return _audio_devices_other()
    _, cf = _coreaudio()
    n = _prop_size(1, "dev#") // 4
    ids = (ctypes.c_uint32 * n)()
    if not n or not _prop(1, "dev#", ids):
        return []
    d_out, d_in = ctypes.c_uint32(0), ctypes.c_uint32(0)
    _prop(1, "dOut", d_out)
    _prop(1, "dIn ", d_in)
    out = []
    for dev in ids:
        ref = ctypes.c_void_p()
        name = ""
        if _prop(dev, "lnam", ref) and ref.value:
            buf = ctypes.create_string_buffer(512)
            if cf.CFStringGetCString(ref, buf, 512, 0x08000100):
                name = buf.value.decode("utf-8", "replace")
            cf.CFRelease(ref)
        tr = ctypes.c_uint32(0)
        _prop(dev, "tran", tr)
        transport = _TRANSPORT.get(struct.pack(">I", tr.value).decode("latin-1"), "other")
        plays, records = _prop_size(dev, "stm#", _OUT) > 0, _prop_size(dev, "stm#", _IN) > 0
        if transport in ("virtual", "aggregate") or not (plays or records):
            continue
        out.append({"id": int(dev), "name": name, "connection": transport, "wired": transport in _WIRED_AUDIO,
                    "output": plays, "input": records,
                    "current_output": dev == d_out.value, "current_input": dev == d_in.value})
    return out


def _audio_devices_other() -> list[dict]:
    """Linux: PulseAudio/PipeWire sinks and sources (pactl). Windows: the devices PortAudio sees."""
    out = []
    if plat.LINUX and plat.which("pactl"):
        def default(kind):
            try:
                return plat.run(["pactl", f"get-default-{kind}"], 3)
            except Exception:
                return ""
        for kind, key in (("sinks", "output"), ("sources", "input")):
            cur = default(kind[:-1])
            try:
                rows = plat.run(["pactl", "list", "short", kind], 5).splitlines()
            except Exception:
                rows = []
            for row in rows:
                cols = row.split("\t")
                if len(cols) < 2 or cols[1].endswith(".monitor"):
                    continue
                dev = cols[1]
                conn = "USB" if "usb" in dev else "HDMI" if "hdmi" in dev else "Bluetooth" if "bluez" in dev else "built-in"
                out.append({"id": dev, "name": dev, "connection": conn, "wired": conn in ("USB", "HDMI"),
                            "output": key == "output", "input": key == "input",
                            "current_output": key == "output" and dev == cur, "current_input": key == "input" and dev == cur})
        return out
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        d_in, d_out = sd.default.device
    except Exception:
        return []
    seen = set()
    for i, d in enumerate(devs):
        if d["name"] in seen:
            continue
        seen.add(d["name"])
        conn = "USB" if "usb" in d["name"].lower() else "HDMI" if re.search("hdmi|display", d["name"], re.I) else "other"
        out.append({"id": i, "name": d["name"], "connection": conn, "wired": conn in ("USB", "HDMI"),
                    "output": d["max_output_channels"] > 0, "input": d["max_input_channels"] > 0,
                    "current_output": i == d_out, "current_input": i == d_in})
    return out


def set_audio(kind: str, name: str) -> str:
    """Make the device whose name contains `name` the computer's sound output ('output') or microphone ('input')."""
    want = name.lower().strip()
    devs = [d for d in audio_devices() if d[kind]]
    hits = [d for d in devs if d["name"].lower() == want] or [d for d in devs if want and want in d["name"].lower()]
    if not hits:
        raise DeviceError(f"No {kind} device matches {name!r}. Available: {', '.join(d['name'] for d in devs)}")
    if plat.LINUX:
        plat.run(["pactl", "set-default-" + ("sink" if kind == "output" else "source"), hits[0]["id"]])
        return f"Sound {kind} is now {hits[0]['name']}."
    if plat.WINDOWS:
        raise DeviceError("Windows doesn't let apps switch the default sound device directly. Open Settings > System > "
                          f"Sound (or the speaker icon in the taskbar) and choose {hits[0]['name']}; the computer tool can do it.")
    ca, _ = _coreaudio()
    dev = ctypes.c_uint32(hits[0]["id"])
    addr = _Addr(_cc("dOut" if kind == "output" else "dIn "), _GLOBAL, 0)
    err = ca.AudioObjectSetPropertyData(1, ctypes.byref(addr), 0, None, 4, ctypes.byref(dev))
    if err:
        raise DeviceError(f"macOS refused to switch (error {err})")
    return f"Sound {kind} is now {hits[0]['name']}."


# ============================================================== serial devices

SERIAL_PORT = re.compile(r"/dev/cu\.[A-Za-z0-9._-]+")
_NOT_SERIAL = ("Bluetooth-Incoming-Port", "debug-console")


def serial_ports() -> list[str]:
    """USB serial ports (Arduino, ESP32, micro:bit, USB-serial adapters)."""
    if not plat.MAC:
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        return sorted(p.device for p in list_ports.comports() if p.vid is not None)  # USB ones only
    return sorted(str(p) for p in Path("/dev").glob("cu.*")
                  if re.search(r"usb|SLAB|wch|serial|ttyACM|UART", p.name, re.I) and not p.name.endswith(_NOT_SERIAL))


def serial_exchange(port: str, text: str | None, baud: int = 9600, seconds: float = 1.5, newline: bool = True) -> str:
    """Open a serial port, optionally send a line, and return what the device says for `seconds`."""
    if not plat.MAC:
        return _serial_exchange_pyserial(port, text, baud, seconds, newline)
    import select
    import termios
    if not SERIAL_PORT.fullmatch(port):
        raise DeviceError("'port' must be a serial port like /dev/cu.usbmodem1101 (see action 'list')")
    speed = getattr(termios, f"B{int(baud)}", None)
    if speed is None:
        raise DeviceError(f"Unsupported speed {baud}; use 9600, 19200, 38400, 57600, 115200 or 230400")
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as e:
        raise DeviceError(f"Couldn't open {port}: {e.strerror}. Close any other program using it (Arduino IDE's Serial Monitor?).")
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0                                   # raw: no translation, no echo
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL             # 8 data bits, no parity, 1 stop bit
        attrs[4] = attrs[5] = speed
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        if text is not None:
            time.sleep(2.0 if re.search("usbmodem|usbserial|wch", port) else 0.1)  # most boards restart when the port opens
            termios.tcflush(fd, termios.TCIFLUSH)
            os.write(fd, (text + ("\n" if newline else "")).encode())
        chunks, end = [], time.time() + max(0.2, min(30.0, seconds))
        while time.time() < end and sum(map(len, chunks)) < 8000:
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if data:
                    chunks.append(data)
        reply = b"".join(chunks).decode("utf-8", "replace").strip()
        return (f"Sent. " if text is not None else "") + (f"Device said:\n{reply}" if reply else "The device sent nothing back.")
    finally:
        os.close(fd)


def _serial_exchange_pyserial(port: str, text: str | None, baud: int, seconds: float, newline: bool) -> str:
    try:
        import serial
    except ImportError:
        raise DeviceError("Serial boards need the pyserial package (pip install pyserial).")
    if port not in serial_ports():
        raise DeviceError(f"'port' must be one of the connected serial ports: {', '.join(serial_ports()) or 'none'}")
    try:
        conn = serial.Serial(port, int(baud), timeout=0.1)
    except Exception as e:
        raise DeviceError(f"Couldn't open {port}: {e}. Close any other program using it (Arduino IDE's Serial Monitor?).")
    with conn:
        if text is not None:
            time.sleep(2.0)  # most boards restart when the port opens
            conn.reset_input_buffer()
            conn.write((text + ("\n" if newline else "")).encode())
        chunks, end = [], time.time() + max(0.2, min(30.0, seconds))
        while time.time() < end and sum(map(len, chunks)) < 8000:
            data = conn.read(4096)
            if data:
                chunks.append(data)
    reply = b"".join(chunks).decode("utf-8", "replace").strip()
    return (f"Sent. " if text is not None else "") + (f"Device said:\n{reply}" if reply else "The device sent nothing back.")


# ============================================================== everything else: discovery

def _profiler(kind: str) -> list:
    try:
        out = subprocess.run(["system_profiler", kind, "-json"], capture_output=True, timeout=15).stdout
        return json.loads(out or b"{}").get(kind, [])
    except Exception:
        return []


def _usb_tree(items: list, found: list):
    for it in items:
        name = it.get("_name", "")
        if it.get("USBKeyHardwareType") != "Built-in" and "USBDeviceKeyVendorID" in it:
            vendor = it.get("USBDeviceKeyVendorName") or it.get("manufacturer") or ""
            kind = ("iPhone/iPad" if re.search(r"iPhone|iPad|iPod", name) else
                    "hub" if "hub" in name.lower() else "")
            found.append({"name": name, "maker": vendor, **({"kind": kind} if kind else {})})
        _usb_tree(it.get("_items", []), found)


def _drives_other() -> list[dict]:
    """Windows and Linux: removable / external volumes, from psutil's list of mounted partitions."""
    import psutil
    drives = []
    for part in psutil.disk_partitions(all=False):
        mp = part.mountpoint
        removable = ("removable" in part.opts) if plat.WINDOWS else \
            mp.startswith(("/media/", "/run/media/", "/mnt/")) and part.fstype not in ("squashfs", "overlay")
        if not removable:
            continue
        try:
            size = psutil.disk_usage(mp).total
        except Exception:
            size = 0
        name = mp.rstrip("\\/") if plat.WINDOWS else Path(mp).name
        drives.append({"name": name, "path": mp, "size_gb": round(size / 1e9, 1), "disk": part.device})
    return drives


def external_drives() -> list[dict]:
    if not plat.MAC:
        return _drives_other()
    try:
        raw = subprocess.run(["diskutil", "list", "-plist", "external", "physical"], capture_output=True, timeout=10).stdout
        info = plistlib.loads(raw)
    except Exception:
        return []
    drives = []
    for disk in info.get("AllDisksAndPartitions", []):
        for part in [disk, *disk.get("Partitions", []), *disk.get("APFSVolumes", [])]:
            if part.get("MountPoint"):
                drives.append({"name": part.get("VolumeName") or Path(part["MountPoint"]).name, "path": part["MountPoint"],
                               "size_gb": round(part.get("Size", 0) / 1e9, 1), "disk": part.get("DeviceIdentifier")})
    return drives


def _usb_other() -> list[dict]:
    try:
        if plat.WINDOWS:
            out = plat.powershell("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB*' -and "
                                  "$_.Class -notin @('USB','HIDClass') } | Select-Object FriendlyName, Class, Manufacturer | "
                                  "ConvertTo-Json -Compress", timeout=20)
            rows = json.loads(out or "[]")
            rows = rows if isinstance(rows, list) else [rows]
            return [{"name": r.get("FriendlyName") or "", "maker": r.get("Manufacturer") or "", "kind": r.get("Class") or ""}
                    for r in rows if r.get("FriendlyName")]
        if plat.which("lsusb"):
            found = []
            for line in plat.run(["lsusb"], 5).splitlines():
                m = re.match(r"Bus \d+ Device \d+: ID [0-9a-f]{4}:[0-9a-f]{4}\s*(.*)", line)
                if m and not re.search(r"root hub|Linux Foundation", m.group(1)):
                    name = m.group(1).strip()
                    found.append({"name": name, "maker": "",
                                  **({"kind": "iPhone/iPad"} if re.search("iPhone|iPad|Apple Mobile", name) else {})})
            return found
    except Exception:
        pass
    return []


def discover() -> dict:
    """Everything attached to this computer by a cable, grouped by what JARVIS can do with it."""
    if not plat.MAC:
        try:
            audio = [d for d in audio_devices() if d["wired"]]
        except Exception:
            audio = []
        return {"usb": _usb_other(), "drives": external_drives(), "displays": [], "audio": audio,
                "serial_ports": serial_ports(), "printers": [], "cameras": []}
    usb: list = []
    for key in ("SPUSBHostDataType", "SPUSBDataType"):
        _usb_tree(_profiler(key), usb)
        if usb:
            break
    displays = [{"name": d.get("_name"), "resolution": d.get("_spdisplays_resolution")}
                for g in _profiler("SPDisplaysDataType") for d in g.get("spdisplays_ndrvs", [])
                if d.get("spdisplays_connection_type") != "spdisplays_internal"]
    cameras = [c.get("_name") for c in _profiler("SPCameraDataType")]
    try:
        printers = [l.split(":", 1)[0].replace("device for ", "") for l in
                    subprocess.run(["lpstat", "-v"], capture_output=True, text=True, timeout=5).stdout.splitlines() if "usb://" in l]
    except Exception:
        printers = []
    try:
        audio = [d for d in audio_devices() if d["wired"]]
    except Exception:
        audio = []
    return {"usb": usb, "drives": external_drives(), "displays": displays, "audio": audio,
            "serial_ports": serial_ports(), "printers": printers, "cameras": cameras}


def eject(name: str) -> str:
    drives = external_drives()
    want = name.lower().strip()
    hits = [d for d in drives if want in (d["name"].lower(), d["path"].lower())] or [d for d in drives if want and want in d["name"].lower()]
    if not hits:
        raise DeviceError(f"No external drive matches {name!r}. Connected: {', '.join(d['name'] for d in drives) or 'none'}")
    d = hits[0]
    try:
        if plat.MAC:
            plat.run(["diskutil", "eject", d["path"]], timeout=60)
        elif plat.WINDOWS:
            letter = d["path"].rstrip("\\/")
            plat.powershell(f"(New-Object -ComObject Shell.Application).Namespace(17).ParseName('{letter}').InvokeVerb('Eject')",
                            timeout=60)
        else:
            if plat.which("udisksctl"):
                plat.run(["udisksctl", "unmount", "-b", d["disk"]], timeout=60)
                try:
                    plat.run(["udisksctl", "power-off", "-b", re.sub(r"p?\d+$", "", d["disk"])], timeout=30)
                except Exception:
                    pass
            else:
                plat.run(["umount", d["path"]], timeout=60)
    except RuntimeError as e:
        raise DeviceError(str(e) or "The drive is busy.")
    return f"{d['name']} is ejected and safe to unplug."


async def wired(inp: dict) -> str:
    """The `wired_devices` tool."""
    action = inp.get("action")
    if action == "list":
        found, phones = await asyncio.gather(asyncio.to_thread(discover), android_devices())
        found["android"] = [p for p in phones if p["wired"]]
        notes = []
        if found["android"]:
            notes.append("Android devices are controlled with the android_device tool.")
        elif not adb_path():
            notes.append(f"Android phones can't be seen until adb is installed ({ADB_INSTALL}).")
        if any(u.get("kind") == "iPhone/iPad" for u in found["usb"]):
            notes.append("iPhones and iPads can't be controlled over the cable (Apple doesn't allow it). If the user has "
                         "set up the iPhone Mirroring app, open it and use the computer tool on its window.")
        if found["drives"]:
            notes.append("Drive files: use the shell tool on the drive's path; eject with action 'eject'.")
        if found["displays"]:
            notes.append("Displays: arrange or mirror them in the system's display settings with the computer tool.")
        found["notes"] = notes
        return json.dumps(found)
    if action == "eject":
        return await asyncio.to_thread(eject, str(inp.get("name") or ""))
    if action == "audio_devices":
        return json.dumps(await asyncio.to_thread(audio_devices))
    if action in ("audio_output", "audio_input"):
        return await asyncio.to_thread(set_audio, action.removeprefix("audio_"), str(inp.get("name") or ""))
    if action in ("serial_send", "serial_read"):
        ports = serial_ports()
        port = str(inp.get("port") or (ports[0] if len(ports) == 1 else ""))
        if not port:
            raise DeviceError(f"'port' is needed. Serial ports: {', '.join(ports) or 'none connected'}")
        text = str(inp.get("text") or "") if action == "serial_send" else None
        if action == "serial_send" and not text:
            raise DeviceError("'text' is needed")
        return await asyncio.to_thread(serial_exchange, port, text, int(inp.get("baud") or 9600),
                                       float(inp.get("seconds") or 1.5), inp.get("newline", True) is not False)
    raise DeviceError(f"Unknown action {action!r}")
