"""Which operating system JARVIS is running on, and the small differences between them.

Everything OS-specific that is only a line or two lives here (names, keys, the shell, the sound
player). Bigger integrations have their own module per system: macos.py, windows.py, linux.py,
picked by system.py.
"""
import os
import shutil
import subprocess
import sys

MAC = sys.platform == "darwin"
WINDOWS = sys.platform.startswith("win")
LINUX = not (MAC or WINDOWS)

NAME = "mac" if MAC else "windows" if WINDOWS else "linux"
# How JARVIS refers to the machine when it talks ("full control of this Mac").
MACHINE = {"mac": "Mac", "windows": "Windows PC", "linux": "Linux computer"}[NAME]

# Push-to-talk keys. On a Mac ⌘H / ⌘S; elsewhere Ctrl+Alt+J starts (and cancels) a recording and
# Ctrl+Alt+S sends it (Ctrl+S alone is "save" in every app, and Windows owns Win+H).
KEYS = ({"listen": "⌘H", "send": "⌘S"} if MAC else {"listen": "Ctrl+Alt+J", "send": "Ctrl+Alt+S"})

# The modifier the computer tool's "super"/"cmd" key means, and the paste shortcut.
SUPER = "command" if MAC else "win"
PASTE = ("command", "v") if MAC else ("ctrl", "v")


def has_display() -> bool:
    """False on a server or phone without a desktop: no screenshots, mouse or keyboard control."""
    if MAC or WINDOWS:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def shell() -> tuple[list[str], str]:
    """(argv prefix, shell name) for the shell tool."""
    if WINDOWS:
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"], "PowerShell"
    if MAC and os.path.exists("/bin/zsh"):
        return ["/bin/zsh", "-lc"], "zsh"
    sh = shutil.which("bash") or "/bin/sh"
    return [sh, "-lc"], os.path.basename(sh)


def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def player_cmd(path: str, volume: float = 1.0) -> list[str] | None:
    """A command that plays a WAV file and exits when it's done (killing it stops the sound).
    Out of process, so playback can't stutter while Python is busy."""
    if MAC:
        return ["afplay", "-v", f"{volume:.2f}", path] if volume != 1.0 else ["afplay", path]
    if WINDOWS:
        # winsound ships with Python on Windows; a child Python keeps the "kill to stop" behaviour.
        return [sys.executable, "-c", "import sys, winsound; winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME)", path]
    for name, args in (("pw-play", []), ("paplay", []), ("aplay", ["-q"]), ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"])):
        p = shutil.which(name)
        if p:
            return [p, *args, path]
    return None


def open_target(target: str) -> None:
    """Open a file, folder or URL with the system's default app."""
    if MAC:
        subprocess.Popen(["open", target])
    elif WINDOWS:
        os.startfile(target)  # noqa: the Windows-only API
    else:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(cmd: list[str], timeout: float = 15) -> str:
    """Run a command, return stdout (stripped); raise RuntimeError with its message on failure."""
    kw = {"creationflags": 0x08000000} if WINDOWS else {}   # CREATE_NO_WINDOW: no console flashes
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300] or f"{cmd[0]} exited {r.returncode}")
    return r.stdout.strip()


def powershell(script: str, timeout: float = 15) -> str:
    exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return run([exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script], timeout)


def description() -> str:
    import platform
    if MAC:
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    if WINDOWS:
        return f"Windows {platform.release()} ({platform.machine()})"
    try:
        info = platform.freedesktop_os_release()
        return f"{info.get('PRETTY_NAME', 'Linux')} ({platform.machine()})"
    except Exception:
        return f"Linux {platform.release()} ({platform.machine()})"
