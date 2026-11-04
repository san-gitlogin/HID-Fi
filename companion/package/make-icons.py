#!/usr/bin/env python3
"""The Windows app icon, from the same artwork as the Mac's (package/icon-1024.png).

macOS icons carry a transparent margin (the 1024 artwork is 824 px of mark inside
100 px of nothing); Windows icons fill their square, so this crops the margin and
writes every size Windows asks for: 16 and 20 (title bar, small icons at 100 and
125 %), 24, 32 and 40 (taskbar), 48 and 64 (Explorer, Alt-Tab), 256 (large icons).

    python3 companion/package/make-icons.py      # writes crates/hidfi-companion/assets/app.ico

The .exe carries it as icon 1 (crates/hidfi-companion/build.rs); the app's window and
taskbar button load it from there.
"""
import pathlib
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "icon-1024.png"
OUT = HERE.parent / "crates" / "hidfi-companion" / "assets" / "app.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 256]


def mark():
    im = Image.open(SRC).convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    w, h = box[2] - box[0], box[3] - box[1]
    assert w == h, f"the mark is not square: {box}"
    return im.crop(box)


def main():
    m = mark()
    m.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"{OUT.relative_to(HERE.parent.parent)}: {OUT.stat().st_size} bytes, sizes {SIZES}")


if __name__ == "__main__":
    main()
