/* The weather radar, and the noise filter that was the point of it.
 *
 * WHAT THE CYAN IS.
 *
 * NEXRAD in clear-air mode is sensitive enough to see insects, birds,
 * dust and the temperature inversions that bend its beam into the
 * ground. All of that returns a real signal at roughly 5-20 dBZ and
 * paints the map blue and turquoise on a day with nothing in the sky.
 * It is not an error in the data — it is the radar working — but it is
 * useless to someone asking whether there is weather on a route, and on
 * a dry night it can cover whole states.
 *
 * WHY THIS IS FILTERED HERE AND NOT ASKED FOR.
 *
 * Every free radar source was checked for a sensitivity control and none
 * has one:
 *
 *   - IEM (this source) serves a bare MapServer raster layer with no
 *     style classes and no threshold parameter. It hands over an
 *     already-coloured PNG and that is the whole interface.
 *   - NOAA's own ArcGIS radar service reports 4-band 8-bit pixels, which
 *     means it too is already coloured — the dBZ numbers are gone before
 *     the image is served, so even its raster-function machinery has
 *     nothing left to threshold.
 *   - RainViewer does expose raw dBZ per pixel, but its free tier caps
 *     out at zoom 7 and it explicitly declines to guarantee the data
 *     stays available. Not worth swapping a working US source for.
 *
 * So the filtering happens on the pixels, after they arrive.
 *
 * WHY BY HUE, RATHER THAN BY dBZ.
 *
 * The honest reason: IEM does not publish the exact palette alongside
 * the service, and a filter built on a guessed colour-to-dBZ table would
 * hide the wrong things while displaying a confident number. What IS
 * reliable is the ORDER of the ramp, which is a deliberate design
 * property of every reflectivity palette in meteorology and has not
 * changed in decades:
 *
 *     blue -> cyan -> green -> yellow -> orange -> red -> magenta
 *     light                                              extreme
 *
 * Hue is therefore a sound proxy for intensity, and it degrades
 * gracefully: if IEM ever retunes the exact shades, blue stays light and
 * red stays severe, and this keeps working. The controls are labelled in
 * those terms — "light", "moderate", "heavy" — instead of dBZ figures
 * this cannot actually justify.
 */
(function () {
  'use strict';

  var WMS_URL = 'https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi';

  // Bands of the reflectivity ramp, in hue degrees. Level rises with
  // intensity. Magenta sits at the TOP of the ramp despite its hue
  // wrapping back past blue, which is why this is a table and not a
  // comparison — a naive "bigger hue is bigger weather" test would file
  // the most violent returns on the map as the lightest.
  var LEVELS = [
    { max: 40,  level: 4 },   // red
    { max: 80,  level: 3 },   // yellow / orange
    { max: 160, level: 2 },   // green
    { max: 260, level: 1 },   // blue / cyan  <- the clear-air noise
    { max: 330, level: 5 },   // magenta / white: hail, extreme
    { max: 361, level: 4 }    // wraps to red
  ];

  function hueLevel(r, g, b) {
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    // Near-grey pixels have no meaningful hue. In this ramp they are the
    // faint speckle at the bottom, so they count as the lightest band.
    if (d < 18) return 1;
    var h;
    if (mx === r)      h = 60 * (((g - b) / d) % 6);
    else if (mx === g) h = 60 * (((b - r) / d) + 2);
    else               h = 60 * (((r - g) / d) + 4);
    if (h < 0) h += 360;
    for (var i = 0; i < LEVELS.length; i++) {
      if (h < LEVELS[i].max) return LEVELS[i].level;
    }
    return 1;
  }

  // WMS PARAMETERS ARE NOT LAYER OPTIONS, AND LEAFLET DECIDES WHICH IS
  // WHICH BY LOOKING AT THIS BLOCK (fixed in 1.27.1).
  //
  // L.TileLayer.WMS.initialize does, in effect:
  //
  //     for (key in passedOptions)
  //         if (!(key in this.options)) wmsParams[key] = passedOptions[key];
  //
  // Anything already named in `options` is treated as a Leaflet setting and
  // deliberately kept OUT of the query string. 1.27.0 declared layers,
  // format and transparent here, which read like sensible defaults and
  // instead deleted them from every request: the map asked IEM for
  // layers="" as an opaque JPEG and got nothing back. The radar simply
  // stopped appearing, with no error anywhere.
  //
  // So ONLY genuine Leaflet options belong in this block. The WMS
  // parameters are passed at construction in create() below, where they
  // are absent from this.options and therefore reach the server.
  // opacity, attribution and crossOrigin ARE real TileLayer options and
  // are correct here — they must not leak into the URL.
  var RadarLayer = L.TileLayer.WMS.extend({
    options: {
      attribution: 'Radar &copy; IEM NEXRAD',
      crossOrigin: 'anonymous',
      minLevel: 0            // 0 = show everything, unfiltered
    },

    // Set once if the browser refuses to let us read the pixels back.
    // See _drawTile — this is the fallback flag, not an error state.
    _blocked: false,

    setMinLevel: function (n) {
      this.options.minLevel = n;
      // Tiles are filtered as they are drawn, so a threshold change means
      // redrawing what is already on screen. redraw() re-runs createTile
      // for every visible tile; the images themselves come from the HTTP
      // cache, so this costs no extra requests.
      if (this._map) this.redraw();
      return this;
    },

    isFilterAvailable: function () { return !this._blocked; },

    createTile: function (coords, done) {
      // Unfiltered, or already known to be unreadable: hand back the
      // plain <img> tile Leaflet would have made anyway. The radar keeps
      // working; it just is not filtered.
      if (this.options.minLevel <= 0 || this._blocked) {
        return L.TileLayer.WMS.prototype.createTile.call(this, coords, done);
      }

      var tile = document.createElement('canvas');
      var size = this.getTileSize();
      tile.width = size.x;
      tile.height = size.y;

      var img = new Image();
      // Without this the canvas is "tainted" and reading pixels throws.
      // It only works if the server allows cross-origin reads; if it
      // does not, _drawTile catches that and falls back for good.
      img.crossOrigin = 'anonymous';
      var self = this;

      img.onload = function () {
        try {
          self._drawTile(tile, img, size);
          done(null, tile);
        } catch (e) {
          // Tainted canvas. Give up on filtering ENTIRELY rather than
          // per tile, so the map does not end up a patchwork of filtered
          // and unfiltered squares, and tell the page once so it can say
          // so rather than leaving a control that silently does nothing.
          self._blocked = true;
          console.warn('[radar] pixels unreadable, filter unavailable', e);
          if (self._map) self._map.fire('radarfilterblocked');
          self.redraw();
          done(null, tile);
        }
      };
      img.onerror = function () { done(null, tile); };
      img.src = this.getTileUrl(coords);
      return tile;
    },

    _drawTile: function (tile, img, size) {
      var ctx = tile.getContext('2d');
      ctx.drawImage(img, 0, 0, size.x, size.y);
      // Throws on a tainted canvas — this is the line the try/catch in
      // createTile exists for.
      var data = ctx.getImageData(0, 0, size.x, size.y);
      var px = data.data, min = this.options.minLevel;
      for (var i = 0; i < px.length; i += 4) {
        if (px[i + 3] === 0) continue;              // already transparent
        if (hueLevel(px[i], px[i + 1], px[i + 2]) < min) px[i + 3] = 0;
      }
      ctx.putImageData(data, 0, 0);
    }
  });

  window.PT_RADAR = {
    create: function (opts) {
      opts = opts || {};
      return new RadarLayer(WMS_URL, {
        // These three are WMS QUERY PARAMETERS, not layer options, and
        // must be passed here rather than defaulted in the options block
        // above — see the comment on RadarLayer for what happens if they
        // move.
        layers: 'nexrad-n0q',
        format: 'image/png',
        transparent: true,
        opacity: typeof opts.opacity === 'number' ? opts.opacity : 0.55,
        minLevel: opts.minLevel || 0
      });
    },
    // Labels live with the levels they describe so the popover and the
    // filter cannot drift apart.
    CHOICES: [
      { value: 0, label: 'Show everything' },
      { value: 2, label: 'Hide light returns' },
      { value: 3, label: 'Moderate and above' },
      { value: 4, label: 'Heavy only' }
    ]
  };
})();
