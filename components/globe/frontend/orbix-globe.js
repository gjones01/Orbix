/**
 * Orbix globe front-end.
 *
 * Responsibilities, and deliberately nothing more:
 *   1. Talk to Streamlit over the custom-component protocol (v1).
 *   2. Render an Earth and the scene payload that Python sends.
 *
 * All orbital mechanics happen in Python (src/orbital/propagation.py). This file
 * receives latitude/longitude in degrees and altitudes *already scaled to globe
 * radii*, so it never converts between Earth-fixed and inertial frames and never
 * does celestial maths.
 *
 * Frame note: the trajectory points are Earth-fixed (ITRS) geodetic positions for
 * the next orbital period, so they are drawn in the same frame as the rotating
 * globe. They drift westward pass-over-pass because the Earth turns underneath
 * the orbit - that is correct, not a rendering bug.
 */
(function () {
  "use strict";

  // ------------------------------------------------------------------ //
  // 1. Streamlit component bridge (protocol version 1)
  // ------------------------------------------------------------------ //
  var Bridge = (function () {
    var embedded = window.parent !== window;

    function post(type, extra) {
      if (!embedded) return;
      var message = Object.assign({ isStreamlitMessage: true, type: type }, extra || {});
      window.parent.postMessage(message, "*");
    }

    return {
      embedded: embedded,
      ready: function () {
        post("streamlit:componentReady", { apiVersion: 1 });
      },
      setHeight: function (px) {
        post("streamlit:setFrameHeight", { height: px });
      },
      setValue: function (value) {
        post("streamlit:setComponentValue", { value: value, dataType: "json" });
      },
      onRender: function (handler) {
        window.addEventListener("message", function (event) {
          var data = event.data;
          if (!data || typeof data !== "object") return;
          if (data.type !== "streamlit:render") return;
          handler(data.args || {});
        });
      },
    };
  })();

  // ------------------------------------------------------------------ //
  // 2. DOM helpers
  // ------------------------------------------------------------------ //
  var statusEl = document.getElementById("status");
  var statusTextEl = document.getElementById("status-text");
  var statusCardEl = statusEl.querySelector(".status-card");

  function showStatus(message, detail) {
    statusCardEl.classList.toggle("error", Boolean(detail));
    statusTextEl.innerHTML = detail
      ? message + "<br /><code>" + String(detail) + "</code>"
      : message;
    statusEl.classList.remove("hidden");
  }

  function hideStatus() {
    statusEl.classList.add("hidden");
  }

  // ------------------------------------------------------------------ //
  // 3. Globe
  // ------------------------------------------------------------------ //
  var state = {
    globe: null,
    globeReady: false,
    framed: false,
    payload: null,
    lastEvent: null,
  };

  var MARKER_COLOR = "#ffcc66";
  var TRACK_COLOR = "rgba(87, 176, 232, 0.78)";

  function maybeHideStatus() {
    // Hide as soon as the scene has been applied. Waiting for onGlobeReady would
    // keep a veil over the globe while the texture streams in, which is worse.
    if (state.payload) hideStatus();
  }

  function initGlobe() {
    if (typeof Globe !== "function") {
      showStatus(
        "The 3D engine could not be loaded.",
        "components/globe/frontend/vendor/globe.gl.min.js is missing or blocked"
      );
      return false;
    }

    try {
      var globe = Globe({ rendererConfig: { antialias: true, alpha: true } })(
        document.getElementById("globe")
      );

      globe
        // A transparent canvas lets the CSS star field and vignette show through.
        .backgroundColor("rgba(0,0,0,0)")
        .globeImageUrl("./assets/earth-blue-marble.jpg")
        .bumpImageUrl("./assets/earth-topology.png")
        .atmosphereColor("#4a9be0")
        .atmosphereAltitude(0.16)
        // markers: positioned at true altitude, sized in globe radii
        .pointLat("lat")
        .pointLng("lng")
        .pointAltitude("alt")
        .pointRadius("radius")
        .pointColor("color")
        .pointLabel("label")
        .pointsMerge(false)
        .onPointClick(handlePointClick)
        // name tag above the marker
        .labelLat("lat")
        .labelLng("lng")
        .labelText("text")
        .labelColor("color")
        .labelSize("size")
        .labelDotRadius("dot")
        .labelAltitude("alt")
        .labelResolution(2)
        // radar-style rings under the satellite
        .ringLat("lat")
        .ringLng("lng")
        .ringMaxRadius("maxR")
        .ringPropagationSpeed("speed")
        .ringRepeatPeriod("period")
        .ringColor(function () {
          return function (t) {
            return "rgba(88, 182, 255, " + (0.55 * (1 - t)) + ")";
          };
        })
        // trajectory + altitude leader line
        .pathPoints("points")
        .pathPointLat("lat")
        .pathPointLng("lng")
        .pathPointAlt("alt")
        .pathColor("color")
        .pathStroke("stroke")
        .pathTransitionDuration(0)
        // Fires once the Earth texture has loaded (or failed to).
        .onGlobeReady(function () {
          state.globeReady = true;
          maybeHideStatus();
        });

      var material = globe.globeMaterial();
      material.shininess = 9;
      material.specular.setRGB(0.045, 0.07, 0.11);
      material.bumpScale = 0.28;

      var controls = globe.controls();
      controls.enableDamping = true;
      controls.dampingFactor = 0.07;
      controls.rotateSpeed = 0.45;
      controls.zoomSpeed = 0.7;
      controls.minDistance = 118;   // globe radius is 100 scene units
      controls.maxDistance = 720;
      controls.enablePan = false;
      controls.autoRotate = false;

      state.globe = globe;
      // Debug handle: lets an automated check (and the developer console) inspect
      // what the renderer actually received. Not used by the app itself.
      window.__orbix = state;
    } catch (error) {
      showStatus("The 3D engine failed to start.", error && error.message);
      return false;
    }

    // Safety net: never leave the loading veil up forever.
    window.setTimeout(function () {
      if (!state.payload) hideStatus();
    }, 6000);

    return true;
  }

  function resizeGlobe(height) {
    if (!state.globe) return;
    var host = document.getElementById("globe");
    var width = host.clientWidth || window.innerWidth;
    var pixels = height || host.clientHeight || 680;
    state.globe.width(width).height(pixels);
  }

  function handlePointClick(point) {
    if (!point || typeof point !== "object") return;
    var event = {
      event: "select",
      norad_id: point.norad_id,
      name: point.name,
      at: new Date().toISOString(),
    };
    state.lastEvent = event;
    // Two-way channel: this makes Python re-run with the value available via the
    // component's return value. Step E will use it for real selection tools.
    Bridge.setValue(event);
  }

  function applyScene(payload) {
    var satellite = payload.satellite;
    var track = payload.track || { points: [] };
    var globe = state.globe;

    // The marker sits at its true altitude, which is only ~6.5% of a globe radius -
    // a fraction of a pixel at normal zoom. Visibility therefore comes from a large
    // translucent halo, a bright core, surface rings and a label, all anchored to
    // the exact propagated position. The marker itself is never raised.
    var markers = [];
    if (satellite) {
      var markerBase = {
        lat: satellite.lat,
        lng: satellite.lon,
        alt: satellite.altitude_ratio,
        norad_id: satellite.norad_id,
        name: satellite.name,
        label:
          "<b>" + satellite.name + "</b><br />NORAD " + satellite.norad_id +
          "<br />" + satellite.altitude_km.toFixed(1) + " km altitude" +
          "<br />" + satellite.lat.toFixed(4) + "\u00b0, " +
          satellite.lon.toFixed(4) + "\u00b0",
      };
      markers.push(
        Object.assign({}, markerBase, {
          radius: satellite.marker_radius * 2.4,
          color: "rgba(255, 204, 102, 0.16)",
        }),
        Object.assign({}, markerBase, {
          radius: satellite.marker_radius * 1.5,
          color: "rgba(255, 214, 130, 0.42)",
        }),
        Object.assign({}, markerBase, {
          radius: satellite.marker_radius,
          color: MARKER_COLOR,
        })
      );
    }

    var labels = satellite
      ? [
          {
            lat: satellite.lat,
            lng: satellite.lon,
            alt: satellite.altitude_ratio + 0.02,
            text: satellite.short_name,
            color: "#ffe0a3",
            size: 1.15,
            dot: 0,
          },
        ]
      : [];

    var rings = satellite
      ? [
          {
            lat: satellite.lat,
            lng: satellite.lon,
            maxR: 6.5,      // degrees of arc on the surface
            speed: 1.3,
            period: 1600,
          },
        ]
      : [];

    var paths = [];
    if (track.points && track.points.length > 1) {
      var trackPoints = track.points.map(function (p) {
        return { lat: p[0], lng: p[1], alt: p[2] };
      });
      // Two overlapping strokes give a soft glow without a post-processing pass.
      paths.push(
        { points: trackPoints, color: "rgba(87, 176, 232, 0.18)", stroke: 3.6 },
        { points: trackPoints, color: TRACK_COLOR, stroke: 1.05 }
      );
    }
    if (satellite) {
      // Vertical leader from the sub-satellite point up to the marker, so the true
      // altitude reads visually without floating the marker.
      paths.push({
        points: [
          { lat: satellite.lat, lng: satellite.lon, alt: 0 },
          { lat: satellite.lat, lng: satellite.lon, alt: satellite.altitude_ratio },
        ],
        color: "rgba(255, 204, 102, 0.30)",
        stroke: 0.35,
      });
    }

    globe
      .pointsData(markers)
      .labelsData(labels)
      .ringsData(rings)
      .pathsData(paths);

    if (!state.framed && satellite) {
      // Only frame the camera on the first scene; later Streamlit reruns must not
      // yank the view away from wherever the user has rotated to.
      globe.pointOfView(
        { lat: satellite.lat, lng: satellite.lon, altitude: 1.75 },
        900
      );
      state.framed = true;
    }

    maybeHideStatus();
  }

  function handleRender(args) {
    var height = typeof args.height === "number" ? args.height : 680;
    Bridge.setHeight(height);
    resizeGlobe(height);

    if (!state.globe && !initGlobe()) return;

    var payload = args.payload;
    if (!payload || !payload.satellite) {
      showStatus(
        "No orbital data was supplied to the globe.",
        "Python sent an empty scene payload"
      );
      return;
    }

    state.payload = payload;
    try {
      applyScene(payload);
    } catch (error) {
      showStatus("The scene could not be rendered.", error && error.message);
    }
  }

  // ------------------------------------------------------------------ //
  // 4. Boot
  // ------------------------------------------------------------------ //
  Bridge.onRender(handleRender);
  // The protocol requires componentReady before any other message.
  Bridge.ready();
  Bridge.setHeight(document.body.clientHeight || 680);

  window.addEventListener("resize", function () {
    if (!state.globe) return;
    state.globe.width(document.getElementById("globe").clientWidth);
    state.globe.height(document.getElementById("globe").clientHeight);
  });
})();

