"""Tool definitions and local executors for the brain."""
import asyncio
import json
import os
import re
from pathlib import Path

import aiohttp

from . import computer, deploy, devices, imagegen, memory, plat, sysinfo, system

# --- Tool declarations ---------------------------------------------------------

SERVER_TOOLS = [
    {"type": "computer_toolset_20260801"},          # full mouse / keyboard / screen control
    {"type": "bash_20250124", "name": "bash"},     # shell on this computer (zsh, bash or PowerShell)
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]


def _tool(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    }


M = plat.MACHINE  # "Mac", "Windows PC" or "Linux computer"

CUSTOM_TOOLS = [
    _tool(
        "applescript",
        "Run AppleScript via osascript to control macOS apps and settings directly (Music, Safari, "
        "Finder, Mail, Calendar, Messages, System Events, volume, notifications, window management, "
        "Do Not Disturb, opening apps/URLs). Faster and more reliable than clicking for anything an "
        "app exposes to scripting. Returns the script's output.",
        {"script": {"type": "string", "description": "AppleScript source"}},
        ["script"],
    ),
    _tool(
        "look_through_camera",
        "Capture a live frame from the user's webcam (through the JARVIS interface) and return it as "
        "an image so you can see the user, their surroundings, objects they hold up, or gestures.",
        {"reason": {"type": "string", "description": "Short note shown on the HUD while scanning"}},
    ),
    _tool(
        "hud",
        "Control the holographic JARVIS interface. Actions: 'panel' shows a floating panel (title + "
        "markdown-ish body, keep it tight: bullet facts, numbers, short lines); 'alert' flashes a "
        "banner; 'mode' switches the scene (idle, scan, alert, party, briefing, globe, diagnostics); "
        "'locate' spins the holo-globe to a lat/lon with a label; 'clear' removes all panels. Use it "
        "to show what you say aloud: lists, figures, code, links, results.",
        {
            "action": {"type": "string", "enum": ["panel", "alert", "mode", "locate", "clear"]},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "level": {"type": "string", "enum": ["info", "warning", "critical", "success"]},
            "mode": {"type": "string", "enum": ["idle", "scan", "alert", "party", "briefing", "globe", "diagnostics"]},
            "lat": {"type": "number"},
            "lon": {"type": "number"},
            "label": {"type": "string"},
        },
        ["action"],
    ),
    _tool(
        "system_diagnostics",
        f"Full diagnostics of this {M}: CPU (per core), memory, disk, battery, network throughput, "
        "uptime, load and the top processes by CPU and memory. Also animates the HUD diagnostics view.",
        {},
    ),
    _tool(
        "weather",
        "Current weather and today's forecast. Omit location to use the user's current location.",
        {"location": {"type": "string", "description": "City or place name (optional)"}},
    ),
    _tool(
        "remember",
        "Save a fact about the user or their preferences to long-term memory (persists across sessions).",
        {"fact": {"type": "string"}},
        ["fact"],
    ),
    _tool(
        "forget",
        "Delete memories whose text contains the given phrase.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "set_timer",
        "Set a timer or reminder. JARVIS announces the message aloud when it fires.",
        {"seconds": {"type": "number"}, "message": {"type": "string"}},
        ["seconds", "message"],
    ),
    _tool(
        "activate_protocol",
        "Activate one of the user's named protocols (see the system prompt). Returns the protocol's "
        "steps, which you then carry out with your other tools, and triggers its HUD sequence.",
        {"name": {"type": "string"}},
        ["name"],
    ),
]

CUSTOM_TOOLS += [
    _tool(
        "media",
        ("Control music playback in Music or Spotify (whichever is open; Music by default). Actions: "
         "now_playing, play, pause, toggle, next, previous, play_song (search the Music library by "
         "song, artist or album and play the first match)." if plat.MAC else
         "Control whatever media player is playing (media keys / MPRIS). Actions: now_playing, play, pause, "
         "toggle, next, previous, play_song (opens a Spotify or YouTube Music search for the query)."),
        {
            "action": {"type": "string", "enum": ["now_playing", "play", "pause", "toggle", "next", "previous", "play_song"]},
            "query": {"type": "string", "description": "For play_song"},
            "player": {"type": "string", "enum": ["Music", "Spotify"]},
        },
        ["action"],
    ),
    _tool(
        "system_control",
        f"Quick {M} controls: status (volume/mute/appearance), set_volume (value 0-100), volume_up / "
        "volume_down (value = step, default 10), mute, unmute, dark_mode (value on/off/toggle), "
        "lock_screen, sleep_display, notify (value = text for a desktop notification), wifi (value on/off).",
        {
            "action": {"type": "string", "enum": ["status", "set_volume", "volume_up", "volume_down", "mute", "unmute",
                                                   "dark_mode", "lock_screen", "sleep_display", "notify", "wifi"]},
            "value": {"type": "string"},
        },
        ["action"],
    ),
    _tool(
        "calendar",
        "The user's Calendar and Reminders. Actions: agenda (events from `start`, default now, for "
        "`days`, default 1), add_event (title, start, optional end/location/notes), reminders (open "
        "reminders), add_reminder (title, optional due), complete_reminder (title, partial match). "
        "Dates are local ISO 8601, e.g. 2026-11-10T15:00. The first use asks the user for permission.",
        {
            "action": {"type": "string", "enum": ["agenda", "add_event", "reminders", "add_reminder", "complete_reminder"]},
            "title": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "due": {"type": "string"},
            "days": {"type": "number"},
            "location": {"type": "string"},
            "notes": {"type": "string"},
        },
        ["action"],
    ),
]

CUSTOM_TOOLS += [
    _tool(
        "compute",
        "Run Python to calculate or verify maths and science: SymPy (already imported with "
        "`from sympy import *`), NumPy as np, SciPy, mpmath, math, fractions.Fraction, statistics, "
        "itertools. Use print() for every result you need. Exact symbolic work: solve, simplify, "
        "factor, expand, diff, integrate, limit, summation, Matrix, nsimplify, primefactors... "
        "30-second limit; no files or network needed.",
        {"code": {"type": "string", "description": "Python source; print what you want back"}},
        ["code"],
    ),
    _tool(
        "set_mode",
        "Switch JARVIS's mode when the user asks: focus 'standard', 'math' or 'science'; power "
        "'normal' or 'full' (full power = maximum reasoning effort). The user can also say "
        "\"math mode\", \"science mode\", \"full power\" or \"power down\" directly.",
        {"kind": {"type": "string", "enum": ["focus", "power"]},
         "value": {"type": "string", "enum": ["standard", "math", "science", "normal", "full"]}},
        ["kind", "value"],
    ),
]

CUSTOM_TOOLS += [
    _tool(
        "generate_image",
        "Create a picture from a text description (Cloudflare Workers AI) and show it on the HUD. "
        "Write a vivid, specific prompt: subject, setting, style, lighting, colours, composition. "
        "Images are saved to ~/Pictures/JARVIS; the result gives the file paths. Use it whenever the "
        "user asks to generate, draw, create, imagine or design an image, picture, logo, wallpaper or art.",
        {"prompt": {"type": "string", "description": "Detailed description of the image"},
         "shape": {"type": "string", "enum": ["square", "wide", "tall"], "description": "Default square"},
         "count": {"type": "integer", "description": "How many variations, 1 to 4 (default 1)"}},
        ["prompt"],
    ),
]

CUSTOM_TOOLS += [
    _tool(
        "wired_devices",
        f"Devices plugged into this {M} by cable. Actions: list (everything connected: USB devices, Android "
        "phones, iPhones, drives, displays, audio devices, serial boards, printers, cameras, with notes on "
        "how to control each); eject (name = a drive, safe to unplug afterwards); audio_devices (all sound "
        "devices); audio_output / audio_input (name = device to play sound through / use as microphone, e.g. "
        "the monitor's speakers or a USB headset); serial_send (text to an Arduino, ESP32 or other serial "
        "board; returns its reply) and serial_read (just listen). Serial options: port (needed when more than "
        "one is connected), baud (default 9600), seconds to wait for a reply (default 1.5), newline (default "
        "true). Files on drives are handled with the shell tool. Call list first when the user mentions "
        "something they plugged in.",
        {"action": {"type": "string", "enum": ["list", "eject", "audio_devices", "audio_output", "audio_input",
                                                "serial_send", "serial_read"]},
         "name": {"type": "string"}, "port": {"type": "string"}, "text": {"type": "string"},
         "baud": {"type": "integer"}, "seconds": {"type": "number"}, "newline": {"type": "boolean"}},
        ["action"],
    ),
    _tool(
        "android_device",
        "Control an Android phone or tablet connected by USB cable (USB debugging on). Work like the computer "
        "tool: take a screenshot, then act on what you see, then screenshot again to check. Actions: devices; "
        "info (model, battery, screen state); screenshot; tap (x, y); long_press (x, y); swipe (x, y to x2, y2; "
        "swipe up from the lower middle to scroll down); type (text into the focused field, plain English "
        "characters); key (home, back, recents, power, wake, sleep, volume_up, volume_down, mute, enter, "
        "delete, tab, play_pause, next, previous, notifications, or any Android KEYCODE name); open_app (app = "
        "name or package); apps (installed packages); open_url (url); shell (command run on the phone); push "
        "(local file to remote, default /sdcard/Download/); pull (remote file to local, default ~/Downloads). "
        "Coordinates are pixels in the latest screenshot. If the phone is locked, ask the user to unlock it: "
        "never enter a PIN, password or payment details, and ask before sending a message, calling, buying or "
        "deleting anything.",
        {"action": {"type": "string", "enum": ["devices", "info", "screenshot", "tap", "long_press", "swipe", "type",
                                                "key", "open_app", "apps", "open_url", "shell", "push", "pull"]},
         "x": {"type": "number"}, "y": {"type": "number"}, "x2": {"type": "number"}, "y2": {"type": "number"},
         "duration_ms": {"type": "integer"}, "text": {"type": "string"}, "key": {"type": "string"},
         "app": {"type": "string"}, "url": {"type": "string"}, "command": {"type": "string"},
         "local": {"type": "string"}, "remote": {"type": "string"},
         "serial": {"type": "string", "description": "Which device, when several are connected"}},
        ["action"],
    ),
]

CUSTOM_TOOLS += [
    _tool(
        "deploy_jarvis",
        f"Copy JARVIS itself onto a device plugged into this {M}, so it can be carried elsewhere. target "
        "'drive' copies the project folder onto a USB drive or SD card, ready to run on another computer "
        "(macOS, Windows or Linux); target "
        "'android' puts a zip of it in the phone's Download folder over USB. Only the published source is "
        "copied — never API keys, memory, paired devices or downloaded models. Give 'name' to choose which "
        "drive, or 'serial' for which phone, when more than one is connected. Omit target to list where it "
        "can go. Phones run JARVIS as the Pocket web app instead, so the zip is for carrying it.",
        {"target": {"type": "string", "enum": ["drive", "android"]},
         "name": {"type": "string", "description": "Which drive"},
         "serial": {"type": "string", "description": "Which Android device, if several"}},
    ),
]

# Only what this OS can do: AppleScript and Calendar/Reminders are macOS features.
CUSTOM_TOOLS = [t for t in CUSTOM_TOOLS
                if not (t["name"] == "applescript" and not plat.MAC)
                and not (t["name"] == "calendar" and not system.HAS_CALENDAR)]

COMPUTE_PRELUDE = (
    "from sympy import *\nimport numpy as np, scipy, mpmath, math, statistics, itertools\n"
    "from fractions import Fraction\n"
)

ALL_TOOLS = SERVER_TOOLS + CUSTOM_TOOLS

REQUIRED_TYPES = {
    "applescript": {"script": str},
    "remember": {"fact": str},
    "forget": {"query": str},
    "set_timer": {"seconds": (int, float), "message": str},
    "activate_protocol": {"name": str},
    "hud": {"action": str},
    "media": {"action": str},
    "system_control": {"action": str},
    "calendar": {"action": str},
    "compute": {"code": str},
    "set_mode": {"kind": str, "value": str},
    "generate_image": {"prompt": str},
    "wired_devices": {"action": str},
    "android_device": {"action": str},
}

# --- Safety gate -----------------------------------------------------------------

DANGEROUS_SHELL = [
    r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)", r"\bsudo\b", r"\bshutdown\b", r"\breboot\b", r"\bhalt\b",
    r"\bmkfs", r"\bdiskutil\s+(erase|partition|zero|secureErase|apfs\s+delete)", r"\bdd\s+if=",
    r":\(\)\s*\{", r"\bchmod\s+-R\b", r"\bchown\s+-R\b", r"\bkillall\b", r"\bpkill\b",
    r"\blaunchctl\s+(unload|remove|bootout)", r"\bcsrutil\b", r"\bdefaults\s+delete\b",
    r"git\s+push\s+.*--force", r"git\s+reset\s+--hard", r"git\s+clean\s+-[a-z]*f",
    r">\s*/dev/(disk|rdisk)", r"(curl|wget)[^|]*\|\s*(ba|z)?sh", r"\bsecurity\s+(delete|dump)",
    r"\btrash\b.*-e", r"\bsrm\b", r"\bnvram\s+(-[dc]\b|\S+=)", r"\bpmset\s+(?!-g\b)",
    # Windows (PowerShell / cmd)
    r"\bRemove-Item\b.*-(Recurse|Force)", r"\b(rd|rmdir)\s+/s\b", r"\bdel\s+/[sfq]", r"\bformat(-volume)?\s+[a-z]:",
    r"\b(Stop|Restart)-Computer\b", r"\bshutdown(\.exe)?\s+/[sr]", r"\bdiskpart\b", r"\bbcdedit\b",
    r"\breg\s+delete\b", r"\bRemove-ItemProperty\b", r"\bClear-(Disk|RecycleBin)\b", r"\bStop-Process\b.*-Force",
    r"\btaskkill\b.*/f", r"\bSet-ExecutionPolicy\b", r"\bInvoke-Expression\b|\biex\b", r"\bcipher\s+/w",
]
DANGEROUS_APPLESCRIPT = [
    r"\bshut\s*down\b", r"\brestart\b", r"\blog\s*out\b", r"\bempty\s+(the\s+)?trash\b",
    r"\bdelete\b", r"\bsend\b", r"do shell script.*(rm\s+-|sudo|shutdown)",
]


DANGEROUS_PYTHON = [
    r"\bos\.(remove|unlink|rmdir|removedirs|system|popen|kill|exec\w*|spawn\w*)\b", r"\bshutil\.(rmtree|move)\b",
    r"\bsubprocess\b", r"\bopen\([^)]*['\"][wax]\+?b?['\"]", r"\.unlink\(", r"\.rmdir\(", r"\.write_(text|bytes)\(",
    r"\b(socket|requests|urllib|http\.client)\b", r"\bctypes\b", r"\b__import__\b",
]


def needs_confirmation(kind: str, text: str) -> bool:
    patterns = {"bash": DANGEROUS_SHELL, "python": DANGEROUS_PYTHON,
                "adb": devices.DANGEROUS_ADB + DANGEROUS_SHELL}.get(kind, DANGEROUS_APPLESCRIPT)
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# --- Executors ----------------------------------------------------------------------

class Shell:
    """Runs shell commands, remembering the working directory between calls."""

    MARK = "__JARVIS_CWD__"

    def __init__(self):
        self.cwd = str(Path.home())

    async def run(self, command: str, timeout: float = 180) -> str:
        argv, kind = plat.shell()
        if kind == "PowerShell":
            wrapped = (f"{command}\n$__rc = if ($?) {{ if ($LASTEXITCODE) {{ $LASTEXITCODE }} else {{ 0 }} }} else {{ 1 }}; "
                       f"''; '{self.MARK}' + (Get-Location).Path; exit $__rc")
        else:
            wrapped = f"{command}\n__rc=$?; echo; echo {self.MARK}$(pwd); exit $__rc"
        proc = await asyncio.create_subprocess_exec(
            *argv, wrapped, cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "TERM": "dumb", "PAGER": "cat", "GIT_PAGER": "cat"},
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {timeout:.0f}s"
        text = out.decode(errors="replace")
        if self.MARK in text:
            text, _, cwd = text.rpartition(self.MARK)
            cwd = cwd.strip()
            if cwd and os.path.isdir(cwd):
                self.cwd = cwd
        text = text.rstrip()
        if len(text) > 20000:
            text = text[:8000] + f"\n... [{len(text) - 16000} chars truncated] ...\n" + text[-8000:]
        return (text or "(no output)") + f"\n[exit code {proc.returncode}, cwd {self.cwd}]"


async def fetch_json(url: str, params: dict | None = None) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(url, params=params) as r:
            return await r.json(content_type=None)


async def locate_me() -> dict:
    data = await fetch_json("https://ipinfo.io/json")
    lat, lon = (float(x) for x in data["loc"].split(","))
    return {"lat": lat, "lon": lon, "name": f"{data.get('city', '')}, {data.get('country', '')}".strip(", ")}


WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "heavy showers",
    82: "violent showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}


async def weather(location: str | None = None) -> dict:
    if location:
        geo = await fetch_json("https://geocoding-api.open-meteo.com/v1/search", {"name": location, "count": 1})
        if not geo.get("results"):
            raise ValueError(f"Couldn't find a place called {location!r}")
        g = geo["results"][0]
        place = {"lat": g["latitude"], "lon": g["longitude"], "name": f"{g['name']}, {g.get('country', '')}"}
    else:
        place = await locate_me()
    w = await fetch_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": place["lat"], "longitude": place["lon"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "timezone": "auto", "forecast_days": 1,
    })
    cur, day = w["current"], w["daily"]
    return {
        "place": place["name"], "lat": place["lat"], "lon": place["lon"],
        "condition": WEATHER_CODES.get(cur["weather_code"], "unknown"),
        "temp_c": cur["temperature_2m"], "feels_like_c": cur["apparent_temperature"],
        "humidity": cur["relative_humidity_2m"], "wind_kmh": cur["wind_speed_10m"],
        "high_c": day["temperature_2m_max"][0], "low_c": day["temperature_2m_min"][0],
        "rain_chance": day["precipitation_probability_max"][0],
        "sunrise": day["sunrise"][0][-5:], "sunset": day["sunset"][0][-5:],
    }


class ToolRunner:
    """Executes tool_use blocks. `hub` provides UI broadcast, camera frames and confirmations."""

    def __init__(self, hub):
        self.hub = hub
        self.shell = Shell()

    async def confirm(self, kind: str, detail: str) -> bool:
        from . import config
        if not config.CONFIRM_DANGEROUS or not needs_confirmation(kind, detail):
            return True
        return await self.hub.confirm(kind, detail)

    async def run(self, block) -> dict:
        """Returns a tool_result content block for one tool_use block."""
        name = block.name
        inp = block.input if isinstance(block.input, dict) else {}
        toolset = getattr(block, "toolset_name", None)
        result = {"type": "tool_result", "tool_use_id": block.id}
        if toolset:
            result["toolset_name"] = toolset
        try:
            if toolset == "computer":
                await self.hub.event("action", {"tool": "computer", "name": name, "input": _short(inp)})
                content = await asyncio.to_thread(computer.run_action, name, inp)
            else:
                self._validate(name, inp)
                await self.hub.event("action", {"tool": name, "input": _short(inp)})
                content = await self._custom(name, inp)
            result["content"] = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
        except Exception as e:
            result["content"] = [{"type": "text", "text": f"Error: {type(e).__name__}: {e}"}]
            result["is_error"] = True
        return result

    def _validate(self, name: str, inp: dict):
        for key, typ in REQUIRED_TYPES.get(name, {}).items():
            if not isinstance(inp.get(key), typ):
                raise ValueError(f"INVALID_JSON: missing or malformed '{key}' in {json.dumps(inp)[:500]}")

    async def _custom(self, name: str, inp: dict):
        hub = self.hub
        if name == "bash":
            if inp.get("restart"):
                self.shell = Shell()
                return "Shell restarted."
            cmd = inp.get("command")
            if not isinstance(cmd, str):
                raise ValueError("INVALID_JSON: bash needs a 'command' string")
            if not await self.confirm("bash", cmd):
                return "The user declined to run this command."
            return await self.shell.run(cmd)

        if name == "applescript":
            script = inp["script"]
            if not await self.confirm("applescript", script):
                return "The user declined to run this AppleScript."
            return await asyncio.to_thread(computer.run_osascript, script)

        if name == "look_through_camera":
            await hub.event("hud", {"action": "mode", "mode": "scan", "label": inp.get("reason", "Visual scan")})
            data, media = await hub.request_frame()
            return [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                {"type": "text", "text": "Live webcam frame (mirrored like a selfie view)."},
            ]

        if name == "hud":
            await hub.event("hud", inp)
            return "Displayed."

        if name == "system_diagnostics":
            await hub.event("hud", {"action": "mode", "mode": "diagnostics"})
            return json.dumps(await asyncio.to_thread(sysinfo.diagnostics))

        if name == "weather":
            w = await weather(inp.get("location"))
            await hub.event("weather", w)
            return json.dumps(w)

        if name == "remember":
            n = memory.add(inp["fact"])
            return f"Saved. {n} memories on file."

        if name == "forget":
            n = memory.forget(inp["query"])
            return f"Removed {n} memories."

        if name == "set_timer":
            secs = max(1.0, float(inp["seconds"]))
            hub.add_timer(secs, inp["message"])
            return f"Timer set for {secs:.0f} seconds."

        if name == "activate_protocol":
            protocols = memory.load_protocols()
            key = inp["name"].lower().strip()
            match = protocols.get(key) or next((v for k, v in protocols.items() if key in k or k in key), None)
            if not match:
                return f"No protocol named {inp['name']!r}. Known: {', '.join(protocols)}"
            await hub.event("protocol", {"name": key.upper(), "mode": match.get("hud_mode", "alert")})
            return f"Protocol engaged. Carry out these steps now: {match['instructions']}"

        if name == "compute":
            code = inp["code"]
            if not await self.confirm("python", code):
                return "The user declined to run this code."
            return await run_python(code)

        if name == "set_mode":
            return await hub.set_mode(inp["kind"], inp["value"], announce=True)

        if name == "wired_devices":
            return await devices.wired(inp)

        if name == "deploy_jarvis":
            await hub.event("progress", {"text": "Packaging JARVIS…"})
            return await deploy.run(inp)

        if name == "android_device":
            if inp["action"] == "shell" and not await self.confirm("adb", str(inp.get("command") or "")):
                return "The user declined to run this on the phone."
            out = await devices.android(inp)
            if isinstance(out, list):  # a screenshot: show the user what JARVIS sees
                await hub.event("hud", {"action": "device_screen", "title": "PHONE",
                                        "image": "data:image/jpeg;base64," + out[0]["source"]["data"]})
            return out

        if name == "generate_image":
            await hub.event("progress", {"text": "Generating image…"})
            paths = await imagegen.generate(inp["prompt"], inp.get("shape") or "square", inp.get("count") or 1)
            await hub.event("hud", {"action": "image", "title": inp["prompt"][:60],
                                    "images": [f"/generated/{p.name}" for p in paths], "caption": inp["prompt"]})
            return (f"Generated {len(paths)} image{'s' if len(paths) > 1 else ''}, now showing on the HUD. Saved to: "
                    + ", ".join(str(p) for p in paths))

        if name == "media":
            out = await asyncio.to_thread(system.media, inp["action"], inp.get("query"), inp.get("player"))
            asyncio.ensure_future(hub.refresh_media())
            return out

        if name == "system_control":
            return await asyncio.to_thread(system.system_control, inp["action"], inp.get("value"))

        if name == "calendar":
            out = await asyncio.to_thread(system.calendar_tool, inp)
            if inp["action"] != "agenda":
                asyncio.ensure_future(hub.refresh_agenda())
            return out

        raise ValueError(f"Unknown tool {name}")


async def run_python(code: str, timeout: float = 30) -> str:
    """Run a computation in a separate Python process (this virtualenv, so SymPy etc. are available)."""
    import sys
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", COMPUTE_PRELUDE + code, cwd=tmp,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Timed out after {timeout:.0f}s; try a smaller or numeric approach."
    text = out.decode(errors="replace").strip()
    if len(text) > 8000:
        text = text[:4000] + "\n…\n" + text[-3500:]
    return text or "(no output; use print())"


def _short(inp: dict) -> dict:
    return {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in inp.items()}
