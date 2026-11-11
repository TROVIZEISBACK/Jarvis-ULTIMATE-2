"""Linux integrations: media (MPRIS via playerctl), volume (PulseAudio/PipeWire via pactl, or ALSA's
amixer), dark mode (GNOME-style gsettings), lock, display sleep, notifications, Wi-Fi (NetworkManager).

Each control uses a common desktop tool and says which package to install when it's missing.
"""
import re
import urllib.parse

from . import plat


def _need(*names: str, package: str) -> str:
    p = plat.which(*names)
    if not p:
        raise RuntimeError(f"This needs `{names[0]}`; install the {package} package (e.g. sudo apt install {package}).")
    return p


# --------------------------------------------------------------------------- media

def now_playing() -> dict | None:
    pc = plat.which("playerctl")
    if not pc:
        return None
    try:
        out = plat.run([pc, "metadata", "--format",
                        "{{playerName}}||{{status}}||{{title}}||{{artist}}||{{album}}||{{position}}||{{mpris:length}}"], timeout=3)
    except Exception:
        return None
    parts = (out.split("||") + [""] * 7)[:7]
    name, state, title, artist, album, pos, length = parts
    if not title or state.lower() == "stopped":
        return None
    us = lambda s: round(int(s) / 1e6, 1) if s.strip().isdigit() else 0.0
    return {"player": name.title(), "state": state.lower(), "title": title, "artist": artist, "album": album,
            "position": us(pos), "duration": us(length)}


def media(action: str, query: str | None = None, player: str | None = None) -> str:
    if action == "now_playing":
        cur = now_playing()
        if not cur:
            return "Nothing is playing." if plat.which("playerctl") else "Install playerctl so I can see what's playing."
        return f"{cur['state']} on {cur['player']}: {cur['title']} by {cur['artist']}"
    verbs = {"play": "play", "pause": "pause", "toggle": "play-pause", "next": "next", "previous": "previous"}
    if action in verbs:
        pc = _need("playerctl", package="playerctl")
        args = [pc] + (["-p", player.lower()] if player else []) + [verbs[action]]
        plat.run(args, timeout=5)
        return f"{action} done."
    if action == "play_song":
        if not query:
            raise ValueError("play_song needs a query")
        if player == "Spotify" or plat.which("spotify"):
            plat.open_target("spotify:search:" + urllib.parse.quote(query))
            return "Opened a Spotify search; pick the track on screen."
        plat.open_target("https://music.youtube.com/search?q=" + urllib.parse.quote(query))
        return "Opened a YouTube Music search for it."
    raise ValueError(f"Unknown media action {action!r}")


# ---------------------------------------------------------------- system controls

def _volume() -> tuple[int | None, bool]:
    if plat.which("pactl"):
        try:
            v = re.search(r"(\d+)%", plat.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], 3))
            m = plat.run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], 3)
            return (int(v.group(1)) if v else None), "yes" in m.lower()
        except Exception:
            pass
    if plat.which("amixer"):
        try:
            out = plat.run(["amixer", "get", "Master"], 3)
            v = re.search(r"\[(\d+)%\]", out)
            return (int(v.group(1)) if v else None), "[off]" in out
        except Exception:
            pass
    return None, False


def _set_volume(v: int):
    if plat.which("pactl"):
        plat.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
        plat.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{v}%"])
    else:
        plat.run([_need("amixer", package="alsa-utils"), "-q", "set", "Master", f"{v}%", "unmute"])


def _dark() -> bool | None:
    if not plat.which("gsettings"):
        return None
    try:
        scheme = plat.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], 3)
        if "dark" in scheme:
            return True
        theme = plat.run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"], 3)
        return "dark" in theme.lower()
    except Exception:
        return None


def status() -> dict:
    vol, muted = _volume()
    return {"volume": vol, "muted": muted, "dark": _dark()}


quick_status = status


def system_control(action: str, value=None) -> str:
    if action == "status":
        s = status()
        vol = f"Volume {s['volume']}%" if s["volume"] is not None else "Volume unknown"
        return f"{vol}{' (muted)' if s['muted'] else ''}, {'dark' if s['dark'] else 'light'} mode."
    if action == "set_volume":
        v = max(0, min(100, int(float(value))))
        _set_volume(v)
        return f"Volume set to {v}%."
    if action in ("volume_up", "volume_down"):
        step = int(float(value)) if value else 10
        cur = _volume()[0] or 0
        v = max(0, min(100, cur + (step if action == "volume_up" else -step)))
        _set_volume(v)
        return f"Volume {v}%."
    if action in ("mute", "unmute"):
        flag = "1" if action == "mute" else "0"
        if plat.which("pactl"):
            plat.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag])
        else:
            plat.run([_need("amixer", package="alsa-utils"), "-q", "set", "Master", "mute" if flag == "1" else "unmute"])
        return f"Audio {action}d."
    if action == "dark_mode":
        _need("gsettings", package="libglib2.0-bin")
        want = str(value).lower()
        dark = (not _dark()) if want not in ("on", "off", "true", "false") else want in ("on", "true")
        plat.run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark" if dark else "default"])
        return f"{'Dark' if dark else 'Light'} mode on."
    if action == "lock_screen":
        for cmd in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"], ["gnome-screensaver-command", "-l"]):
            if plat.which(cmd[0]):
                try:
                    plat.run(cmd)
                    return "Screen locked."
                except Exception:
                    continue
        raise RuntimeError("No screen locker answered (tried loginctl, xdg-screensaver).")
    if action == "sleep_display":
        plat.run([_need("xset", package="x11-xserver-utils"), "dpms", "force", "off"])
        return "Display sleeping."
    if action == "notify":
        plat.run([_need("notify-send", package="libnotify-bin"), "JARVIS", str(value or "")[:500]])
        return "Notification shown."
    if action == "wifi":
        on = str(value).lower() in ("on", "true", "1")
        plat.run([_need("nmcli", package="network-manager"), "radio", "wifi", "on" if on else "off"])
        return f"Wi-Fi turned {'on' if on else 'off'}."
    raise ValueError(f"Unknown system action {action!r}")
