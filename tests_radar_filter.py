"""The radar clear-air filter, checked against known pixels.

This exists because the browser-side version of this filter could only be
reasoned about, and the reasoning was wrong: it shipped a striped colour
mess and nobody could tell why without a real browser and real weather.
Moving it to the server was mostly about being able to write this file.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from app.radar_proxy import filter_tile, _level_for_hue

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if not ok else ""))


# Representative colours from the NEXRAD reflectivity ramp, lightest to
# heaviest, with the band each belongs to.
RAMP = [
    ("light blue ~10dBZ", (4, 233, 231), 1),
    ("blue ~15dBZ", (1, 159, 244), 1),
    ("dark blue ~20dBZ", (3, 0, 244), 1),
    ("green ~25dBZ", (2, 253, 2), 2),
    ("dark green ~35dBZ", (0, 142, 0), 2),
    ("yellow ~40dBZ", (253, 248, 2), 3),
    ("orange ~50dBZ", (253, 149, 0), 4),
    ("red ~55dBZ", (253, 0, 0), 4),
    ("dark red ~65dBZ", (188, 0, 0), 4),
    ("magenta ~70dBZ", (248, 0, 253), 5),
    ("purple ~75dBZ", (152, 84, 198), 5),
    ("grey speckle", (120, 120, 122), 1),
]


def _strip():
    """One opaque pixel per ramp colour, plus a transparent one on the end."""
    img = Image.new("RGBA", (len(RAMP) + 1, 1), (0, 0, 0, 0))
    for i, (_, rgb, _) in enumerate(RAMP):
        img.putpixel((i, 0), rgb + (255,))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_the_ramp_is_ordered():
    """Hue must rank the ramp lightest-to-heaviest, or a threshold means
    nothing. Magenta is the one that catches a naive implementation: its
    hue wraps back past blue, so a plain comparison files the most
    violent returns on the map as the lightest."""
    levels = [_level_for_hue(h) for h in (200, 220, 120, 60, 20, 300)]
    check("blue and cyan are the lightest band",
          levels[0] == 1 and levels[1] == 1, str(levels))
    check("green sits above blue", levels[2] == 2, str(levels))
    check("yellow above green", levels[3] == 3, str(levels))
    check("red above yellow", levels[4] == 4, str(levels))
    check("magenta is the TOP band, not the bottom", levels[5] == 5, str(levels))


def test_each_threshold_keeps_exactly_what_it_should():
    png = _strip()
    for min_level in (0, 2, 3, 4):
        out = Image.open(io.BytesIO(filter_tile(png, min_level))).convert("RGBA")
        for i, (name, _, level) in enumerate(RAMP):
            kept = out.getpixel((i, 0))[3] > 0
            want = True if min_level <= 1 else level >= min_level
            check(f"min={min_level}: {name} {'kept' if want else 'hidden'}",
                  kept == want)


def test_transparency_survives():
    """A pixel with no radar in it must never become visible. Sky that
    grows weather when you turn a filter ON is worse than no filter."""
    png = _strip()
    last = len(RAMP)
    for min_level in (0, 2, 3, 4):
        out = Image.open(io.BytesIO(filter_tile(png, min_level))).convert("RGBA")
        check(f"min={min_level}: empty sky stays empty",
              out.getpixel((last, 0))[3] == 0)


def test_off_is_a_passthrough():
    """Level 1 is the lowest band there is, so asking to keep it and above
    is asking for everything — and must not cost a decode and re-encode."""
    png = _strip()
    check("min=0 returns the bytes untouched", filter_tile(png, 0) is png)
    check("min=1 returns the bytes untouched", filter_tile(png, 1) is png)


def test_the_image_survives_a_round_trip():
    """Size and mode must come back unchanged. A filter that quietly
    resizes a tile is how you get a striped mess on the map."""
    png = _strip()
    src = Image.open(io.BytesIO(png))
    out = Image.open(io.BytesIO(filter_tile(png, 2)))
    check("tile keeps its dimensions", out.size == src.size, f"{out.size} vs {src.size}")
    check("tile is still RGBA", out.convert("RGBA").mode == "RGBA")


def main():
    test_the_ramp_is_ordered()
    test_each_threshold_keeps_exactly_what_it_should()
    test_transparency_survives()
    test_off_is_a_passthrough()
    test_the_image_survives_a_round_trip()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
