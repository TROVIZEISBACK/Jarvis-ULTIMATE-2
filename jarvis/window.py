"""Open the interface when running from a terminal (run.sh / run.ps1).

On a Mac with JARVIS.app installed, the app shows the interface in its own native window.
Otherwise: a chromeless app window in a Chromium browser (Chrome, Edge, Brave...), else a
normal tab in the default browser.
"""
import os
import shutil
import subprocess
import webbrowser

from . import config, plat

MAC_CHROMIUM = ["Google Chrome", "Vivaldi", "Brave Browser", "Microsoft Edge", "Chromium"]


def _mac_installed(app: str) -> bool:
    return any(os.path.isdir(f"{root}/Applications/{app}.app") for root in ("", os.path.expanduser("~")))


def _windows_browsers() -> list[str]:
    roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
           r"BraveSoftware\Brave-Browser\Application\brave.exe"]
    return [os.path.join(r, p) for p in rel for r in roots if r and os.path.exists(os.path.join(r, p))]


def open_interface():
    url = f"http://{config.HOST}:{config.PORT}"
    app_flags = [f"--app={url}", "--window-size=1440,900"]
    if plat.MAC:
        app_mode = _mac_installed("JARVIS")
        if app_mode and os.environ.get("JARVIS_WINDOW", "app") == "app":
            subprocess.Popen(["open", "-a", "JARVIS"])  # attaches to this running server
            return
        for browser in MAC_CHROMIUM:
            if _mac_installed(browser):
                subprocess.Popen(["open", "-na", browser, "--args", *app_flags])
                return
    elif plat.WINDOWS:
        for exe in _windows_browsers():
            subprocess.Popen([exe, *app_flags])
            return
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                     "brave-browser"):
            exe = shutil.which(name)
            if exe:
                subprocess.Popen([exe, *app_flags], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    webbrowser.open(url)
