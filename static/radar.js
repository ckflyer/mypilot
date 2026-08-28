/* The weather radar layer.
 *
 * This is a plain Leaflet WMS layer and nothing more. It used to be a
 * custom tile class that drew every tile to a canvas and rewrote the
 * pixels to hide clear-air noise; that produced a striped colour mess on
 * every setting except "off", and it could not be debugged anywhere but
 * in a real browser looking at real weather.
 *
 * The filtering now happens on the server — see app/radar_proxy.py,
 * which carries the reasoning and has tests against known images. All
 * that is left here is pointing at it and passing the threshold.
 *
 * `min` IS A WMS PARAMETER, NOT A LAYER OPTION.
 *
 * Leaflet decides which is which by checking what is NOT already named
 * in this.options: anything it recognises as a layer option is
 * deliberately kept out of the query string. That is exactly how 1.27.0
 * broke the radar — layers/format/transparent were declared as options
 * and silently vanished from every request. So `min`, like them, is
 * passed at construction and never added to an options block, or the
 * threshold stops reaching the server and the filter appears to do
 * nothing.
 */
(function () {
  'use strict';

  // Same-origin: the server fetches from IEM, filters, and serves the
  // result. See app/radar_proxy.py.
  var URL = '/radar/wms';

  // Slider positions, in order. `min` is the threshold handed to the
  // server; `label` is what sits under the slider. Kept as one list so
  // the control and the values cannot drift apart, and so adding a stop
  // is editing one array.
  var STOPS = [
    { min: 0, label: 'Show everything' },
    { min: 2, label: 'Hide light returns' },
    { min: 3, label: 'Moderate and above' },
    { min: 4, label: 'Heavy only' }
  ];

  window.PT_RADAR = {
    STOPS: STOPS,

    create: function (opts) {
      opts = opts || {};
      return L.tileLayer.wms(URL, {
        // WMS query parameters — see the note above before moving any of
        // these into an options block.
        layers: 'nexrad-n0q',
        format: 'image/png',
        transparent: true,
        min: opts.min || 0,
        // Genuine Leaflet options. These belong here and must NOT reach
        // the query string.
        opacity: typeof opts.opacity === 'number' ? opts.opacity : 0.55,
        attribution: 'Radar &copy; IEM NEXRAD'
      });
    },

    /* Change the threshold on a live layer. setParams rewrites the query
     * and redraws, which is Leaflet reloading tiles — something it
     * already does well, and the reason this is no longer a custom tile
     * class. */
    setMin: function (layer, min) {
      if (layer && layer.setParams) layer.setParams({ min: min });
    }
  };
})();
