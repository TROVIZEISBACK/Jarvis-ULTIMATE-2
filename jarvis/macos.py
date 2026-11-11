"""macOS integrations: media playback, system controls, Calendar and Reminders.

Media and system controls use AppleScript (macOS asks once per app for Automation
permission). Calendar and Reminders use EventKit directly, which is fast and needs
the Calendars / Reminders permission, requested only when you first ask for them.
"""
import datetime
import os
import subprocess
import threading

import psutil

from . import sysinfo
from .computer import run_osascript

# --------------------------------------------------------------------------- media

PLAYERS = {
    # name: (process name, AppleScript application name, duration unit divisor)
    "Music": ("Music", "Music", 1),
    "Spotify": ("Spotify", "Spotify", 1000),  # Spotify reports track duration in ms
}


def _running_players() -> list[str]:
    # Reuse the process list the System Core card already gathered (walking every process costs ~45 ms).
    names = sysinfo.recent_process_names(max_age=8) or {p.info["name"] for p in psutil.process_iter(["name"])}
    return [k for k, (proc, _, _) in PLAYERS.items() if proc in names]


def _osa(script: str, timeout: float = 6) -> str:
    try:
        return run_osascript(script, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("the app didn't respond in time")


def now_playing() -> dict | None:
    """What's playing, without launching any player that isn't already open."""
    best = None
    for player in _running_players():
        _, app, div = PLAYERS[player]
        out = _osa(f'''
            tell application "{app}"
              if player state is stopped then return "stopped"
              set t to current track
              return (player state as text) & "||" & (name of t) & "||" & (artist of t) & "||" & (album of t) & "||" & (player position as text) & "||" & (duration of t as text)
            end tell''')
        if out in ("stopped", "OK") or "||" not in out:
            continue
        state, title, artist, album, pos, dur = (out.split("||") + [""] * 6)[:6]
        info = {
            "player": player, "state": state.strip(), "title": title, "artist": artist, "album": album,
            "position": _num(pos), "duration": round(_num(dur) / div, 1),
        }
        if best is None or info["state"] == "playing":
            best = info
    return best


def _num(s: str) -> float:
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return 0.0


def media(action: str, query: str | None = None, player: str | None = None) -> str:
    running = _running_players()
    current = now_playing()
    target = player or (current or {}).get("player") or (running[0] if running else "Music")
    if target not in PLAYERS:
        raise ValueError(f"Unknown player {target!r}; use Music or Spotify")
    app = PLAYERS[target][1]
    if action == "now_playing":
        if not current:
            return "Nothing is playing."
        return (f"{current['state']} on {current['player']}: {current['title']} by {current['artist']} "
                f"({current['album']}), {current['position']:.0f}s of {current['duration']:.0f}s")
    verbs = {"play": "play", "pause": "pause", "toggle": "playpause", "next": "next track", "previous": "previous track"}
    if action in verbs and action != "play" and target not in running:
        return "Nothing is playing."  # don't launch a player just to pause or skip it
    if action in verbs:
        _osa(f'tell application "{app}" to {verbs[action]}')
        return f"{target}: {action} done."
    if action == "play_song":
        if not query:
            raise ValueError("play_song needs a query")
        q = query.replace('"', "'")
        if target == "Spotify":
            _osa(f'tell application "Spotify" to activate\nopen location "spotify:search:{q}"')
            return "Opened a Spotify search; Spotify can't start a search result by script, so pick it on screen."
        out = _osa(f'''
            tell application "Music"
              set hits to (every track of library playlist 1 whose name contains "{q}" or artist contains "{q}" or album contains "{q}")
              if (count of hits) is 0 then return "none"
              play item 1 of hits
              return (name of item 1 of hits) & " by " & (artist of item 1 of hits)
            end tell''', timeout=15)
        return "No match in the Music library." if out == "none" else f"Playing {out}."
    raise ValueError(f"Unknown media action {action!r}")


# ---------------------------------------------------------------- system controls

def status() -> dict:
    """Volume, mute and appearance, read without needing any permission."""
    out = _osa("set v to get volume settings\nreturn ((output volume of v) as text) & \",\" & ((output muted of v) as text)")
    vol, muted = (out.split(",") + ["false"])[:2]
    dark = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True).stdout.strip() == "Dark"
    return {"volume": int(_num(vol)) if vol.strip() not in ("missing value", "") else None,
            "muted": muted.strip() == "true", "dark": dark}


_VOLUME_SCRIPT = None


def quick_status() -> dict:
    """status() without launching anything: AppleScript compiled once and run in-process, and the
    appearance read straight from preferences. ~0 ms instead of ~230 ms of CPU per call.
    Call it on the main thread only (NSAppleScript is not thread-safe); the event loop runs there."""
    global _VOLUME_SCRIPT
    from CoreFoundation import CFPreferencesAppSynchronize, CFPreferencesCopyAppValue, kCFPreferencesAnyApplication
    from Foundation import NSAppleScript
    if _VOLUME_SCRIPT is None:
        _VOLUME_SCRIPT = NSAppleScript.alloc().initWithSource_(
            'set v to get volume settings\nreturn ((output volume of v) as text) & "," & ((output muted of v) as text)')
    res, err = _VOLUME_SCRIPT.executeAndReturnError_(None)
    if res is None:
        raise RuntimeError(f"volume: {err}")
    vol, muted = (str(res.stringValue()).split(",") + ["false"])[:2]
    CFPreferencesAppSynchronize(kCFPreferencesAnyApplication)
    dark = CFPreferencesCopyAppValue("AppleInterfaceStyle", kCFPreferencesAnyApplication) == "Dark"
    return {"volume": int(_num(vol)) if vol.strip() not in ("missing value", "") else None,
            "muted": muted.strip() == "true", "dark": dark}


def system_control(action: str, value=None) -> str:
    if action == "status":
        s = status()
        return f"Volume {s['volume']}%{' (muted)' if s['muted'] else ''}, {'dark' if s['dark'] else 'light'} mode."
    if action == "set_volume":
        v = max(0, min(100, int(float(value))))
        _osa(f"set volume output volume {v} without output muted")
        return f"Volume set to {v}%."
    if action in ("volume_up", "volume_down"):
        step = int(float(value)) if value else 10
        cur = status()["volume"] or 0
        v = max(0, min(100, cur + (step if action == "volume_up" else -step)))
        _osa(f"set volume output volume {v} without output muted")
        return f"Volume {v}%."
    if action in ("mute", "unmute"):
        _osa(f"set volume {'with' if action == 'mute' else 'without'} output muted")
        return f"Audio {action}d."
    if action == "dark_mode":
        want = {"on": "true", "off": "false"}.get(str(value).lower(), "not dark mode")
        _osa(f'tell application "System Events" to tell appearance preferences to set dark mode to {want}')
        return "Appearance updated."
    if action == "lock_screen":
        import pyautogui
        pyautogui.hotkey("ctrl", "command", "q")
        return "Screen locked."
    if action == "sleep_display":
        subprocess.run(["pmset", "displaysleepnow"], check=True)
        return "Display sleeping."
    if action == "notify":
        text = str(value or "").replace('"', "'")
        _osa(f'display notification "{text}" with title "JARVIS"')
        return "Notification shown."
    if action == "wifi":
        dev = _wifi_device()
        state = "on" if str(value).lower() in ("on", "true", "1") else "off"
        subprocess.run(["networksetup", "-setairportpower", dev, state], check=True, capture_output=True)
        return f"Wi-Fi turned {state}."
    raise ValueError(f"Unknown system action {action!r}")


def _wifi_device() -> str:
    out = subprocess.run(["networksetup", "-listallhardwareports"], capture_output=True, text=True).stdout
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if "Wi-Fi" in line and i + 1 < len(lines) and lines[i + 1].startswith("Device:"):
            return lines[i + 1].split(":", 1)[1].strip()
    return "en0"


# ------------------------------------------------------------ calendar & reminders

class Organizer:
    """Calendar and Reminders through EventKit."""

    EVENTS, REMINDERS = 0, 1           # EKEntityType
    AUTHORIZED = (3,)                  # EKAuthorizationStatusFullAccess (== legacy Authorized)

    def __init__(self):
        import EventKit
        self.ek = EventKit
        self.store = EventKit.EKEventStore.alloc().init()
        self.lock = threading.Lock()

    def authorized(self, kind: int) -> bool:
        return self.ek.EKEventStore.authorizationStatusForEntityType_(kind) in self.AUTHORIZED

    def _request(self, kind: int) -> bool:
        if self.authorized(kind):
            return True
        done, result = threading.Event(), {}

        def callback(granted, error):
            result["ok"] = bool(granted)
            done.set()
        if kind == self.EVENTS:
            self.store.requestFullAccessToEventsWithCompletion_(callback)
        else:
            self.store.requestFullAccessToRemindersWithCompletion_(callback)
        done.wait(120)  # macOS shows its permission dialog meanwhile
        if result.get("ok"):
            self.store = self.ek.EKEventStore.alloc().init()  # pick up the new access
        return bool(result.get("ok"))

    def _need(self, kind: int):
        if not self._request(kind):
            what = "Calendars" if kind == self.EVENTS else "Reminders"
            raise PermissionError(f"{what} access is off. Turn on JARVIS in System Settings → Privacy & Security → {what}.")

    @staticmethod
    def _nsdate(dt: datetime.datetime):
        from Foundation import NSDate
        return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())

    @staticmethod
    def _py(nsdate) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(nsdate.timeIntervalSince1970())

    def events(self, days: float = 1, start: datetime.datetime | None = None, request: bool = True) -> list[dict]:
        if request:
            self._need(self.EVENTS)
        elif not self.authorized(self.EVENTS):
            return []
        start = start or datetime.datetime.now()
        end = start + datetime.timedelta(days=days)
        with self.lock:
            pred = self.store.predicateForEventsWithStartDate_endDate_calendars_(self._nsdate(start), self._nsdate(end), None)
            found = self.store.eventsMatchingPredicate_(pred) or []
        out = []
        for e in found:
            out.append({
                "title": str(e.title() or "(untitled)"),
                "start": self._py(e.startDate()).isoformat(timespec="minutes"),
                "end": self._py(e.endDate()).isoformat(timespec="minutes"),
                "all_day": bool(e.isAllDay()),
                "location": str(e.location() or ""),
                "calendar": str(e.calendar().title()) if e.calendar() else "",
            })
        return sorted(out, key=lambda x: x["start"])

    def add_event(self, title: str, start: str, end: str | None = None, location: str = "", notes: str = "") -> str:
        self._need(self.EVENTS)
        s = datetime.datetime.fromisoformat(start)
        e = datetime.datetime.fromisoformat(end) if end else s + datetime.timedelta(hours=1)
        with self.lock:
            ev = self.ek.EKEvent.eventWithEventStore_(self.store)
            ev.setTitle_(title)
            ev.setStartDate_(self._nsdate(s))
            ev.setEndDate_(self._nsdate(e))
            if location:
                ev.setLocation_(location)
            if notes:
                ev.setNotes_(notes)
            ev.setCalendar_(self.store.defaultCalendarForNewEvents())
            ok, err = self.store.saveEvent_span_commit_error_(ev, 0, True, None)
        if not ok:
            raise RuntimeError(f"Calendar refused the event: {err}")
        return f"Added '{title}' on {s:%A %d %B at %H:%M}."

    def reminders(self) -> list[dict]:
        self._need(self.REMINDERS)
        done, box = threading.Event(), {}
        with self.lock:
            pred = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, None)

            def got(items):
                box["items"] = list(items or [])
                done.set()
            self.store.fetchRemindersMatchingPredicate_completion_(pred, got)
        done.wait(20)
        out = []
        for r in box.get("items", []):
            due = r.dueDateComponents()
            due_s = ""
            if due is not None and due.date() is not None:
                due_s = self._py(due.date()).isoformat(timespec="minutes")
            out.append({"title": str(r.title() or ""), "due": due_s, "list": str(r.calendar().title()) if r.calendar() else ""})
        return sorted(out, key=lambda x: (x["due"] == "", x["due"]))

    def add_reminder(self, title: str, due: str | None = None, notes: str = "") -> str:
        self._need(self.REMINDERS)
        with self.lock:
            r = self.ek.EKReminder.reminderWithEventStore_(self.store)
            r.setTitle_(title)
            if notes:
                r.setNotes_(notes)
            r.setCalendar_(self.store.defaultCalendarForNewReminders())
            when = ""
            if due:
                from Foundation import NSCalendar
                d = datetime.datetime.fromisoformat(due)
                comps = NSCalendar.currentCalendar().components_fromDate_(0x2000 | 0x4 | 0x8 | 0x10 | 0x20 | 0x40, self._nsdate(d))
                r.setDueDateComponents_(comps)
                r.addAlarm_(self.ek.EKAlarm.alarmWithAbsoluteDate_(self._nsdate(d)))
                when = f" for {d:%A %d %B at %H:%M}"
            ok, err = self.store.saveReminder_commit_error_(r, True, None)
        if not ok:
            raise RuntimeError(f"Reminders refused it: {err}")
        return f"Reminder '{title}' added{when}."

    def complete_reminder(self, title: str) -> str:
        items = self.reminders()
        q = title.lower()
        match = next((i for i in items if q in i["title"].lower()), None)
        if not match:
            return f"No open reminder matches {title!r}."
        done, box = threading.Event(), {}
        with self.lock:
            pred = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, None)
            self.store.fetchRemindersMatchingPredicate_completion_(pred, lambda items: (box.update(items=list(items or [])), done.set()))
        done.wait(20)
        for r in box.get("items", []):
            if str(r.title() or "") == match["title"]:
                with self.lock:
                    r.setCompleted_(True)
                    self.store.saveReminder_commit_error_(r, True, None)
                return f"Marked '{match['title']}' as done."
        return "Couldn't update that reminder."


_organizer = None


def organizer() -> Organizer:
    global _organizer
    if _organizer is None:
        _organizer = Organizer()
    return _organizer


def calendar_tool(inp: dict) -> str:
    import json
    org = organizer()
    action = inp["action"]
    if action == "agenda":
        days = float(inp.get("days") or 1)
        start = datetime.datetime.fromisoformat(inp["start"]) if inp.get("start") else None
        ev = org.events(days, start)
        return json.dumps(ev) if ev else "No events in that window."
    if action == "add_event":
        return org.add_event(inp.get("title") or "Event", inp["start"], inp.get("end"), inp.get("location", ""), inp.get("notes", ""))
    if action == "reminders":
        r = org.reminders()
        return json.dumps(r) if r else "No open reminders."
    if action == "add_reminder":
        return org.add_reminder(inp.get("title") or "Reminder", inp.get("due"), inp.get("notes", ""))
    if action == "complete_reminder":
        return org.complete_reminder(inp.get("title", ""))
    raise ValueError(f"Unknown calendar action {action!r}")
