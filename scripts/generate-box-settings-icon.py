#!/usr/bin/env python3
"""Draw DashPlay's original box/settings mark. No external artwork, fonts or network inputs.

Requires Pillow (tested with 12.3). Usage: python3 scripts/generate-box-settings-icon.py [OUT.png]
The receiver resource keeps its historical filename for compatibility; see assets/ICON-SOURCE.md.
"""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw


def render():
    scale = 4
    image = Image.new('RGB', (180 * scale, 180 * scale), '#146b76')
    draw = ImageDraw.Draw(image)
    def box(coords, radius, fill):
        draw.rounded_rectangle(tuple(int(v * scale) for v in coords), radius=radius * scale, fill=fill)
    # A physical receiver enclosure, with controls cut into its front panel.
    box((28, 35, 152, 145), 22, '#f4f8f8')
    box((37, 44, 143, 136), 14, '#146b76')
    for y, x in [(68, 73), (109, 108)]:
        box((53, y - 4, 127, y + 4), 4, '#f4f8f8')
        box((x - 6, y - 12, x + 6, y + 12), 6, '#146b76')
        box((x - 3, y - 9, x + 3, y + 9), 3, '#f4f8f8')
    return image.resize((180, 180), Image.Resampling.LANCZOS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('out', nargs='?', type=Path, default=Path(__file__).resolve().parents[1] / 'assets/box-settings.png')
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    render().save(args.out, format='PNG', optimize=False)


if __name__ == '__main__':
    main()
