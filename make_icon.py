"""
Build the app icon from Images/Logo.jpg.

Crops the logo to its drawing, centres it on a white rounded square (the
standard macOS icon shape, with a soft shadow), and writes:
  Images/icon.png   1024x1024 (also used as the Dock icon by desktop.py)
  Images/Arete.icns all sizes, for Arete.app/Contents/Resources
  Images/splash.png the cleaned drawing, for the loading screen

Run with:   .venv/bin/python make_icon.py
"""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).parent
LOGO = HERE / "Images" / "Logo.jpg"
PNG = HERE / "Images" / "icon.png"
ICNS = HERE / "Images" / "Arete.icns"
SPLASH = HERE / "Images" / "splash.png"

SIZE = 1024
INSET = 100          # Apple's icon grid: the shape is 824 px inside a 1024 canvas
RADIUS = 185
DRAWING = 680        # max width/height of the logo drawing inside the shape


def clean_logo():
    """The logo drawing with its white margin cropped off and JPEG specks removed."""
    logo = Image.open(LOGO).convert("RGB")
    # JPEG leaves faint off-white specks in the background: snap near-white to white,
    # then crop away the margin around the drawing.
    logo = logo.point(lambda v: 255 if v > 232 else v)
    diff = ImageChops.difference(logo, Image.new("RGB", logo.size, "white")).convert("L")
    return logo.crop(diff.point(lambda v: 255 if v > 40 else 0).getbbox())


def build_splash():
    """The cleaned drawing on white, for the loading screen (splash.html)."""
    logo = clean_logo()
    logo.thumbnail((900, 900), Image.LANCZOS)
    logo.save(SPLASH)


def build_png():
    logo = clean_logo()
    logo.thumbnail((DRAWING, DRAWING), Image.LANCZOS)

    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    box = (INSET, INSET, SIZE - INSET, SIZE - INSET)

    # Soft drop shadow under the shape.
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 12, box[2], box[3] + 12), RADIUS, fill=(0, 0, 0, 70))
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))

    # White rounded square with a hairline edge, then the drawing in the middle.
    ImageDraw.Draw(icon).rounded_rectangle(box, RADIUS, fill="white", outline=(0, 0, 0, 30), width=2)
    x = (SIZE - logo.width) // 2
    y = (SIZE - logo.height) // 2
    icon.paste(logo, (x, y))

    icon.save(PNG)
    return icon


def build_icns(icon):
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Arete.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            icon.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            icon.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)], check=True)


if __name__ == "__main__":
    build_icns(build_png())
    build_splash()
    print(f"Wrote {PNG.name}, {ICNS.name} and {SPLASH.name}")
