"""Minimal MCP server (streamable HTTP, JSON responses) exposing JARVIS's tools to Claude Code.

Claude Code connects to http://127.0.0.1:<port>/mcp with a per-run bearer token. Every
call is executed by the same ToolRunner the API brain uses, so the safety gate
(spoken yes/no for destructive commands), the HUD and the camera all behave the same.
"""
import json
import secrets
from types import SimpleNamespace

from aiohttp import web

from . import plat
from .tools import CUSTOM_TOOLS

_MOD = "cmd" if plat.MAC else "ctrl"
_SHELL = plat.shell()[1]
_OPEN = {"mac": "`open -a`", "windows": "`Start-Process`", "linux": "`xdg-open`"}[plat.NAME]

TOKEN = secrets.token_urlsafe(24)
SERVER_NAME = "jarvis"

COMPUTER_TOOL = {
    "name": "computer",
    "description": (
        f"Control this {plat.MACHINE}'s screen, mouse and keyboard. Always take a screenshot first; coordinates are "
        "pixels in the most recent screenshot (top-left origin). Actions: screenshot, zoom (region "
        "[x0,y0,x1,y1] for a closer look), left_click, right_click, middle_click, double_click, "
        f"triple_click (optional coordinate; optional text = modifier keys like '{_MOD}' or 'shift'), "
        "left_click_drag (start_coordinate -> coordinate), mouse_move, scroll (scroll_direction, "
        f"scroll_amount), type (text), key (text like '{_MOD}+c', 'Return', '{_MOD}+shift+t'; optional "
        "repeat), hold_key (text, duration), cursor_position, wait (duration seconds)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [
                "screenshot", "zoom", "left_click", "right_click", "middle_click", "double_click",
                "triple_click", "left_click_drag", "mouse_move", "left_mouse_down", "left_mouse_up",
                "cursor_position", "scroll", "type", "key", "hold_key", "wait"]},
            "coordinate": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "start_coordinate": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
            "text": {"type": "string"},
            "scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "scroll_amount": {"type": "integer"},
            "repeat": {"type": "integer"},
            "duration": {"type": "number"},
        },
        "required": ["action"],
    },
}

SHELL_TOOL = {
    "name": "shell",
    "description": (
        f"Run a {_SHELL} command on this {plat.MACHINE} and return its output. The working directory persists "
        f"between calls. Use for files, apps ({_OPEN}), system info, git, scripts, anything command-line."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}


def _tool_list() -> list[dict]:
    out = []
    for t in [SHELL_TOOL, COMPUTER_TOOL, *CUSTOM_TOOLS]:
        schema = dict(t["input_schema"])
        out.append({"name": t["name"], "description": t["description"], "inputSchema": schema})
    return out


def _to_mcp_content(content: list[dict]) -> list[dict]:
    out = []
    for c in content:
        if c.get("type") == "image":
            src = c["source"]
            out.append({"type": "image", "data": src["data"], "mimeType": src["media_type"]})
        else:
            out.append({"type": "text", "text": c.get("text", "")})
    return out


def mcp_config(port: int) -> str:
    return json.dumps({"mcpServers": {SERVER_NAME: {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/mcp",
        "headers": {"Authorization": f"Bearer {TOKEN}"},
    }}})


def make_handler(get_runner):
    """get_runner() -> ToolRunner (created lazily once the hub is up)."""

    async def handler(request: web.Request):
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.Response(status=401, text="unauthorized")
        if request.method != "POST":
            return web.Response(status=405)
        try:
            msg = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        if isinstance(msg, list):  # batch
            results = [r for r in [await _dispatch(m, get_runner) for m in msg] if r]
            return web.json_response(results) if results else web.Response(status=202)
        result = await _dispatch(msg, get_runner)
        return web.json_response(result) if result else web.Response(status=202)

    return handler


async def _dispatch(msg: dict, get_runner) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "2.0"},
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": _tool_list()})
    if method == "tools/call":
        name, args = params.get("name", ""), params.get("arguments") or {}
        runner = get_runner()
        if name == "computer":
            args = dict(args)
            action = args.pop("action", "")
            block = SimpleNamespace(id="mcp", name=action, input=args, toolset_name="computer")
        elif name == "shell":
            block = SimpleNamespace(id="mcp", name="bash", input=args, toolset_name=None)
        else:
            block = SimpleNamespace(id="mcp", name=name, input=args, toolset_name=None)
        res = await runner.run(block)
        content = _to_mcp_content(res.get("content", []))
        if name == "computer" and any(c["type"] == "image" for c in content) and block.name == "screenshot":
            from .computer import GEOMETRY, screen_size
            w, h = screen_size()
            content.append({"type": "text", "text": f"Screenshot is {round(w * GEOMETRY.scale)}x{round(h * GEOMETRY.scale)} px; use these pixel coordinates."})
        return ok({"content": content, "isError": bool(res.get("is_error"))})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
