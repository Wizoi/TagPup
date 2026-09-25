"""Draw the apps' icons: a paw print on a luggage tag, orange for TagPup, teal for TagTuner.

    .venv/Scripts/python.exe tools/make_icons.py

Writes web/common/icons/tagpup.ico and tagtuner.ico (16 to 256 pixels). They are
committed; run this again only to change them. scripts/install_app.py puts them beside
the launchers and gives the shortcuts it makes these icons.
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(ROOT, "web", "common", "icons")
COLOURS = {"tagpup": (232, 131, 58), "tagtuner": (42, 157, 143)}
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
SCALE = 4   # drawn large and scaled down, for smooth edges


def draw(colour):
    n = 256 * SCALE
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)

    def box(x0, y0, x1, y1):
        return [v * SCALE for v in (x0, y0, x1, y1)]

    # The tag: a rounded body with a pointed end on the left, and its hole.
    pen.rounded_rectangle(box(56, 24, 240, 232), radius=36 * SCALE, fill=colour)
    pen.polygon([(v * SCALE) for v in (60, 24, 8, 128, 60, 232)], fill=colour)
    pen.ellipse(box(34, 112, 66, 144), fill=(0, 0, 0, 0))
    # The paw: a pad and four toes.
    white = (255, 255, 255, 255)
    pen.ellipse(box(104, 120, 204, 204), fill=white)
    for x0, y0 in ((86, 88), (118, 58), (160, 58), (192, 88)):
        pen.ellipse(box(x0, y0, x0 + 34, y0 + 44), fill=white)
    return image.resize((256, 256), Image.LANCZOS)


def main():
    os.makedirs(FOLDER, exist_ok=True)
    for name, colour in COLOURS.items():
        path = os.path.join(FOLDER, name + ".ico")
        draw(colour + (255,)).save(path, sizes=SIZES)
        print(path)


if __name__ == "__main__":
    main()
