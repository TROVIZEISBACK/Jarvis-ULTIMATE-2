"""JARVIS Ultimate 2 — server entry point.

Runs the local web server for the holographic interface, the always-on
listener, the voice, and the Opus 5.5 brain, and routes events between them.
"""
import asyncio
import collections
import datetime
import html
import itertools
import json
import re
import socket
import time

from aiohttp import WSMsgType, web

from . import config, plat, sysinfo, system
from .modes import FOCUS, POWER, ModeState, parse_switches
from . import mcp_bridge
from .brain import Brain
from .brain_code import CodeBrain
from .tools import ToolRunner, weather
from .remote import COOKIE, RemoteAccess, lan_ip
from . import uploads
from .voice import Listener, Speaker, recognize, strip_wake_word

STOP_RE = re.compile(r"^(stop|cancel|abort|halt|shut up|be quiet|quiet|silence|never ?mind|that's enough|enough)\b", re.I)
YES_RE = re.compile(r"\b(yes|yeah|yep|sure|proceed|go ahead|do it|confirm(ed)?|affirmative|approved?|okay|ok)\b", re.I)
NO_RE = re.compile(r"\b(no|nope|don't|do not|cancel|stop|abort|negative|deny|denied)\b", re.I)


class Hub:
    def __init__(self):
        self.clients: set[web.WebSocketResponse] = set()
        self.hidden: set[web.WebSocketResponse] = set()  # clients whose window is minimised / hidden
        self.loop: asyncio.AbstractEventLoop | None = None
        self.listener: Listener | None = None
        self.speaker: Speaker | None = None
        self.brain: Brain | None = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.tray = uploads.Tray()  # images attached in the HUD, waiting for the next command
        self.current: asyncio.Task | None = None
        self.attention_until = 0.0
        self.pending_confirm: asyncio.Future | None = None
        self.frame_waiters: dict[int, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.mic_on = config.MIC_ENABLED
        self.hotkey = None  # set in main(); .ok once ⌘H is live
        self.state = "idle"
        self.telemetry: dict = {}
        self.media: dict | None = None
        self.agenda: list[dict] = []
        self.agenda_authorized = False
        self.alerted: dict[str, float] = {}
        self.modes = ModeState()
        self.remote = RemoteAccess()
        self.remote_clients: set[web.WebSocketResponse] = set()  # phones, tablets, PCs
        self.voice_target: web.WebSocketResponse | None = None   # remote device that hears JARVIS, else this computer
        self.remote_runner: web.AppRunner | None = None

    # --- outbound -------------------------------------------------------------
    async def event(self, kind: str, payload: dict | None = None):
        msg = json.dumps({"type": kind, **(payload or {})})
        dead = []
        for ws in self.clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def event_local(self, kind: str, payload: dict | None = None):
        """Only to windows on this computer (e.g. pairing codes)."""
        msg = json.dumps({"type": kind, **(payload or {})})
        for ws in [c for c in self.clients if c not in self.remote_clients]:
            try:
                await ws.send_str(msg)
            except Exception:
                self.clients.discard(ws)

    # --- remote devices ----------------------------------------------------------
    def remote_speech_out(self):
        """Speaker hook: where JARVIS's voice goes. The device that last spoke or typed to it
        hears the reply; anything from this computer (⌘H, the window here) speaks on the computer."""
        ws = self.voice_target
        if not ws or ws.closed or ws not in self.clients:
            self.voice_target = None
            return None

        async def send(path) -> bool:
            import base64
            try:
                data = await asyncio.to_thread(lambda: base64.b64encode(open(path, "rb").read()).decode())
                await ws.send_str(json.dumps({"type": "speech_audio", "data": data}))
                return True
            except Exception:
                return False
        return send

    def remote_speech_stop(self):
        ws = self.voice_target
        if ws and not ws.closed:
            asyncio.ensure_future(ws.send_str(json.dumps({"type": "speech_stop"})))

    async def remote_voice(self, data_b64: str):
        """A recording from a remote device's microphone (16 kHz int16, base64)."""
        import base64
        raw = base64.b64decode(data_b64)
        await self.voice_state("recognizing")
        text = await asyncio.to_thread(recognize, raw) if len(raw) >= 16000 * 2 * 0.4 else ""
        if text:
            await self.voice_state("idle")
            await self.on_phrase(text, remote=True)
        else:
            await self.voice_state("not-heard")

    async def remote_action(self, msg: dict):
        r, action = self.remote, msg.get("action")
        try:
            if action == "enable" and not r.enabled:
                r.set_enabled(True)
                await self.start_remote()
            elif action == "disable" and r.enabled:
                r.set_enabled(False)
                await self.stop_remote()
            elif action == "pair" and r.enabled:
                r.new_code()
                asyncio.get_running_loop().call_later(121, lambda: asyncio.ensure_future(
                    self.event_local("remote", self.remote.describe())))
            elif action == "revoke":
                if r.revoke(str(msg.get("id"))):
                    await self.drop_unpaired_clients()
        except Exception as e:
            print(f"[remote] {action}: {e}")
            await self.event_local("progress", {"text": f"Remote access: {e}"})
        await self.event_local("remote", r.describe())

    async def drop_unpaired_clients(self):
        for ws in list(self.remote_clients):
            if not self.remote.device(getattr(ws, "jarvis_token", None)):
                await ws.close()

    async def start_remote(self):
        if self.remote_runner:
            return
        ctx = await asyncio.to_thread(self.remote.ssl_context)
        runner = web.AppRunner(create_remote_app(), handle_signals=False)
        await runner.setup()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", config.REMOTE_PORT))
        sock.listen(64)
        sock.setblocking(False)
        self.remote_runner = runner
        self.remote_sock = sock
        self.remote_accept = asyncio.create_task(self._remote_accept(sock, runner, ctx))
        print(f"[remote] on: {', '.join(self.remote.addresses())}")

    async def _remote_accept(self, sock, runner, ctx):
        """One port for both: HTTPS goes to JARVIS; plain HTTP (someone typed the address
        without https://) gets sent to the HTTPS address instead of a dead connection."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                conn, addr = await loop.sock_accept(sock)
            except (OSError, asyncio.CancelledError):
                return
            asyncio.create_task(self._remote_conn(loop, conn, addr, runner, ctx))

    async def _remote_conn(self, loop, conn, addr, runner, ctx):
        conn.setblocking(False)
        seen = self.__dict__.setdefault("_remote_seen", {})
        if time.time() - seen.get(addr[0], 0) > 60:  # one line per device per minute
            seen[addr[0]] = time.time()
            print(f"[remote] connection from {addr[0]}")
        try:
            ready = loop.create_future()
            loop.add_reader(conn.fileno(), lambda: ready.done() or ready.set_result(None))
            try:
                await asyncio.wait_for(ready, 15)
            finally:
                loop.remove_reader(conn.fileno())
            first = conn.recv(1, socket.MSG_PEEK)
            if first == b"\x16":  # TLS handshake
                await loop.connect_accepted_socket(runner.server, conn, ssl=ctx, ssl_handshake_timeout=15)
                return
            if not first:
                conn.close()
                return
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 8192:
                chunk = await asyncio.wait_for(loop.sock_recv(conn, 4096), 5)
                if not chunk:
                    break
                head += chunk
            m = re.search(rb"^Host:\s*([^\s]+)", head, re.I | re.M)
            host = m.group(1).decode("latin-1") if m else f"{lan_ip()}:{config.REMOTE_PORT}"
            path = (head.split(b" ")[1].decode("latin-1") if head.count(b" ") >= 2 else "/")
            if not path.startswith("/"):
                path = "/"
            if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", host):
                host = f"{lan_ip()}:{config.REMOTE_PORT}"
            url = f"https://{host}{path}"
            body = f'<a href="{html.escape(url)}">{html.escape(url)}</a>\n'.encode()
            await loop.sock_sendall(conn, (
                f"HTTP/1.1 301 Moved Permanently\r\nLocation: {url}\r\nContent-Type: text/html\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
            print(f"[remote] {addr[0]} used http://, sent to https://")
            conn.close()
        except Exception as e:
            print(f"[remote] connection from {addr[0]} failed: {type(e).__name__}: {e}")
            conn.close()

    async def stop_remote(self):
        for ws in list(self.remote_clients):
            await ws.close()
        if self.remote_runner:
            self.remote_accept.cancel()
            self.remote_sock.close()
            await self.remote_runner.cleanup()
            self.remote_runner = None
            print("[remote] off")

    async def say_and_show(self, text: str):
        await self.event("assistant_delta", {"text": text})
        await self.event("assistant_done", {})
        self.speaker.say(text)

    @property
    def push_to_talk(self) -> bool:
        return self.listener is not None and self.listener.push

    async def voice_state(self, state: str):
        if state in ("listening", "hearing", "recognizing", "idle", "not-heard") or state.startswith("mic-error"):
            self.on_listen_state(state)
        if state == "not-heard":
            state = "idle"
            await self.event("progress", {"text": "Didn't catch that. Try again."})
        if state == "idle" and self.state == "speaking" and not self.push_to_talk:
            self.attention_until = time.time() + config.FOLLOW_UP_SECONDS
        if state in ("speaking", "idle") or self.state not in ("thinking", "working"):
            self.state = state
        attentive = self.mic_on and self.listener is not None and time.time() < self.attention_until
        await self.event("voice", {"state": state, "attentive": attentive, "window": config.FOLLOW_UP_SECONDS})

    async def voice_level(self, level: float):
        await self.event("level", {"level": round(level, 3)})

    async def mic_level(self, level: float):
        await self.event("mic", {"level": round(level, 3)})

    # --- camera & confirmation -----------------------------------------------
    async def request_frame(self, timeout: float = 8.0) -> tuple[str, str]:
        if not self.clients:
            raise RuntimeError("The JARVIS interface isn't open, so the camera is unavailable.")
        fid = next(self.ids)
        fut = self.loop.create_future()
        self.frame_waiters[fid] = fut
        await self.event("camera_request", {"id": fid})
        try:
            data, media = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise RuntimeError("The camera didn't respond. Make sure camera access is allowed in the interface.")
        finally:
            self.frame_waiters.pop(fid, None)
        return data, media

    async def confirm(self, kind: str, detail: str, timeout: float = 30.0) -> bool:
        self.pending_confirm = self.loop.create_future()
        summary = detail if len(detail) < 160 else detail[:157] + "..."
        await self.event("confirm", {"kind": kind, "detail": detail})
        what = {"bash": "command", "adb": "phone command", "python": "code"}.get(kind, "script")
        self.speaker.say(f"That {what} could be destructive, {config.OWNER}. Shall I proceed?")
        await self.event("assistant_delta", {"text": f"⚠ Authorisation required — {kind}: {summary}"})
        await self.event("assistant_done", {})
        try:
            ok = await asyncio.wait_for(self.pending_confirm, timeout)
        except asyncio.TimeoutError:
            ok = False
        finally:
            self.pending_confirm = None
        await self.event("confirm_done", {"approved": ok})
        self.speaker.say("Proceeding." if ok else "Understood, standing down.")
        return ok

    def _resolve_confirm(self, value: bool):
        if self.pending_confirm and not self.pending_confirm.done():
            self.pending_confirm.set_result(value)

    def add_timer(self, seconds: float, message: str):
        async def fire():
            await asyncio.sleep(seconds)
            await self.event("hud", {"action": "alert", "title": "TIMER", "body": message, "level": "warning"})
            self.speaker.say(f"Pardon the interruption, {config.OWNER}. {message}")
        asyncio.create_task(fire())
        due = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
        asyncio.create_task(self.event("timer", {"message": message, "due": due.strftime("%H:%M:%S"), "at": due.timestamp() * 1000}))

    # --- inbound ---------------------------------------------------------------
    async def on_phrase(self, text: str, remote: bool = False):
        await self.event("heard", {"text": text})
        if self.pending_confirm:
            if NO_RE.search(text):
                self._resolve_confirm(False)
            elif YES_RE.search(text):
                self._resolve_confirm(True)
            return

        if self.push_to_talk or remote:  # ⌘H / the mic button was pressed, so everything said is a command
            woke, rest = strip_wake_word(text)
            command = (rest if woke else text).strip()
            if STOP_RE.match(command):
                await self.interrupt()
                self.speaker.say("Of course.")
            elif command:
                await self.submit(command, "voice")
            else:
                self.speaker.say(f"Yes, {config.OWNER}?" if remote else f"Yes, {config.OWNER}? Press command H and tell me.")
            return

        woke, rest = strip_wake_word(text)
        attentive = time.time() < self.attention_until
        busy = self.state in ("thinking", "working", "speaking")
        command = rest if woke else text

        if (woke or attentive or busy) and STOP_RE.match(command.strip()):
            await self.interrupt()
            self.speaker.say("Of course.")
            return
        if woke and not rest:
            self.attention_until = time.time() + config.FOLLOW_UP_SECONDS
            self.speaker.say(f"Yes, {config.OWNER}?")
            await self.event("voice", {"state": "idle", "attentive": True, "window": config.FOLLOW_UP_SECONDS})
            return
        if woke or attentive:
            self.attention_until = 0
            await self.submit(command, "voice")
            return
        await self.event("voice", {"state": "idle", "attentive": False})

    def listen(self):
        """⌘H / mic button: stop talking and record until ⌘S (⌘H again cancels)."""
        if not self.listener:
            return
        if not self.listener.listening:
            self.speaker.interrupt()
        self.voice_target = None  # spoken to on this computer: answer on this computer
        self.listener.activate()

    def stop_listening(self) -> bool:
        """⌘S: finish recording and send it (thread-safe: the hotkey thread calls it directly)."""
        return bool(self.listener and self.listener.stop())

    def listen_threadsafe(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.listen)

    def on_listen_state(self, state: str):
        """Hook for a menu bar indicator; unused for now."""

    def hotkey_ready_threadsafe(self):
        def ready():
            asyncio.ensure_future(self.event("hotkey", {"ok": True}))
        if self.loop:
            self.loop.call_soon_threadsafe(ready)

    async def submit(self, text: str, source: str):
        text = text.strip()
        waiting = bool(self.tray.describe()["thumbs"]) and source in ("voice", "typed")
        if not text and not waiting:
            return
        if source in ("voice", "typed") and text:
            switches, rest = parse_switches(text)
            if switches:
                await self.event("user", {"text": text, "source": source})
                for kind, value in switches:
                    await self.set_mode(kind, value, announce=not rest)
                if not rest and not waiting:
                    return
                text = rest
                images = self.tray.take() if waiting else []
                if images:
                    await self.event("tray", self.tray.describe())
                await self.queue.put((text or "(The user sent this without a message.)", source, images))
                return
        images = self.tray.take() if waiting else []
        if images:
            await self.event("tray", self.tray.describe())
        await self.event("user", {"text": text, "source": source,
                                  "images": [f"/uploads/{i['path'].name}" for i in images]})
        await self.queue.put((text or "(The user sent this without a message.)", source, images))

    async def set_mode(self, kind: str, value: str, announce: bool = True) -> str:
        changed = self.modes.set(kind, value)
        state = self.modes.describe()
        await self.event("modes", state)
        if changed and hasattr(self.brain, "on_mode_change"):
            self.brain.on_mode_change()
        label = (FOCUS if kind == "focus" else POWER)[value]["label"]
        if changed and announce:
            await self.event("protocol", {"name": label + (" MODE" if kind == "focus" and value != "standard" else ""),
                                          "mode": {"math": "briefing", "science": "scan"}.get(value, "alert" if value == "full" else "idle")})
            spoken = (FOCUS if kind == "focus" else POWER)[value].get("spoken") or (
                "Back to normal power, {owner}." if kind == "power" else "Standard mode, {owner}.")
            self.speaker.say(spoken.format(owner=config.OWNER))
        print(f"[modes] {kind} → {value} (effort {state['effort']})")
        return f"{kind} is now {value}; reasoning effort {state['effort']}." if changed else f"{kind} was already {value}."

    async def interrupt(self):
        while not self.queue.empty():
            self.queue.get_nowait()
        if self.current and not self.current.done():
            self.current.cancel()
        self.speaker.interrupt()
        self._resolve_confirm(False)
        await self.event("state", {"state": "idle"})

    async def on_client_message(self, msg: dict):
        kind = msg.get("type")
        if kind == "command":
            await self.submit(msg.get("text", ""), "typed")
        elif kind == "attach":
            try:
                for img in (msg.get("images") or [])[:uploads.MAX_IMAGES]:
                    await asyncio.to_thread(self.tray.add, img)
            except uploads.UploadError as e:
                await self.event("progress", {"text": str(e)})
            await self.event("tray", self.tray.describe())
        elif kind == "detach":
            self.tray.remove(msg.get("index") if isinstance(msg.get("index"), int) else None)
            await self.event("tray", self.tray.describe())
        elif kind == "camera_frame":
            fut = self.frame_waiters.get(msg.get("id"))
            if fut and not fut.done():
                fut.set_result((msg["data"], msg.get("media", "image/jpeg")))
        elif kind == "confirm_answer":
            self._resolve_confirm(bool(msg.get("value")))
        elif kind == "interrupt":
            await self.interrupt()
        elif kind == "listen":
            self.listen()
        elif kind == "stop_listening":
            self.stop_listening()
        elif kind == "mic":
            self.mic_on = bool(msg.get("enabled"))
            if self.listener:
                (self.listener.paused.clear if self.mic_on else self.listener.paused.set)()
        elif kind == "gesture":
            await self.on_gesture(msg.get("name", ""))
        elif kind == "media":  # Now Playing card buttons
            action = msg.get("action")
            if action in ("play", "pause", "toggle", "next", "previous"):
                try:
                    await asyncio.to_thread(system.media, action)
                except Exception as e:
                    await self.event("progress", {"text": f"Media control failed: {e}"})
                await asyncio.sleep(0.3)
                await self.refresh_media()
        elif kind == "set_mode":
            if msg.get("kind") in ("focus", "power"):
                await self.set_mode(msg["kind"], msg.get("value", ""))
        elif kind == "calendar_connect":  # Agenda card button: ask macOS for Calendar + Reminders access
            org = system.organizer()
            ok = await asyncio.to_thread(org._request, org.EVENTS)
            await asyncio.to_thread(org._request, org.REMINDERS)
            if not ok:
                await self.event("progress", {"text": "Calendar access is off: System Settings → Privacy & Security → Calendars → JARVIS."})
            await self.refresh_agenda()

    async def on_gesture(self, name: str):
        if name == "wake" and self.push_to_talk:
            await self.event("hud", {"action": "clear"})
        elif name == "wake":
            self.attention_until = time.time() + config.FOLLOW_UP_SECONDS + 4
            await self.event("voice", {"state": "idle", "attentive": True, "window": config.FOLLOW_UP_SECONDS + 4})
            self.speaker.say(f"Listening, {config.OWNER}.")
        elif name == "stop":
            if self.pending_confirm:
                self._resolve_confirm(False)
            else:
                await self.interrupt()
        elif name == "confirm":
            self._resolve_confirm(True)
        elif name == "cancel":
            self._resolve_confirm(False)
        elif name == "scan":
            await self.submit({
                "math": "Look through the camera. If there's a maths problem in view, read it and solve it; "
                        "otherwise briefly describe what you see.",
                "science": "Look through the camera. If there's a science project or experiment in view, tell me what "
                           "it is and the science behind it; otherwise briefly describe what you see.",
            }.get(self.modes.focus, "Scan through the camera and give me a quick tactical read of what you see."), "gesture")

    # --- workers ---------------------------------------------------------------
    async def brain_worker(self):
        while True:
            text, source, images = await self.queue.get()
            self.current = asyncio.create_task(self.brain.handle(text, source, images))
            try:
                await self.current
            except asyncio.CancelledError:
                await self.event("assistant_done", {})
            except Exception as e:
                print(f"[hub] brain crashed: {e!r}")
                await self.say_and_show("I've hit an internal fault, sir. Check the console.")

    @property
    def ui_visible(self) -> bool:
        return any(ws not in self.hidden for ws in self.clients)

    async def telemetry_worker(self):
        # Polls only what's on screen: with the window hidden, just the cheap counters the
        # proactive alerts need (plus the process list when the CPU is pinned).
        tick = 0
        while True:
            try:
                t = sysinfo.snapshot()  # ~1 ms
                visible = self.ui_visible
                if (visible and tick % 2 == 0) or (not visible and tick % 5 == 0 and t.get("cpu", 0) > 80):
                    t["procs"] = await asyncio.to_thread(sysinfo.top_processes)
                elif "procs" in self.telemetry:
                    t["procs"] = self.telemetry["procs"]
                if visible and tick % 5 == 0:
                    try:
                        if not plat.MAC:
                            raise RuntimeError  # Windows/Linux read it with small commands: off the event loop
                        t.update(system.quick_status())  # macOS: in-process, main thread
                    except Exception:
                        try:
                            t.update(await asyncio.to_thread(system.status))
                        except Exception:
                            pass
                else:
                    t.update({k: self.telemetry[k] for k in ("volume", "muted", "dark") if k in self.telemetry})
                self.telemetry = t
                await self.event("telemetry", t)
            except Exception as e:
                print(f"[hub] telemetry: {e}")
            tick += 1
            await asyncio.sleep(2)

    async def refresh_media(self):
        try:
            self.media = await asyncio.to_thread(system.now_playing)
        except Exception:
            self.media = None
        await self.event("media", {"media": self.media})

    async def media_worker(self):
        while True:
            if not self.ui_visible:
                await asyncio.sleep(3)  # nobody can see the Audio Feed card
                continue
            await self.refresh_media()
            await asyncio.sleep(4 if self.media and self.media.get("state") == "playing" else 10)

    async def refresh_agenda(self):
        try:
            org = system.organizer()
            self.agenda_authorized = org.authorized(org.EVENTS)
            self.agenda = await asyncio.to_thread(org.events, 1.5, None, False) if self.agenda_authorized else []
        except Exception as e:
            print(f"[hub] agenda: {e}")
        await self.event("agenda", {"events": self.agenda, "authorized": self.agenda_authorized})

    async def agenda_worker(self):
        while True:
            await self.refresh_agenda()
            await asyncio.sleep(60)

    # --- proactive awareness ------------------------------------------------------
    def _due(self, key: str, cooldown: float) -> bool:
        """True (and remembered) if `key` hasn't been announced within `cooldown` seconds."""
        now = time.time()
        if now - self.alerted.get(key, 0) < cooldown:
            return False
        self.alerted[key] = now
        return True

    async def notice(self, title: str, spoken: str, body: str = "", level: str = "warning"):
        await self.event("hud", {"action": "alert", "title": title, "body": body or spoken, "level": level})
        await self.event("assistant_delta", {"text": spoken})
        await self.event("assistant_done", {})
        self.speaker.say(spoken)

    async def watch_worker(self):
        cpu = collections.deque(maxlen=6)  # last minute, sampled every 10s
        charging = None
        while True:
            await asyncio.sleep(10)
            t = self.telemetry
            if not t or (self.listener and self.listener.listening):
                continue  # never talk over the user
            try:
                await self._check(t, cpu, charging)
                charging = t.get("charging")
            except Exception as e:
                print(f"[hub] watch: {e}")

    async def _check(self, t: dict, cpu, charging):
        o = config.OWNER
        bat, plugged = t.get("battery"), t.get("charging")
        if bat is not None:
            if charging is not None and plugged != charging:
                await self.event("hud", {"action": "alert", "title": "POWER",
                                         "body": f"{'Charger connected' if plugged else 'Running on battery'} · {bat}%", "level": "info"})
            if plugged:
                for lvl in (20, 10, 5):
                    self.alerted.pop(f"bat{lvl}", None)
            else:
                for lvl in (5, 10, 20):
                    if bat <= lvl:
                        if self._due(f"bat{lvl}", 6 * 3600):
                            urgent = "I'd plug in now. " if lvl <= 10 else ""
                            await self.notice("POWER LOW", f"Battery is at {bat} percent, {o}. {urgent}".strip(),
                                              level="critical" if lvl <= 10 else "warning")
                        break
        cpu.append(t.get("cpu", 0))
        procs = t.get("procs") or []
        if len(cpu) == cpu.maxlen and min(cpu) > 85 and procs and self._due("cpu", 900):
            p = procs[0]
            await self.notice("PROCESSOR LOAD", f"{p['name']} has been working the processor hard for a minute now, {o}.",
                              body=f"{p['name']} · {p['cpu']:.0f}% CPU · system {t['cpu']:.0f}%")
        if t.get("mem", 0) > 92 and self._due("mem", 1800):
            hog = max(procs, key=lambda p: p["mem"], default=None)
            extra = f" {hog['name']} is the largest." if hog else ""
            await self.notice("MEMORY", f"Memory is nearly full, {o}.{extra}", body=f"{t['mem']:.0f}% in use")
        if t.get("disk_free_gb") is not None and t["disk_free_gb"] < 10 and self._due("disk", 12 * 3600):
            await self.notice("STORAGE", f"You're down to {t['disk_free_gb']:.0f} gigabytes of free disk space, {o}.")
        now = datetime.datetime.now()
        for e in self.agenda:
            if e.get("all_day"):
                continue
            start = datetime.datetime.fromisoformat(e["start"])
            mins = (start - now).total_seconds() / 60
            if 0 < mins <= config.MEETING_HEADS_UP_MIN and self._due(f"evt:{e['title']}:{e['start']}", 86400):
                where = f" at {e['location']}" if e.get("location") else ""
                await self.notice("UPCOMING", f"{e['title']} begins in {max(1, round(mins))} minutes{where}, {o}.",
                                  body=f"{start:%H:%M} · {e['title']}{where}", level="info")

    async def weather_worker(self):
        while True:
            try:
                self.last_weather = await weather()
                await self.event("weather", self.last_weather)
            except Exception as e:
                print(f"[hub] weather: {e}")
            await asyncio.sleep(900)


HUB = Hub()


ALLOWED_ORIGINS = {f"http://{h}:{config.PORT}" for h in ("127.0.0.1", "localhost", config.HOST)}


async def ws_handler(request):
    # Only the JARVIS page itself may connect; any other website could otherwise drive your computer.
    if request.headers.get("Origin") not in ALLOWED_ORIGINS:
        return web.Response(status=403, text="forbidden origin")
    return await serve_ws(request, remote=False)


async def remote_ws_handler(request):
    # Same-origin page only, and a paired device (the cookie is SameSite=Strict as well).
    if request.headers.get("Origin") != f"https://{request.host}":
        return web.Response(status=403, text="forbidden origin")
    token = request.cookies.get(COOKIE)
    if not HUB.remote.device(token):
        return web.Response(status=401, text="not paired")
    return await serve_ws(request, remote=True, token=token)


LOCAL_ONLY = {"remote", "calendar_connect"}  # never accepted from a remote device


async def serve_ws(request, remote: bool, token: str | None = None):
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=20 * 2**20)
    await ws.prepare(request)
    ws.jarvis_token = token
    HUB.clients.add(ws)
    if remote:
        HUB.remote_clients.add(ws)
    await ws.send_str(json.dumps({
        "type": "hello",
        "model": config.MODEL,
        "brain": HUB.brain.label,
        "online": bool(HUB.brain.client),
        "mic": HUB.mic_on,
        "mic_mode": "push" if HUB.push_to_talk else ("wake" if HUB.listener else "off"),
        "hotkey": bool(HUB.hotkey and HUB.hotkey.ok),
        "keys": plat.KEYS,
        "os": plat.NAME,
        "machine": plat.MACHINE,
        "owner": config.OWNER,
        "weather": getattr(HUB, "last_weather", None),
        "media": HUB.media,
        "modes": HUB.modes.describe(),
        "tray": HUB.tray.describe(),
        "agenda": {"events": HUB.agenda, "authorized": HUB.agenda_authorized},
        "remote_client": remote,
        **({} if remote else {"remote": HUB.remote.describe()}),
    }))
    try:
        async for m in ws:
            if m.type == WSMsgType.TEXT:
                try:
                    msg = json.loads(m.data)
                    kind = msg.get("type")
                    if kind == "visibility":
                        (HUB.hidden.discard if msg.get("visible") else HUB.hidden.add)(ws)
                        continue
                    if kind in ("command", "voice_audio", "barge_in"):
                        HUB.voice_target = ws if remote else None  # reply where the user is
                    if remote:
                        if kind in LOCAL_ONLY or (kind in ("listen", "stop_listening", "mic")):
                            continue  # this computer's mic and settings stay with this computer
                        if not HUB.remote.device(token):
                            await ws.close()
                            break
                        if kind == "voice_audio":
                            HUB.speaker.interrupt()
                            asyncio.ensure_future(HUB.remote_voice(msg.get("data", "")))
                            continue
                        if kind == "barge_in":
                            HUB.speaker.interrupt()
                            continue
                    elif kind == "remote":
                        await HUB.remote_action(msg)
                        continue
                    await HUB.on_client_message(msg)
                except Exception as e:
                    print(f"[ws] bad message: {e}")
    finally:
        HUB.clients.discard(ws)
        HUB.hidden.discard(ws)
        HUB.remote_clients.discard(ws)
        if HUB.voice_target is ws:
            HUB.voice_target = None
    return ws


async def index(request):
    return web.FileResponse(config.UI_DIR / "index.html")


def greeting() -> str:
    hour = datetime.datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}, {config.OWNER}. All systems online."


async def on_startup(app):
    HUB.loop = asyncio.get_running_loop()
    HUB.tools = ToolRunner(HUB)
    HUB.brain = make_brain(HUB)
    if config.MIC_ENABLED:
        HUB.listener = Listener(HUB.loop, HUB.on_phrase, HUB.mic_level, HUB.voice_state)
    HUB.speaker = Speaker(HUB.voice_level, HUB.voice_state,
                          HUB.listener.paused if HUB.listener and not HUB.listener.push else None)
    HUB.speaker.remote_out = HUB.remote_speech_out
    HUB.speaker.on_interrupt = HUB.remote_speech_stop
    HUB.speaker.start()
    if HUB.remote.enabled:
        try:
            await HUB.start_remote()
        except Exception as e:
            print(f"[remote] couldn't start: {e}")
    o = config.OWNER  # lines JARVIS says often: rendered once, then instant
    asyncio.create_task(HUB.speaker.precache([
        f"Right away, {o}.", "On it.", f"Of course, {o}.", "Of course.", f"Yes, {o}?", f"Listening, {o}.",
        "Proceeding.", "Understood, standing down.", f"Certainly, {o}.", f"Very good, {o}.", f"Done, {o}.",
        *[v["spoken"].format(owner=o) for v in (*FOCUS.values(), *POWER.values()) if v.get("spoken")],
        f"Back to normal power, {o}.",
    ]))
    if HUB.listener:
        HUB.listener.start()
    workers = [HUB.brain_worker, HUB.telemetry_worker, HUB.weather_worker, HUB.media_worker, HUB.agenda_worker]
    if config.ALERTS:
        workers.append(HUB.watch_worker)
    for worker in workers:
        asyncio.create_task(worker())
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"\n  J.A.R.V.I.S. Ultimate 2 online — interface at {url}")
    via = f"Claude Code {HUB.brain.version} on your Claude plan" if isinstance(HUB.brain, CodeBrain) else "Anthropic API (credits)"
    print(f"  Brain: {config.MODEL} via {via}, effort {HUB.modes.effort} "
          f"({HUB.modes.describe()['focus_label'].lower()} mode, {HUB.modes.power} power)"
          f"{'' if HUB.brain.client else '  [OFFLINE: ' + HUB.brain.offline_reason + ']'}\n")
    if HUB.push_to_talk:
        print("  Microphone: off until you press ⌘H" + ("" if HUB.hotkey and HUB.hotkey.ok else "  [⌘H needs Accessibility permission]") + "\n")
    HUB.speaker.say(greeting())


def make_brain(hub):
    if config.BRAIN == "api":
        return Brain(hub)
    code = CodeBrain(hub)
    if config.BRAIN == "claude-code" or code.binary:
        return code
    return Brain(hub)


@web.middleware
async def no_stale_cache(request, handler):
    """Let the browser keep files but always revalidate them (cheap 304s), so UI updates apply on reload."""
    resp = await handler(request)
    resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


async def health(request):
    return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})


async def uploaded_image(request):
    """An image the user attached, for the conversation feed."""
    path = uploads.file_for(request.match_info["name"])
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


async def generated_image(request):
    """A picture JARVIS generated, for the HUD to display."""
    from . import imagegen
    path = imagegen.file_for(request.match_info["name"])
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


def create_app() -> web.Application:
    app = web.Application(client_max_size=20 * 2**20, middlewares=[no_stale_cache])
    app.router.add_get("/", index)
    app.router.add_get("/generated/{name}", generated_image)
    app.router.add_get("/uploads/{name}", uploaded_image)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", ws_handler)
    app.router.add_route("*", "/mcp", mcp_bridge.make_handler(lambda: HUB.tools))
    app.router.add_static("/", config.UI_DIR)
    app.on_startup.append(on_startup)
    return app


# ---------------------------------------------------------------- remote devices

PAIR_PAGE = config.UI_DIR / "pair.html"


@web.middleware
async def require_device(request, handler):
    """Everything on the remote port needs a paired device, except the pairing page itself."""
    if request.path in ("/pair", "/manifest.webmanifest", "/icon.png") or HUB.remote.device(request.cookies.get(COOKIE)):
        resp = await handler(request)
        resp.headers.setdefault("Cache-Control", "no-cache")
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp
    if request.method == "GET" and request.path == "/":
        raise web.HTTPFound("/pair")
    return web.Response(status=401, text="This device isn't paired with JARVIS.")


async def pair_page(request):
    return web.FileResponse(PAIR_PAGE, headers={"Cache-Control": "no-store"})


async def pair_submit(request):
    if request.headers.get("Origin") != f"https://{request.host}":
        return web.json_response({"ok": False, "error": "forbidden origin"}, status=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    token = HUB.remote.pair(str(body.get("code", "")), str(body.get("name", "")))
    if not token:
        await HUB.event_local("remote", HUB.remote.describe())
        return web.json_response({"ok": False, "error": "That code is wrong or has expired. Make a new one on the computer."}, status=403)
    name = HUB.remote.device(token)["name"]
    print(f"[remote] paired {name}")
    await HUB.event_local("remote", HUB.remote.describe())
    await HUB.event_local("hud", {"action": "alert", "title": "DEVICE PAIRED", "body": name, "level": "info"})
    resp = web.json_response({"ok": True})
    resp.set_cookie(COOKIE, token, max_age=400 * 86400, secure=True, httponly=True, samesite="Strict", path="/")
    return resp


def create_remote_app() -> web.Application:
    app = web.Application(client_max_size=20 * 2**20, middlewares=[require_device])
    app.router.add_get("/", index)
    app.router.add_get("/pair", pair_page)
    app.router.add_post("/pair", pair_submit)
    app.router.add_get("/ws", remote_ws_handler)
    app.router.add_get("/generated/{name}", generated_image)
    app.router.add_get("/uploads/{name}", uploaded_image)
    app.router.add_static("/", config.UI_DIR)
    return app  # deliberately no /mcp: the tool bridge stays on this computer only


async def serve(open_window: bool, ready=None):
    """Start the server, then (once it's actually listening) open the interface window."""
    runner = web.AppRunner(create_app(), handle_signals=False)
    await runner.setup()
    await web.TCPSite(runner, config.HOST, config.PORT).start()
    if hasattr(HUB.brain, "prewarm"):
        HUB.brain.prewarm()  # start Claude Code now (its tools connect to the server just started)
    if ready:
        ready()
    if open_window:
        from .window import open_interface
        open_interface()
    try:
        await asyncio.Event().wait()
    finally:
        await HUB.stop_remote()
        await runner.cleanup()


def watch_parent():
    """Under JARVIS.app: exit if the app is gone (crash or force quit) so no orphan keeps the port."""
    import os
    import threading
    parent = os.environ.get("JARVIS_PARENT_PID")
    if not parent:
        return

    def run():
        while True:
            time.sleep(2)
            if os.getppid() != int(parent):
                print("  JARVIS.app is gone; shutting down.")
                os._exit(0)
    threading.Thread(target=run, daemon=True, name="parent-watch").start()


def main():
    """Terminal mode (./run.sh)."""
    import signal
    import sys
    sys.stdout.reconfigure(line_buffering=True)

    import atexit
    import faulthandler
    import os
    faulthandler.enable()  # a native crash prints a Python traceback to the log
    stamp = lambda: datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp()}] core: starting (pid {os.getpid()}, parent {os.getppid()})")
    atexit.register(lambda: print(f"[{stamp()}] core: exiting"))

    def on_term(signum, _frame):  # JARVIS.app quitting: shut down cleanly so Claude Code subprocesses are stopped too
        print(f"[{stamp()}] core: received {signal.Signals(signum).name}")
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    if hasattr(signal, "SIGHUP"):  # not on Windows
        signal.signal(signal.SIGHUP, on_term)
    watch_parent()
    print(f"[{stamp()}] core: {plat.description()}")
    if config.MIC_ENABLED and config.MIC_MODE != "wake":
        try:
            from .hotkey import Hotkey
            HUB.hotkey = Hotkey(HUB.listen_threadsafe, on_stop=HUB.stop_listening)
            HUB.hotkey.install(on_ready=HUB.hotkey_ready_threadsafe)
        except Exception as e:  # e.g. Linux without a desktop session: the mic button still works
            HUB.hotkey = None
            print(f"[hotkey] {plat.KEYS['listen']} unavailable ({e}); use the mic button in the window.")
    try:
        asyncio.run(serve(config.OPEN_BROWSER))
    except KeyboardInterrupt:
        print("\n  JARVIS offline.")


if __name__ == "__main__":
    main()
