"""Images the user attaches in the HUD (📎 button, paste or drag-and-drop).

Attached images wait in a tray until the next command, typed or spoken, and go to the brain
with it. Each image is checked and cleaned here: only real JPEG/PNG/WebP/GIF pictures are
accepted, big ones are scaled down, and EXIF metadata (which on phone photos includes the GPS
location) is removed. They're saved to data/uploads (never uploaded to GitHub) so tools can use
the file too, e.g. "set this as my wallpaper" or "copy it to my phone".
"""
import base64
import io
import re
import time
from pathlib import Path

from . import config

DIR = config.DATA_DIR / "uploads"
MAX_IMAGES = 4
MAX_BYTES = 12 * 2**20        # per image, as received
MAX_EDGE = 2000               # longer side after cleaning; the model sees up to ~1568 px anyway
KEEP_DAYS = 14
STAGE_TTL = 15 * 60           # an attachment forgotten in the tray expires after this
SAFE_NAME = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}\.(jpg|png)")


class UploadError(Exception):
    pass


def clean(b64: str) -> tuple[bytes, str, str]:
    """Decode, verify and sanitise one image. Returns (bytes, media_type, thumbnail data URL)."""
    from PIL import Image, ImageOps
    try:
        raw = base64.b64decode(str(b64).split(",")[-1], validate=True)
    except Exception:
        raise UploadError("That attachment isn't a valid image.")
    if len(raw) > MAX_BYTES:
        raise UploadError("That image is too large (over 12 MB).")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise UploadError("That file isn't an image JARVIS can read (JPEG, PNG, WebP or GIF).")
    if img.format not in ("JPEG", "PNG", "WEBP", "GIF", "MPO"):
        raise UploadError(f"{img.format} images aren't supported; use JPEG, PNG, WebP or GIF.")
    img = ImageOps.exif_transpose(img)          # keep the photo the right way up once EXIF is gone
    if max(img.size) > MAX_EDGE:
        img.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    out = io.BytesIO()
    if alpha:                                    # screenshots with transparency stay PNG
        img.convert("RGBA").save(out, "PNG", optimize=True)
        media = "image/png"
    else:                                        # re-encoding drops all metadata, GPS included
        img.convert("RGB").save(out, "JPEG", quality=90)
        media = "image/jpeg"
    data = out.getvalue()
    thumb = img.convert("RGB")
    thumb.thumbnail((160, 160))
    tb = io.BytesIO()
    thumb.save(tb, "JPEG", quality=70)
    return data, media, "data:image/jpeg;base64," + base64.b64encode(tb.getvalue()).decode()


def save(data: bytes, media: str) -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    _prune()
    import secrets
    path = DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.{'png' if media == 'image/png' else 'jpg'}"
    path.write_bytes(data)
    return path


def _prune():
    cutoff = time.time() - KEEP_DAYS * 86400
    for p in DIR.glob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def file_for(name: str) -> Path | None:
    if not SAFE_NAME.fullmatch(name):
        return None
    path = DIR / name
    return path if path.is_file() else None


class Tray:
    """Images waiting to go with the next command."""

    def __init__(self):
        self.items: list[dict] = []   # {data, media, thumb, at}

    def add(self, b64: str) -> None:
        if len(self.items) >= MAX_IMAGES:
            raise UploadError(f"Up to {MAX_IMAGES} images at a time.")
        data, media, thumb = clean(b64)
        self.items.append({"data": data, "media": media, "thumb": thumb, "at": time.time()})

    def remove(self, index: int | None) -> None:
        if index is None:
            self.items.clear()
        elif 0 <= index < len(self.items):
            self.items.pop(index)

    def describe(self) -> dict:
        self._expire()
        return {"thumbs": [i["thumb"] for i in self.items], "max": MAX_IMAGES}

    def take(self) -> list[dict]:
        """Save and hand over everything in the tray (emptying it).
        Returns [{path, media, data(base64)}] ready for the brain."""
        self._expire()
        out = []
        for item in self.items:
            path = save(item["data"], item["media"])
            out.append({"path": path, "media": item["media"], "data": base64.b64encode(item["data"]).decode()})
        self.items.clear()
        return out

    def _expire(self):
        now = time.time()
        self.items = [i for i in self.items if now - i["at"] < STAGE_TTL]


def content_blocks(images: list[dict], text: str) -> list[dict]:
    """Message content for the model: the pictures first, then the words (with where each is saved)."""
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": i["media"], "data": i["data"]}} for i in images]
    note = ""
    if images:
        where = ", ".join(str(i["path"]) for i in images)
        note = (f"\n[{len(images)} image{'s' if len(images) > 1 else ''} attached by the user"
                f" (saved at {where})]")
    blocks.append({"type": "text", "text": text + note})
    return blocks
