"""Rebuild vendored rendering maps from the sources credited in assets/README.md.

Usage: python scripts/prepare_earth_textures.py NASA-original.jpg ne_50m_land.geojson
Requires Pillow only for this development step; the application loads saved images.
"""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('night', type=Path)
    parser.add_argument('land', type=Path)
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / 'components/globe/frontend/assets'
    with Image.open(args.night) as source:
        source.convert('RGB').resize((2048, 1024), Image.Resampling.LANCZOS).save(
            target / 'earth-night-2016.jpg', quality=92, optimize=True)
    mask = Image.new('L', (4096, 2048), 255)
    draw = ImageDraw.Draw(mask)
    for feature in json.loads(args.land.read_text(encoding='utf-8'))['features']:
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        for polygon in polygons:
            for index, ring in enumerate(polygon):
                draw.polygon([((lon+180)/360*4096, (90-lat)/180*2048)
                              for lon, lat, *_ in ring], fill=0 if index == 0 else 255)
    mask.resize((2048, 1024), Image.Resampling.LANCZOS).save(target / 'earth-water-mask.png', optimize=True)


if __name__ == '__main__':
    main()
