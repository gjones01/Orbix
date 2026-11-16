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


## Phase 6 surface assets

- `earth-night-2016.jpg`: NASA Earth Observatory **Black Marble 2016**, the 3600x1800
  0.1-degree color map, resized with Lanczos to 2048x1024 and JPEG quality 92.
  Source: https://assets.science.nasa.gov/content/dam/science/esd/eo/images/imagerecords/144000/144898/BlackMarble_2016_01deg.jpg
  Catalogue: https://science.nasa.gov/earth/earth-observatory/earth-at-night/maps/
  NASA imagery is generally not copyrighted in the US; educational/informational use
  follows https://www.nasa.gov/nasa-brand-center/images-and-media/ . Credit NASA Earth
  Observatory / Black Marble team, Suomi NPP VIIRS. This is a historical composite,
  not current city conditions or live imagery.
  Source SHA256: `d87de751a264e4f8ff69c68de5dab9606daee87a6f15ae743c93200743bd7ec1`.
- `earth-water-mask.png`: rasterized from **Natural Earth 1:50m land polygons**,
  public domain: https://www.naturalearthdata.com/about/terms-of-use/ . White is water,
  black is land. Source polygons:
  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson
  Source SHA256: `e874b27a51d146452be360cafb3cc50c86001074a67d534113e6534682f9826b`.
  Polygon exteriors were filled black and holes white on a 4096x2048 white raster,
  then Lanczos-downsampled to 2048x1024. This generalized coastline is for rendering,
  not coastal measurement; it does not describe current sea ice or weather.

Both are north-up equirectangular maps: left edge -180 degrees longitude, top edge
90 degrees latitude. They share the globe's existing geographic UVs. No generated
or fabricated city geography is used. Assets are served locally; no runtime requests
to NASA or Natural Earth are made.
