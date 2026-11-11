"""Windows integrations: media keys, volume, dark mode, lock, display sleep, notifications, Wi-Fi.

Volume is read and set through the Windows Core Audio API (pycaw) when it's installed, otherwise
by pressing the volume keys. Media control uses the media keys, which every player listens to.
"""
import ctypes
import urllib.parse

from . import plat

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
      "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF}
KEYUP = 0x0002


def _press(vk: int, times: int = 1):
    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYUP, 0)


def _endpoint():
    """The default speaker's IAudioEndpointVolume (pycaw), or None."""
    try:
        from pycaw.pycaw import AudioUtilities
        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, "EndpointVolume"):          # pycaw 2024+
            return speakers.EndpointVolume
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(iface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def _com(fn):
    """Run fn with COM initialised on this thread (tools run in worker threads)."""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        comtypes = None
    try:
        return fn()
    finally:
        if comtypes:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def _dark() -> bool | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return None


def status() -> dict:
    def read():
        ep = _endpoint()
        if ep is None:
            return {"volume": None, "muted": False}
        return {"volume": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}
    return {**_com(read), "dark": _dark()}


quick_status = status


def now_playing() -> dict | None:
    return None   # Windows doesn't expose the current track without extra packages; controls still work


def media(action: str, query: str | None = None, player: str | None = None) -> str:
    if action == "now_playing":
        return "I can't read the current track on Windows, but I can play, pause and skip."
    keys = {"play": "play_pause", "pause": "play_pause", "toggle": "play_pause", "next": "next", "previous": "previous"}
    if action in keys:
        _press(VK[keys[action]])
        return f"Sent {action} to the media player."
    if action == "play_song":
        if not query:
            raise ValueError("play_song needs a query")
        if player == "Spotify" or plat.which("spotify"):
            plat.open_target("spotify:search:" + urllib.parse.quote(query))
            return "Opened a Spotify search; pick the track on screen."
        plat.open_target("https://music.youtube.com/search?q=" + urllib.parse.quote(query))
        return "Opened a YouTube Music search for it."
    raise ValueError(f"Unknown media action {action!r}")


def _set_volume(v: int) -> str:
    def go():
        ep = _endpoint()
        if ep is None:
            return False
        ep.SetMute(0, None)
        ep.SetMasterVolumeLevelScalar(v / 100, None)
        return True
    if not _com(go):
        cur = status()["volume"]
        if cur is None:   # no pycaw: approximate with the volume keys (2% per press)
            _press(VK["volume_down"], 50)
            _press(VK["volume_up"], v // 2)
    return f"Volume set to {v}%."


def system_control(action: str, value=None) -> str:
    if action == "status":
        s = status()
        vol = f"Volume {s['volume']}%" if s["volume"] is not None else "Volume unknown"
        return f"{vol}{' (muted)' if s['muted'] else ''}, {'dark' if s['dark'] else 'light'} mode."
    if action == "set_volume":
        return _set_volume(max(0, min(100, int(float(value)))))
    if action in ("volume_up", "volume_down"):
        step = int(float(value)) if value else 10
        cur = status()["volume"]
        if cur is None:
            _press(VK[action], max(1, step // 2))
            return "Volume adjusted."
        return _set_volume(max(0, min(100, cur + (step if action == "volume_up" else -step))))
    if action in ("mute", "unmute"):
        def go():
            ep = _endpoint()
            if ep is None:
                return False
            ep.SetMute(1 if action == "mute" else 0, None)
            return True
        if not _com(go):
            _press(VK["mute"])
        return f"Audio {action}d."
    if action == "dark_mode":
        import winreg
        want = str(value).lower()
        dark = (not _dark()) if want not in ("on", "off", "true", "false") else want in ("on", "true")
        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0 if dark else 1)
        return f"{'Dark' if dark else 'Light'} mode on."
    if action == "lock_screen":
        ctypes.windll.user32.LockWorkStation()
        return "Screen locked."
    if action == "sleep_display":
        ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)  # broadcast SC_MONITORPOWER off
        return "Display sleeping."
    if action == "notify":
        text = str(value or "").replace("'", "''")[:240]
        plat.powershell(
            "Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
            f"$n.ShowBalloonTip(6000, 'JARVIS', '{text}', 'Info'); Start-Sleep -Seconds 7; $n.Dispose()", timeout=15)
        return "Notification shown."
    if action == "wifi":
        on = str(value).lower() in ("on", "true", "1")
        try:
            plat.run(["netsh", "interface", "set", "interface", "name=Wi-Fi", f"admin={'enabled' if on else 'disabled'}"])
        except RuntimeError as e:
            raise RuntimeError(f"Windows refused ({e}). Turning Wi-Fi on or off needs JARVIS to run as administrator; "
                               "use the network icon in the taskbar instead.")
        return f"Wi-Fi turned {'on' if on else 'off'}."
    raise ValueError(f"Unknown system action {action!r}")
