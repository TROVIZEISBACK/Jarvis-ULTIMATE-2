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
        s._content, s.ask = content, ask

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


def check_memory() -> None:
    """Conversation memory: the log, the prompt, search, and compaction at the threshold (stubbed model)."""
    import tempfile
    import types
    from jarvis import config, history

    tmp = Path(tempfile.mkdtemp())
    history.LOG, history.STATE = tmp / "history.jsonl", tmp / "conversation.json"
    assert history.as_prompt() == "(no earlier conversations yet)"
    assert config.COMPACT_AT == 60, "the default compaction threshold is 60% of the context window"

    async def run() -> None:
        events, resets = [], []

        async def event(kind, payload=None):
            events.append(kind)
            if hub.history.open is not None:
                hub.history.capture(kind, payload or {})

        brain = types.SimpleNamespace(client=True, context_tokens=0, context_window=1_000_000, last_active=0.0,
                                      has_conversation=True)

        def reset(note=""):
            resets.append(note)
            brain.context_tokens = 0
        brain.reset = reset
        hub = types.SimpleNamespace(brain=brain, event=event, state="idle", queue=asyncio.Queue(), current=None)
        mem = hub.history = history.ConversationMemory(hub)
        summaries = []

        async def summarise(text):
            summaries.append(text)
            return "About sir\n- Likes the colour teal.\nTimeline\n- Talked about colours and opened the notes folder."
        mem.summarise = summarise

        async def exchange(user, said, tokens, action=None):
            mem.begin(user, "typed")
            if action:
                await event("action", action)
            await event("assistant_delta", {"text": said})
            await event("assistant_done", {})
            mem.end()
            brain.context_tokens = tokens
            await mem.after_request()

        await exchange("my favourite colour is teal", "Noted, sir.", 150_000)
        await exchange("open my notes folder", "Done, sir.", 599_000, {"tool": "bash", "input": {"command": "open notes"}})
        assert not resets and not summaries, "compacted below 60%"                      # 59.9%: not yet
        assert len(history.load()) == 2 and history.load()[1]["actions"], history.load()
        assert "favourite colour is teal" in history.as_prompt()
        assert "open my notes folder" in history.search("notes FOLDER") and "Nothing" in history.search("zebra")

        await exchange("what's the time", "Ten past nine, sir.", 600_000)               # 60%: compacts
        assert len(resets) == 1 and len(summaries) == 1, (resets, summaries)
        assert all(w in summaries[0] for w in ("teal", "notes folder", "Ten past nine")), "transcript incomplete"
        state = history.load_state()
        assert state["through"] == 3 and state["compactions"] == 1 and "teal" in state["summary"], state
        assert len(history.load()) == 3, "compaction must never shorten the log"
        prompt = history.as_prompt()
        assert "Likes the colour teal" in prompt and "Ten past nine" in prompt           # summary + latest exchanges
        assert mem.describe()["percent"] == 0 and brain.context_tokens == 0

        await exchange("thanks", "Of course, sir.", 20_000)
        assert len(resets) == 1 and "CURRENT SUMMARY" in summaries[0]
        mem.note("sentinel", "Sir, the backup failed.")
        assert history.load()[-1]["source"] == "sentinel" and len(history.load()) == 5

    with contextlib.redirect_stdout(io.StringIO()):
        asyncio.run(run())
    history.LOG, history.STATE = config.DATA_DIR / "history.jsonl", config.DATA_DIR / "conversation.json"
    print("memory ok: every exchange logged, compaction at 60% keeps the log and carries a summary forward")


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
    check_memory()
    port, remote_port = free_port(), free_port()
    import tempfile
    env = dict(os.environ, JARVIS_DATA_DIR=tempfile.mkdtemp(), JARVIS_PORT=str(port), JARVIS_REMOTE_PORT=str(remote_port), JARVIS_MIC="0",
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
