"""Weather radar tiles, fetched and filtered on the server.

WHY THIS MOVED OFF THE BROWSER (1.28.0).
========================================

1.27.x filtered the clear-air noise in the browser: draw each tile to a
canvas, read the pixels back, hide the light returns. It produced a
coloured striped mess over the whole map on every setting except "off",
and there was no way to debug it from here — the failure needed a real
browser and real tiles to reproduce.

Doing it on the server fixes that class of problem rather than one
instance of it:

  * It is TESTABLE. tests_radar_filter.py feeds known images through the
    exact function the route calls and checks the output pixels. The
    browser version could only be reasoned about.
  * No canvas, so no cross-origin pixel-reading question at all. That was
    an unknown hanging over the whole feature — whether IEM would even
    permit it — and it is now moot.
  * The threshold becomes a URL parameter, so changing it is Leaflet
    reloading tiles, which it already knows how to do. No custom tile
    class, no redraw bookkeeping.

The cost is that radar tiles pass through this box instead of going
straight to IEM. For a handful of family members that is a few hundred
kilobytes an hour, and the cache below means a tile is fetched once no
matter how many people are looking.

WHAT IS BEING FILTERED.
=======================

NEXRAD in clear-air mode sees insects, birds, dust and the temperature
inversions that bend its beam into the ground. That returns a real signal
around 5-20 dBZ and paints the map blue and turquoise on a day with
nothing in the sky. It is the radar working correctly and it is useless
to someone asking whether there is weather on a route.

Classification is by HUE, not by dBZ, and that is deliberate. IEM does
not publish the palette alongside the service, so a colour-to-dBZ table
would be guesswork wearing a number. What is reliable is the ORDER of
the ramp, which is a design property of every reflectivity palette in
meteorology:

    blue -> cyan -> green -> yellow -> orange -> red -> magenta
    light                                              extreme

Magenta is the exception that makes this a table rather than a
comparison: its hue wraps back past blue, so "bigger hue is worse
weather" would file the most violent returns on the map as the lightest.
"""

import io
import time
import threading
from typing import Dict, Optional, Tuple

import requests
from PIL import Image, ImageChops

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi"

# Bands of the reflectivity ramp in hue degrees, with the level each maps
# to. Level rises with intensity; see the module docstring for why
# magenta sits at the top despite its hue.
_HUE_BANDS = (
    (40, 4),    # red
    (80, 3),    # yellow / orange
    (160, 2),   # green
    (260, 1),   # blue / cyan  <- the clear-air noise
    (330, 5),   # magenta / white: hail, extreme
    (361, 4),   # wraps back to red
)

# Below this chroma a pixel has no meaningful hue. In this ramp those are
# the faint speckle at the bottom of the scale, so they count as lightest.
_GREY_CHROMA = 18

_TIMEOUT = 8
_CACHE_TTL = 240          # radar updates every 5 minutes; expire just under
_CACHE_MAX = 400          # tiles, not bytes — a tile is tens of KB


def _level_for_hue(deg: float) -> int:
    for limit, level in _HUE_BANDS:
        if deg < limit:
            return level
    return 1


def _keep_mask_lut(min_level: int):
    """LUT over the 0-255 hue byte: 255 to keep the pixel, 0 to drop it.

    PIL stores hue as a byte covering 0-360 degrees, so the scale factor
    is 360/256 rather than 360/255 — the byte is a bucket index, not a
    fraction of the range.
    """
    return [255 if _level_for_hue(i * 360.0 / 256.0) >= min_level else 0
            for i in range(256)]


def filter_tile(png: bytes, min_level: int) -> bytes:
    """Hide reflectivity below min_level. Returns PNG bytes.

    min_level <= 1 is a no-op and returns the input untouched, because
    level 1 is the lowest band there is — asking to keep it and above is
    asking for everything.
    """
    if min_level <= 1:
        return png

    img = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = img.getchannel("A")

    hsv = img.convert("RGB").convert("HSV")
    h, s, v = hsv.split()

    # PIL's S is (max-min)/max scaled to a byte and V is max, so S*V/255
    # recovers (max-min) — the chroma — which is what distinguishes a
    # coloured return from grey speckle. ImageChops.multiply divides by
    # 255 for us.
    chroma = ImageChops.multiply(s, v)
    is_grey = chroma.point(lambda c: 255 if c < _GREY_CHROMA else 0, mode="L")

    by_hue = h.point(_keep_mask_lut(min_level), mode="L")

    # Grey pixels are level 1, so at any threshold above 1 they go. Where
    # a pixel is grey, take that verdict; everywhere else use the hue.
    keep = Image.composite(Image.new("L", img.size, 0), by_hue, is_grey)

    # 255 keeps the original alpha, 0 erases it. Pixels already
    # transparent stay transparent either way.
    img.putalpha(ImageChops.multiply(alpha, keep))

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=False)
    return out.getvalue()


# --- Cache ------------------------------------------------------------
# Keyed by the upstream query plus the threshold. Radar imagery is the
# same for everyone looking at the same place, so one fetch serves every
# viewer, and the filtering — the only expensive part — happens once per
# distinct threshold rather than once per request.

_cache: Dict[Tuple[str, int], Tuple[float, bytes]] = {}
_lock = threading.Lock()


def _cache_get(key) -> Optional[bytes]:
    with _lock:
        hit = _cache.get(key)
        if not hit:
            return None
        stamped, body = hit
        if time.time() - stamped > _CACHE_TTL:
            _cache.pop(key, None)
            return None
        return body


def _cache_put(key, body: bytes) -> None:
    with _lock:
        if len(_cache) >= _CACHE_MAX:
            # Cheap eviction: drop whatever is oldest. A strict LRU would
            # need access bookkeeping this does not earn.
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[key] = (time.time(), body)


def get_tile(query: dict, min_level: int) -> Optional[bytes]:
    """Fetch one radar tile from IEM and filter it. None if unavailable.

    None means "no radar right now", which the caller turns into an empty
    tile. A radar outage must never take the map down with it.
    """
    key = (tuple(sorted(query.items())).__str__(), min_level)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    try:
        r = requests.get(IEM_URL, params=query, timeout=_TIMEOUT)
        if r.status_code != 200 or not r.content:
            return None
        # MapServer reports failures as an XML exception document with a
        # 200, so trust the content type rather than the status code.
        if "image" not in r.headers.get("Content-Type", ""):
            return None
        body = filter_tile(r.content, min_level)
    except Exception:
        return None
    _cache_put(key, body)
    return body
