"""Image generation through Cloudflare Workers AI.

Credentials come from `.env` (read fresh on every request, so adding them needs no restart):
    CLOUDFLARE_API_TOKEN=...      an API token with the "Workers AI" permission
    CLOUDFLARE_ACCOUNT_ID=...     optional; looked up with the token when missing
Only the prompt is sent to Cloudflare. Images are saved to ~/Pictures/JARVIS.
"""
import asyncio
import base64
import os
import re
import time
from pathlib import Path

import aiohttp

from . import config

API = "https://api.cloudflare.com/client/v4"
OUT_DIR = Path(os.environ.get("JARVIS_IMAGE_DIR") or Path.home() / "Pictures" / "JARVIS").expanduser()
DEFAULT_MODEL = "@cf/black-forest-labs/flux-1-schnell"
SHAPES = {"square": (1024, 1024), "wide": (1344, 768), "tall": (768, 1344)}
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(png|jpg|webp)")
_account_cache: dict[str, str] = {}


class ImageError(Exception):
    pass


def credentials() -> tuple[str | None, str | None]:
    """(token, account id) from .env or the environment. Key names are matched loosely
    (CLOUDFLARE_API_TOKEN, CF_API_KEY, CLOUDFARE_API...), because people name them differently."""
    env = {**os.environ, **config.read_dotenv()}
    token = account = None
    for key, value in env.items():
        k = key.upper()
        if not value or not ("CLOUDFLARE" in k or "CLOUDFARE" in k or k.startswith("CF_")):
            continue
        if "ACCOUNT" in k:
            account = account or value
        elif any(w in k for w in ("TOKEN", "KEY", "API")) and "EMAIL" not in k:
            token = token or value
    return token, account


def available() -> bool:
    return bool(credentials()[0])


async def _account_id(session: aiohttp.ClientSession, token: str) -> str:
    if token in _account_cache:
        return _account_cache[token]
    async with session.get(f"{API}/accounts", headers={"Authorization": f"Bearer {token}"}) as r:
        data = await _json(r)
        if r.status in (401, 403):
            raise ImageError(f"Cloudflare rejected the API token ({_errors(data)}). Check the token in .env.")
    accounts = (data or {}).get("result") or []
    if not accounts:
        raise ImageError(
            "Cloudflare didn't list an account for this API token. Add CLOUDFLARE_ACCOUNT_ID=<your account id> "
            f"to {config.ROOT / '.env'} (it's on the Cloudflare dashboard, under Workers & Pages).")
    _account_cache[token] = accounts[0]["id"]
    return accounts[0]["id"]


async def _json(r):
    try:
        return await r.json(content_type=None)
    except ValueError:
        return {"errors": [{"message": (await r.text())[:200] or f"HTTP {r.status}"}], "success": False}


def _errors(data) -> str:
    if isinstance(data, dict):
        return "; ".join(str(e.get("message", e)) for e in data.get("errors") or []) or str(data)[:300]
    return str(data)[:300]


async def _run(session, token, account, model, payload) -> bytes:
    url = f"{API}/accounts/{account}/ai/run/{model}"
    async with session.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}) as r:
        kind = r.headers.get("Content-Type", "")
        if kind.startswith("image/"):           # some models answer with the picture itself
            return await r.read()
        data = await _json(r)
        if r.status in (401, 403):
            raise ImageError(f"Cloudflare rejected the API token ({_errors(data)}). It needs the Workers AI permission.")
        if r.status >= 400 or not (isinstance(data, dict) and data.get("success", True)):
            raise ImageError(f"Cloudflare error {r.status}: {_errors(data)}")
        result = data.get("result") or {}
        b64 = result.get("image") or (result.get("images") or [None])[0]
        if not b64:
            raise ImageError(f"Cloudflare returned no image: {str(data)[:200]}")
        return base64.b64decode(b64.split(",")[-1])


def _fit(data: bytes, w: int, h: int) -> bytes:
    """Centre-crop to the w:h shape when the model returned a different one."""
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    want = w / h
    if abs(img.width / img.height - want) < 0.03:
        return data
    if img.width / img.height > want:
        nw = round(img.height * want); box = ((img.width - nw) // 2, 0, (img.width + nw) // 2, img.height)
    else:
        nh = round(img.width / want); box = (0, (img.height - nh) // 2, img.width, (img.height + nh) // 2)
    out = io.BytesIO()
    img.convert("RGB").crop(box).save(out, "JPEG", quality=93)
    return out.getvalue()


def _extension(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpg"


async def generate(prompt: str, shape: str = "square", count: int = 1) -> list[Path]:
    """Generate `count` images for the prompt, save them, and return their paths."""
    prompt = " ".join(str(prompt).split())[:2000]
    if not prompt:
        raise ImageError("The image needs a description.")
    token, account = credentials()
    if not token:
        raise ImageError(
            f"No Cloudflare API token found. Ask the user to add these two lines to {config.ROOT / '.env'}: "
            "CLOUDFLARE_API_TOKEN=<token with Workers AI permission> and CLOUDFLARE_ACCOUNT_ID=<account id>.")
    model = os.environ.get("JARVIS_IMAGE_MODEL") or config.read_dotenv().get("JARVIS_IMAGE_MODEL") or DEFAULT_MODEL
    count = max(1, min(4, int(count or 1)))
    w, h = SHAPES.get(shape, SHAPES["square"])
    # Some models (FLUX schnell among them) only make squares; those are cropped to shape afterwards,
    # so ask for a composition that survives the crop.
    hint = {"wide": " Wide panoramic composition, main subject in the middle band of the frame.",
            "tall": " Tall vertical composition, main subject in the centre column of the frame."}.get(shape, "")
    base = {"prompt": prompt + hint}
    if "flux-1-schnell" in model:
        base["steps"] = 6
    sized = {**base, "width": w, "height": h}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        account = account or await _account_id(session, token)

        async def one(i: int) -> bytes:
            payload = {**(sized if shape in ("wide", "tall") else base), "seed": int(time.time() * 1000 + i * 7919) % 2**31}
            # Models accept different options. When one is refused, drop it and try again:
            # the prompt is the only thing every model needs.
            for _ in range(4):
                try:
                    return await _run(session, token, account, model, payload)
                except ImageError as e:
                    refused = re.findall(r"properties? '/(\w+)'", str(e)) or \
                        [k for k in payload if k != "prompt" and re.search(rf"\b{k}\b", str(e))]
                    refused = [k for k in refused if k in payload and k != "prompt"]
                    if not refused:
                        if len(payload) > 1 and "Cloudflare error 400" in str(e):
                            payload = {"prompt": base["prompt"]}
                            continue
                        raise
                    for k in refused:
                        payload.pop(k)
            return await _run(session, token, account, model, payload)

        images = await asyncio.gather(*(one(i) for i in range(count)))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "image"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    paths = []
    for i, data in enumerate(images):
        data = _fit(data, w, h) if shape in ("wide", "tall") else data
        path = OUT_DIR / f"{stamp}-{slug}{'-' + str(i + 1) if count > 1 else ''}.{_extension(data)}"
        path.write_bytes(data)
        paths.append(path)
    return paths


def file_for(name: str) -> Path | None:
    """The saved image with this file name, for the interface to display (no paths, no other files)."""
    if not SAFE_NAME.fullmatch(name):
        return None
    path = OUT_DIR / name
    return path if path.is_file() else None
