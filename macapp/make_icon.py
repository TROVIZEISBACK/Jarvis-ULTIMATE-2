"""Draw the arc-reactor app icon and write JARVIS.icns (needs Pillow and iconutil)."""
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024


def reactor() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # rounded-square base in the macOS icon grid (824px plate, 100px margin)
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((100, 100, 924, 924), radius=185, fill=(4, 12, 18, 255))
    img.alpha_composite(base)
    c = S / 2
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((c - 300, c - 300, c + 300, c + 300), outline=(41, 211, 255, 255), width=26)
    g.ellipse((c - 120, c - 120, c + 120, c + 120), fill=(140, 235, 255, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(38))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    d.ellipse((c - 300, c - 300, c + 300, c + 300), outline=(120, 230, 255, 255), width=14)
    d.ellipse((c - 250, c - 250, c + 250, c + 250), outline=(41, 160, 200, 255), width=6)
    for i in range(10):  # coil segments
        a0 = i * 36 + 4
        d.arc((c - 228, c - 228, c + 228, c + 228), a0, a0 + 26, fill=(70, 215, 255, 255), width=46)
    for i in range(3):  # triangle spokes
        a = math.radians(i * 120 - 90)
        x, y = c + math.cos(a) * 175, c + math.sin(a) * 175
        d.line((c, c, x, y), fill=(41, 190, 235, 255), width=18)
    d.ellipse((c - 112, c - 112, c + 112, c + 112), fill=(18, 60, 80, 255), outline=(150, 240, 255, 255), width=10)
    d.ellipse((c - 78, c - 78, c + 78, c + 78), fill=(220, 252, 255, 255))
    return img


def main(out: Path):
    art = reactor()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "JARVIS.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            art.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            art.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
