/* The basemap, in one place.
 *
 * WHY THIS FILE EXISTS AT ALL (1.26.0).
 *
 * The tile URL used to be typed out twice — once in viewer.html, once in
 * calendar.html — and the two had already drifted: the tracker asked for
 * retina tiles and the calendar didn't. When CARTO started demanding an
 * API key, that meant two separate places to fix, and the calendar was
 * the one nobody would have noticed was broken. One definition, two
 * callers, no drift.
 *
 * WHY WE LEFT CARTO.
 *
 * Not the key requirement — the retirement. CARTO's own docs say the
 * raster (PNG) basemaps "are still available, but they now require an API
 * key and are being retired". Wiring in a key would have bought a year.
 *
 * WHAT REPLACED IT.
 *
 * OpenFreeMap: free, no key, no account, no request limit, donation
 * funded. Crucially it serves the standard OpenMapTiles schema, so if it
 * ever disappeared, every style below keeps working against any other
 * OpenMapTiles host — including a self-hosted one. Changing provider is
 * changing TILE_STYLES, not rewriting the map.
 *
 * RASTER TO VECTOR, AND WHAT THAT COSTS.
 *
 * These are vector tiles: the browser is sent road and coastline geometry
 * and draws the map itself, rather than being sent finished pictures. It
 * stays sharp at any zoom on any screen, which matters most at the one
 * moment this app is looked at hardest — an aircraft parked on a stand,
 * zoomed all the way in, where raster tiles turn to mush.
 *
 * The cost is MapLibre GL, which needs WebGL. Every browser since about
 * 2013 has it, but "almost every" is not "every", so there is a raster
 * fallback below. A plain map beats no map.
 */
(function () {
  'use strict';

  // Light and dark are different STYLES, not a light/dark switch on one
  // style. Positron is the pale, low-contrast cartography that lets a
  // coloured flight path sit on top without competing with it; Fiord is
  // the blue-grey dark counterpart. Both are OpenMapTiles styles hosted
  // by OpenFreeMap.
  //
  // Fiord is deliberately not listed on OpenFreeMap's quick-start page —
  // it lives at this URL and is documented in the openfreemap-styles
  // README instead. It works; it is just less advertised.
  var TILE_STYLES = {
    light: 'https://tiles.openfreemap.org/styles/positron',
    dark: 'https://tiles.openfreemap.org/styles/fiord'
  };

  // ODbL requires attribution and it is not optional, so it lives here
  // next to the tiles rather than being left to each caller to remember.
  var ATTRIBUTION =
    '&copy; <a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> ' +
    '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';

  // Last-resort raster, used ONLY when WebGL is missing. Not a second
  // supported basemap — a parachute. Kept low-volume by definition,
  // because it only ever runs on hardware that cannot do the real one.
  var FALLBACK_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';

  // The basemap must paint UNDER the weather radar. Leaflet's built-in
  // tilePane sits at z-index 200 and the radar overlay lives there, so a
  // basemap sharing that pane would be a coin toss decided by which layer
  // was added first. Its own pane underneath makes the order a fact
  // rather than an accident.
  var PANE_NAME = 'basemapPane';
  var PANE_Z = 100;

  // WHY THIS IS NOT JUST `=== 'light' ? light : dark` (moved here from
  // calendar.html, 1.26.0). Settings only ever writes "dark" or "light",
  // so in practice the third branch never runs. It stays because an
  // ABSENT attribute is a different thing from a chosen one: if the
  // template ever renders before settings load, or a future release adds
  // an "auto" option, guessing dark would put a dark map on a light page.
  // Asking the operating system is the better guess and costs nothing.
  function theme() {
    var t = document.documentElement.getAttribute('data-theme');
    if (t === 'light') return 'light';
    if (t === 'dark') return 'dark';
    return (window.matchMedia &&
            window.matchMedia('(prefers-color-scheme: light)').matches)
            ? 'light' : 'dark';
  }

  // Asking the browser directly, rather than sniffing for old phones.
  // Cached because creating a probe context is not free and both pages
  // may ask more than once.
  var _webgl = null;
  function hasWebGL() {
    if (_webgl !== null) return _webgl;
    try {
      var c = document.createElement('canvas');
      _webgl = !!(window.WebGLRenderingContext &&
                  (c.getContext('webgl') || c.getContext('experimental-webgl')));
    } catch (e) {
      _webgl = false;
    }
    return _webgl;
  }

  function ensurePane(map) {
    if (!map.getPane(PANE_NAME)) {
      var p = map.createPane(PANE_NAME);
      p.style.zIndex = PANE_Z;
      // The GL canvas is not an overlay and must never swallow taps meant
      // for a plane marker or the map's own drag handler.
      p.style.pointerEvents = 'none';
    }
    return PANE_NAME;
  }

  /* Add the basemap to a Leaflet map. Returns the layer, or null if
   * neither renderer could be used — callers should treat null as "the
   * map still works, it just has no background" rather than as fatal.
   */
  function add(map, opts) {
    opts = opts || {};
    var attribution = opts.attribution === false ? undefined : ATTRIBUTION;
    var pane = ensurePane(map);

    if (hasWebGL() && typeof L.maplibreGL === 'function') {
      try {
        var layer = L.maplibreGL({
          style: TILE_STYLES[theme()],
          pane: pane,
          attribution: attribution
        });
        layer.addTo(map);
        return layer;
      } catch (e) {
        // Falling through rather than rethrowing. A map with plain tiles
        // is a worse map; a page that threw here is no map at all.
        console.warn('[basemap] MapLibre failed, using raster fallback', e);
      }
    }

    console.warn('[basemap] WebGL unavailable — using raster fallback');
    return L.tileLayer(FALLBACK_URL, {
      maxZoom: 19,
      pane: pane,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
    }).addTo(map);
  }

  window.PT_BASEMAP = {
    add: add,
    styles: TILE_STYLES,
    attribution: ATTRIBUTION,
    paneName: PANE_NAME,
    hasWebGL: hasWebGL
  };
})();
