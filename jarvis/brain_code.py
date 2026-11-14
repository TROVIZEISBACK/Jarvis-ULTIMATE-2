"""Brain backed by Claude Code in headless mode — uses your Claude plan (Pro/Max) instead of API credits.

One long-lived `claude -p --input-format stream-json` process is started (and
pre-warmed) ahead of time and fed each request on stdin, so replies don't pay
Claude Code's start-up, MCP connection and session reload every time. JARVIS's tools reach Claude Code through
the local MCP bridge (jarvis/mcp_bridge.py); Claude Code's own shell and file-editing
tools are switched off so every action passes through JARVIS's safety gate.
"""
import asyncio
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config
from . import uploads
from .brain import build_system
from .mcp_bridge import SERVER_NAME, mcp_config
from .voice import SentenceChunker

MIN_VERSION = (2, 1, 280)  # first Claude Code release that supports Opus 5.5
BUILTIN_TOOLS = "WebSearch,WebFetch,Read,Glob,Grep"


def _version(binary: str) -> tuple[int, ...]:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15).stdout
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
        return tuple(int(x) for x in m.groups()) if m else ()
    except Exception:
        return ()


def find_claude() -> tuple[str | None, str]:
    """Pick the newest Claude Code binary: $JARVIS_CLAUDE_BIN, PATH, ~/.local/bin, or the desktop app's copy."""
    candidates = []
    if os.environ.get("JARVIS_CLAUDE_BIN"):
        candidates.append(os.environ["JARVIS_CLAUDE_BIN"])
    home = Path.home()
    for c in (shutil.which("claude"), str(home / ".local/bin/claude"), str(home / ".claude/local/claude"),
              str(home / ".local/bin/claude.exe"), os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")):
        if c and os.path.exists(c):
            candidates.append(c)
    candidates += glob.glob(str(Path.home() / "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"))
    best, best_v = None, ()
    for c in dict.fromkeys(candidates):
        v = _version(c)
        if v > best_v:
            best, best_v = c, v
    if not best:
        return None, "Claude Code isn't installed"
    if best_v < MIN_VERSION:
        return None, f"Claude Code {'.'.join(map(str, best_v))} is too old for Opus 5.5 (need 2.1.280+); run `claude update`"
    return best, ".".join(map(str, best_v))


class CodeBrain:
    label = "CLAUDE PLAN"

    def __init__(self, hub):
        self.hub = hub
        self.session_id: str | None = None
        self.last_active = time.time()
        self.binary, info = find_claude()
        self.client = self.binary  # truthy when usable (mirrors Brain.client)
        self.offline_reason = "" if self.binary else info
        self.version = info if self.binary else ""
        self.system = build_system("claude-code")
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr_tail = ""
        self.proc_effort = None
        self.busy = False
        self.context_tokens = 0   # size of the conversation as of the latest reply
        self.context_window = 0   # the model's context window, once Claude Code has reported it
        self._spawn_lock = asyncio.Lock()

    @property
    def has_conversation(self) -> bool:
        return bool(self.session_id)

    def reset(self, note: str = ""):
        self.session_id = None
        self.context_tokens = 0
        self.system = build_system("claude-code")
        self._stop_proc()
        if note:
            print(f"[brain] conversation reset: {note}")
        self.prewarm()

    def on_mode_change(self):
        """Effort changed: restart the idle process at the new level so the next request is quick."""
        if not self.busy:
            self.prewarm()  # _ensure_proc replaces an idle process whose effort is out of date

    def prewarm(self):
        """Start the Claude Code process in the background so the next request is quick."""
        if self.binary:
            asyncio.ensure_future(self._ensure_proc())

    def _stop_proc(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
        self.proc = None

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        async with self._spawn_lock:
            if self.proc and self.proc.returncode is None:
                if self.proc_effort == self.hub.modes.effort or self.busy:
                    return self.proc
                self._stop_proc()  # idle at an old effort level (the mode changed): restart at the new one
            self.proc = await asyncio.create_subprocess_exec(
                *self._args(), cwd=str(Path.home()), env=self._env(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=64 * 2**20,
            )
            self.proc_effort = self.hub.modes.effort
            asyncio.ensure_future(self._drain_stderr(self.proc))
            return self.proc

    async def _drain_stderr(self, proc):
        async for line in proc.stderr:  # keep the pipe from filling; remember the end for error reports
            self.stderr_tail = (self.stderr_tail + line.decode(errors="replace"))[-2000:]

    def _args(self) -> list[str]:
        args = [
            self.binary, "-p", "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", config.MODEL, "--effort", self.hub.modes.effort,
            "--system-prompt", self.system,
            "--tools", BUILTIN_TOOLS,
            "--mcp-config", mcp_config(config.PORT), "--strict-mcp-config",
            "--allowedTools", f"mcp__{SERVER_NAME}", *BUILTIN_TOOLS.split(","),
            "--permission-mode", "dontAsk",
            "--setting-sources", "",
        ]
        if self.session_id:
            args += ["--resume", self.session_id]
        return args

    @staticmethod
    def _env() -> dict:
        env = dict(os.environ)
        # Make sure Claude Code bills the signed-in Claude plan, never an API key.
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
            env.pop(k, None)
        return env

    async def handle(self, text: str, source: str = "voice", images: list | None = None):
        if not self.binary:
            await self.hub.say_and_show(f"My higher functions are offline, {config.OWNER}. {self.offline_reason}.")
            return
        self.last_active = time.time()  # idle resets are done by jarvis/history.py, which keeps the memory
        now = datetime.datetime.now().strftime("%A %d %B %Y, %H:%M")
        prompt = f"{self.hub.modes.preamble()}[{now} · via {source}]\n{text}"
        if self.proc and self.proc.returncode is None and self.proc_effort != self.hub.modes.effort:
            self._stop_proc()  # effort is fixed per process: restart (resuming the session) at the new level

        await self.hub.event("state", {"state": "thinking"})
        chunker = SentenceChunker()
        result = None
        finished = False
        self.busy = True
        try:
            proc = await self._ensure_proc()
            content = uploads.content_blocks(images, prompt) if images else prompt
            line = {"type": "user", "message": {"role": "user", "content": content}}
            proc.stdin.write((json.dumps(line) + "\n").encode())
            await proc.stdin.drain()
            while True:
                raw = await proc.stdout.readline()
                if not raw:  # process exited
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                result = await self._on_message(msg, chunker) or result
                if msg.get("type") == "result":
                    break
            for s in chunker.flush():
                self.hub.speaker.say(s)
            await self.hub.event("assistant_done", {})
            if not result:
                await asyncio.sleep(0.2)
                print(f"[brain] claude exited ({proc.returncode}): {self.stderr_tail[-500:]}")
                self.proc = None
                await self.hub.say_and_show(f"My connection to Claude failed, {config.OWNER}. Check the console.")
            elif result.get("is_error"):
                await self._report_error(result.get("result") or "")
            finished = True
        except asyncio.CancelledError:
            raise
        except (BrokenPipeError, ConnectionResetError) as e:
            print(f"[brain] claude process lost: {e}; {self.stderr_tail[-300:]}")
            await self.hub.say_and_show(f"My connection to Claude dropped, {config.OWNER}. Please try again.")
        finally:
            self.busy = False
            if not finished:
                self._stop_proc()  # interrupted mid-reply: start a fresh process that resumes the session
            self.last_active = time.time()
            await self.hub.event("state", {"state": "idle"})
            if not self.proc:
                self.prewarm()

    async def _on_message(self, msg: dict, chunker: SentenceChunker):
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "init":
            self.session_id = msg.get("session_id") or self.session_id
        elif kind == "stream_event":
            ev = msg.get("event", {})
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta", {})
                if d.get("type") == "text_delta":
                    await self.hub.event("assistant_delta", {"text": d["text"]})
                    for s in chunker.feed(d["text"]):
                        self.hub.speaker.say(s)
                elif d.get("type") == "thinking_delta" and d.get("thinking", "").strip():
                    await self.hub.event("progress", {"text": d["thinking"]})
            elif ev.get("type") == "message_stop":
                # separate spoken paragraphs between tool rounds
                for s in chunker.flush():
                    self.hub.speaker.say(s)
                await self.hub.event("assistant_done", {})
        elif kind == "assistant":
            usage = msg.get("message", {}).get("usage") or {}
            if not msg.get("parent_tool_use_id"):  # the main conversation, not a sub-agent
                size = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens", "output_tokens"))
                self.context_tokens = size or self.context_tokens
            for block in msg.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    await self.hub.event("state", {"state": "working"})
                    name = block.get("name", "")
                    if not name.startswith("mcp__"):  # JARVIS tools announce themselves
                        await self.hub.event("action", {"tool": name, "input": _short(block.get("input", {}))})
        elif kind == "result":
            self.session_id = msg.get("session_id") or self.session_id
            for model, used in (msg.get("modelUsage") or {}).items():
                if model.startswith(config.MODEL) and used.get("contextWindow"):
                    self.context_window = int(used["contextWindow"])
            return msg
        return None

    async def _report_error(self, text: str):
        print(f"[brain] claude error: {text}")
        low = text.lower()
        if "limit" in low and ("usage" in low or "reset" in low):
            await self.hub.say_and_show(f"I've reached the usage limit on your Claude plan, {config.OWNER}. {text}")
        elif "login" in low or "auth" in low or "401" in low:
            await self.hub.say_and_show(f"Claude Code isn't signed in, {config.OWNER}. Run claude and log in, then try again.")
        else:
            self.reset("error")
            await self.hub.say_and_show(f"Something went wrong, {config.OWNER}: {text[:200]}")


def _short(inp: dict) -> dict:
    return {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in inp.items()}
