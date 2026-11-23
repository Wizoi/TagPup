"""Draw the apps' icons: a paw print on a dog tag, orange for TagPup, teal for TagTuner.

    .venv/Scripts/python.exe tools/make_icons.py

Writes web/common/icons/tagpup.ico and tagtuner.ico (16 to 256 pixels). They are
committed; run this again only to change them. scripts/install_app.py puts them beside
the launchers and gives the shortcuts it makes these icons. It also writes TagPup's at
the two sizes Chrome on Android asks a home-screen page for, beside the phone's page
(web/tagpup/mobile-icon-192.png and -512.png, named by web/tagpup/manifest.webmanifest).

The tag is one plate, a rounded rectangle with a darker rim, hung from a ball chain
whose two strands rise from its hole and leave the top of the square. Up to 32 pixels
the chain would be grey speckle, so those sizes are the plate alone, filling the square. The manifest gives no `purpose`, so Android
shows the icons whole ("any") and crops none, on a white circle with the tag small inside it; so the manifest also
names a "maskable" icon (mobile-icon-maskable-512.png): the app's dark colour to the edges and the tag larger, which the
phone crops to its own shape with no white behind it. (A launcher never shows a clear background for a home-screen page.)
"""

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(ROOT, "web", "common", "icons")
COLOURS = {"tagpup": (232, 131, 58), "tagtuner": (42, 157, 143)}
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
SCALE = 4   # drawn large and scaled down, for smooth edges
PHONE = os.path.join(ROOT, "web", "tagpup")
PHONE_SIZES = (192, 512)
MASKABLE = (15, 23, 42, 255)  # the manifest's theme_color, #0f172a
MASKABLE_GROWTH = 1.0  # the plate fills the icon as the plain one does, with no white circle shrinking it; its corners stay inside a circle
SMALL = 32  # this size and under: the plate alone
CHAIN = (138, 146, 158, 255)  # steel grey, seen on a dark page and a light one
WHITE = (255, 255, 255, 255)
PLATE = (54, 52, 202, 252)  # the plate's box on the 256 grid
HOLE = (128, 80, 11)  # the hole: centre and radius


def box(x0, y0, x1, y1):
    return [v * SCALE for v in (x0, y0, x1, y1)]


def disc(pen, x, y, r, fill):
    pen.ellipse(box(x - r, y - r, x + r, y + r), fill=fill)


def plate(colour):
    """The plate, its rim, its hole and the paw on it, on a clear square."""
    image = Image.new("RGBA", (256 * SCALE, 256 * SCALE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    rim = tuple(int(c * 0.72) for c in colour[:3]) + (255,)
    x0, y0, x1, y1 = PLATE
    pen.rounded_rectangle(box(x0, y0, x1, y1), radius=40 * SCALE, fill=rim)
    pen.rounded_rectangle(box(x0 + 8, y0 + 8, x1 - 8, y1 - 8), radius=32 * SCALE, fill=colour)
    hx, hy, hr = HOLE
    disc(pen, hx, hy, hr + 5, rim)
    disc(pen, hx, hy, hr, (0, 0, 0, 0))
    # The paw: a pad and four toes.
    pen.ellipse(box(95, 172, 161, 226), fill=WHITE)
    for cx, cy in ((84, 156), (111, 130), (145, 130), (172, 156)):
        pen.ellipse(box(cx - 12, cy - 16, cx + 12, cy + 16), fill=WHITE)
    return image


def chain():
    """Two strands of ball chain, from the plate's hole up and out of the square."""
    image = Image.new("RGBA", (256 * SCALE, 256 * SCALE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    hx, hy, _ = HOLE
    step, rise, spread = 12.5, 0.89, 0.46  # a ball every 12.5 units, along a line this steep
    k = 0
    while hy - k * step * rise > -6:
        for side in (-1, 1):
            disc(pen, hx + side * k * step * spread, hy - k * step * rise, 5, CHAIN)
        k += 1
    return image


def draw(colour, size):
    """The icon at one size: the chain behind the plate, seen through its hole; small, the plate alone."""
    tag = plate(colour)
    if size <= SMALL:
        x0, y0, x1, y1 = PLATE
        side = y1 - y0
        left = (x0 + x1 - side) // 2
        return tag.crop(box(left, y0, left + side, y1)).resize((size, size), Image.LANCZOS)
    image = chain()
    image.alpha_composite(tag)
    return image.resize((size, size), Image.LANCZOS)


def draw_maskable(colour, size):
    """The tag on the app's dark colour to every edge, larger than the plain icon, for the phone to crop."""
    whole = size * 2
    side = int(whole * MASKABLE_GROWTH)
    grown = draw(colour, whole).resize((side, side), Image.LANCZOS)
    off = (side - whole) // 2
    image = Image.new("RGBA", (whole, whole), MASKABLE)
    image.alpha_composite(grown.crop((off, off, off + whole, off + whole)))
    return image.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(FOLDER, exist_ok=True)
    for name, colour in COLOURS.items():
        path = os.path.join(FOLDER, name + ".ico")
        images = [draw(colour + (255,), side) for side, _ in SIZES]
        images[-1].save(path, sizes=SIZES, append_images=images[:-1])
        print(path)
    for size in PHONE_SIZES:
        path = os.path.join(PHONE, "mobile-icon-%d.png" % size)
        draw(COLOURS["tagpup"] + (255,), size).save(path, optimize=True)
        print(path)
    path = os.path.join(PHONE, "mobile-icon-maskable-512.png")
    draw_maskable(COLOURS["tagpup"] + (255,), 512).save(path, optimize=True)
    print(path)


if __name__ == "__main__":
    main()
