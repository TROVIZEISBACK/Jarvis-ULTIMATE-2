"""Cross-platform smoke test: every module imports, the core starts, serves the HUD and says hello.

Runs on macOS, Windows and Linux (CI runs it on all three). Needs no API key, microphone,
speaker or display: the brain is left offline and the mic switched off.

    python tests/smoke.py
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import io
import json
import os
import pkgutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aiohttp  # noqa: E402

# Modules that only exist for one platform.
ONLY_ON = {"macos": "darwin", "hotkey_mac": "darwin", "windows": "win32", "linux": "linux"}


def check_imports() -> None:
    import jarvis
    failed = []
    for mod in pkgutil.iter_modules(jarvis.__path__):
        need = ONLY_ON.get(mod.name)
        if need and not sys.platform.startswith(need):
            continue
        try:
            importlib.import_module(f"jarvis.{mod.name}")
        except Exception as e:  # noqa: BLE001
            failed.append(f"jarvis.{mod.name}: {type(e).__name__}: {e}")
    if failed:
        raise SystemExit("Import failures:\n  " + "\n  ".join(failed))
    print("imports ok")

    from jarvis import plat, tools
    names = {t["name"] for t in tools.CUSTOM_TOOLS}
    assert ("applescript" in names) == plat.MAC, "applescript must be offered only on macOS"
    assert plat.shell()[0], "no shell found"
    print(f"platform: {plat.description()} · shell: {plat.shell()[1]} · keys: {plat.KEYS}")


def check_sentinel() -> None:
    """The sentinel's schedule and rules, with the model call replaced by a stub (no network)."""
    import datetime as dt
    import types
    from jarvis import sentinel

    w = sentinel.parse_window("08:00-12:30")
    assert w == (480, 750), w
    at = lambda h, m, s=0: dt.datetime(2026, 11, 12, h, m, s)  # noqa: E731
    assert sentinel.due_slot(at(7, 59), w, 10) is None and sentinel.due_slot(at(8, 9), w, 10) is None
    assert sentinel.due_slot(at(8, 10), w, 10)[1] == 1 and sentinel.due_slot(at(12, 30, 30), w, 10)[1] == 27
    assert sentinel.due_slot(at(12, 31), w, 10) is None
    assert sentinel.next_check(at(8, 37), w, 25) == at(8, 50)
    assert sentinel.parse_reply("NOTHING") == sentinel.parse_reply("Nothing urgent, sir.") == ""
    assert sentinel.parse_reply("**Sir**, the build failed.") == "Sir, the build failed."
    assert sentinel.is_allowed_model("claude-sonnet-5-5") and not sentinel.is_allowed_model("claude-opus-5-5")
    assert sentinel.MODEL == "claude-sonnet-5-5", "sentinel checks must stay on Sonnet 5.5"

    async def day() -> None:
        said = []

        async def notice(title, spoken, **_):
            said.append(spoken)

        async def event(*_a, **_k):
            pass

        hub = types.SimpleNamespace(
            state="idle", pending_confirm=None, queue=asyncio.Queue(), current=None, listener=None, clients=set(),
            telemetry={}, media=None, agenda=[], brain=types.SimpleNamespace(client=True), notice=notice, event=event)
        s = sentinel.Sentinel(hub)
        s.window, s.every = w, {"text": 10, "screen": 25}
        s.enabled, s.done = True, dict.fromkeys(sentinel.KINDS)
        asked = []

        async def content(kind):
            return [{"type": "text", "text": kind}]

        async def ask(c):
            asked.append(c[0]["text"])
            if len(asked) == 3:
                return "Sir, something needs you.", sentinel.MODEL
            if len(asked) == 5:
                return "Sir, I am another model.", "claude-opus-5-5"  # must be discarded
            return "NOTHING", sentinel.MODEL
        s._content, s._ask = content, ask

        t = at(7, 0)
        while t < at(13, 30):  # a whole morning, one tick every 15 s
            if at(9, 0) <= t < at(9, 3):
                hub.state = "working"      # the user's own request is running: the 09:00 check waits for it
            else:
                hub.state = "idle"
            before = len(asked)
            await s.tick(t)
            assert not (hub.state == "working" and len(asked) > before), "a check ran while JARVIS was busy"
            t += dt.timedelta(seconds=15)
        assert asked.count("text") == 27 and asked.count("screen") == 10, (asked.count("text"), asked.count("screen"))
        assert said == ["Sir, something needs you."], said
        assert s.counts == {"checks": 37, "spoke": 1, "failed": 1}, s.counts
        assert "something needs you" in s.take_context() and s.take_context() == ""

        s.enabled, n = False, len(asked)
        await s.tick(at(8, 10) + dt.timedelta(days=1))
        assert len(asked) == n, "a check ran while the sentinel was off"

    with contextlib.redirect_stdout(io.StringIO()):  # the per-check log lines
        asyncio.run(day())
    print("sentinel ok: 27 status + 10 screen checks a day, Sonnet 5.5 only, silent unless needed")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def check_server(port: int) -> None:
    base = f"http://127.0.0.1:{port}"
    async with aiohttp.ClientSession() as http:
        deadline = time.monotonic() + 90
        while True:
            try:
                async with http.get(f"{base}/health") as r:
                    if r.status == 200 and (await r.json())["ok"]:
                        break
            except aiohttp.ClientError:
                pass
            if time.monotonic() > deadline:
                raise SystemExit("core did not answer /health within 90 s")
            await asyncio.sleep(1)
        print("health ok")

        async with http.get(base + "/") as r:
            html = await r.text()
            assert r.status == 200 and "J.A.R.V.I.S." in html, "HUD page missing"
        print("HUD page ok")

        # A foreign website must not be able to open the socket.
        try:
            async with http.ws_connect(f"{base}/ws", headers={"Origin": "https://example.com"}):
                raise SystemExit("socket accepted a foreign origin")
        except aiohttp.WSServerHandshakeError as e:
            assert e.status == 403, e.status
        print("origin check ok")

        async with http.ws_connect(f"{base}/ws", headers={"Origin": base}) as ws:
            hello = json.loads((await ws.receive(timeout=20)).data)
            assert hello["type"] == "hello", hello
            want = {"darwin": "mac", "win32": "windows"}.get(sys.platform, "linux")
            assert hello["os"] == want, (hello["os"], want)
            print(f"hello ok: os={hello['os']} keys={hello['keys']}")


def main() -> None:
    check_imports()
    check_sentinel()
    port, remote_port = free_port(), free_port()
    env = dict(os.environ, JARVIS_PORT=str(port), JARVIS_REMOTE_PORT=str(remote_port), JARVIS_MIC="0",
               JARVIS_OPEN_BROWSER="0", JARVIS_BRAIN="api", ANTHROPIC_API_KEY="", JARVIS_ALERTS="0",
               PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "-m", "jarvis.main"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        asyncio.run(check_server(port))
    except BaseException:
        proc.kill()
        print("---- core output ----\n" + (proc.communicate(timeout=10)[0] or ""))
        raise
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("smoke test passed")


if __name__ == "__main__":
    main()
