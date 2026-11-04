# Vendored front-end assets

These files are served to the Globe.gl iframe. They are committed so the app works
without a CDN - important for a live demo and for offline use.

## Earth textures

| File | Content | Source | Licence |
|---|---|---|---|
| `earth-blue-marble.jpg` | NASA Blue Marble Next Generation, land/ocean/ice at 4096x2048 | NASA Visible Earth, redistributed in the [three-globe](https://github.com/vasturiano/three-globe) example assets (`unpkg.com/three-globe/example/img/`) | **Public domain** (NASA imagery is not copyrighted; NASA requests credit) |
| `earth-topology.png` | Global elevation/bathymetry, used as a bump map | Same three-globe example assets, derived from NASA/NOAA elevation data | **Public domain** |

Attribution shown in the globe's HUD: *"Earth imagery: NASA Blue Marble (public
domain) · Globe.gl / Three.js"*.

To refresh the imagery, replace the files keeping the same names; nothing else
needs to change.

## JavaScript library

| File | Version | Licence |
|---|---|---|
| `../vendor/globe.gl.min.js` | Globe.gl **2.34.4** (bundles three.js r1xx), fetched from `unpkg.com/globe.gl@2.34.4/dist/globe.gl.min.js` | MIT |

Pinning the version (rather than loading from a CDN at runtime) means a classroom
network problem cannot leave the app showing an empty box.

## Why these are not git-ignored

They are small (~3.2 MB total), immutable, and required for the application's
primary feature. Bulk, frequently changing data (CelesTrak groups) is ignored
instead; see the repository `.gitignore`.
