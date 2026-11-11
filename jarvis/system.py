"""System integrations for whichever OS JARVIS runs on: media, quick controls, calendar.

    system.media(...), system.now_playing(), system.status(), system.quick_status(),
    system.system_control(...), system.organizer(), system.calendar_tool(...)

macOS has the most (AppleScript, EventKit); Windows and Linux use their own equivalents.
Calendar and Reminders exist on macOS only; elsewhere the calendar tool isn't offered.
"""
from . import plat

if plat.MAC:
    from .macos import (calendar_tool, media, now_playing, organizer, quick_status,  # noqa: F401
                        status, system_control)
    HAS_CALENDAR = True
else:
    if plat.WINDOWS:
        from .windows import media, now_playing, quick_status, status, system_control  # noqa: F401
    else:
        from .linux import media, now_playing, quick_status, status, system_control  # noqa: F401
    HAS_CALENDAR = False

    class _NoCalendar:
        EVENTS, REMINDERS = 0, 1

        def authorized(self, kind):
            return False

        def _request(self, kind):
            return False

        def events(self, *a, **k):
            return []

    _none = _NoCalendar()

    def organizer():
        return _none

    def calendar_tool(inp: dict) -> str:
        raise RuntimeError("Calendar and Reminders are only connected on macOS.")
