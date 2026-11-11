"""Cross-platform smoke test: every module imports, the core starts, serves the HUD and says hello.

Runs on macOS, Windows and Linux (CI runs it on all three). Needs no API key, microphone,
speaker or display: the brain is left offline and the mic switched off.

    python tests/smoke.py
"""
from __future__ import annotations

import asyncio
import importlib
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
