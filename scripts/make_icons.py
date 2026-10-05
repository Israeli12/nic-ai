"""Generate the PWA icon set. Run only when the icon design changes."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "nic" / "web" / "static" / "icons"
BG = (15, 17, 21)
ACCENT = (91, 140, 255)
TEXT = (230, 232, 236)


def draw(size: int, maskable: bool = False) -> Image.Image:
    image = Image.new("RGBA", (size, size), BG + (255,))
    draw = ImageDraw.Draw(image)
    # Maskable icons get cropped to a circle by Android, so keep the art
    # inside the safe zone (80% of the canvas).
    inset = size * 0.18 if maskable else size * 0.10
    box = (inset, inset, size - inset, size - inset)

    # A listening ring: the mic sits inside concentric arcs.
    ring_width = max(2, int(size * 0.035))
    draw.ellipse(box, outline=ACCENT + (255,), width=ring_width)

    centre = size / 2
    body_width = size * 0.16
    body_height = size * 0.26
    top = centre - body_height * 0.75
    draw.rounded_rectangle(
        (centre - body_width / 2, top, centre + body_width / 2, top + body_height),
        radius=body_width / 2,
        fill=TEXT + (255,),
    )
    # Mic stand and base.
    stem_top = top + body_height * 1.05
    draw.line(
        (centre, stem_top, centre, stem_top + size * 0.10),
        fill=TEXT + (255,),
        width=max(2, int(size * 0.03)),
    )
    draw.line(
        (centre - size * 0.09, stem_top + size * 0.10, centre + size * 0.09, stem_top + size * 0.10),
        fill=TEXT + (255,),
        width=max(2, int(size * 0.03)),
    )
    # Cradle arc under the mic capsule.
    pad = size * 0.14
    draw.arc(
        (centre - pad, top + body_height * 0.35, centre + pad, stem_top + size * 0.02),
        start=0,
        end=180,
        fill=ACCENT + (255,),
        width=max(2, int(size * 0.03)),
    )
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        draw(size).save(OUT / f"icon-{size}.png")
    for size in (192, 512):
        draw(size, maskable=True).save(OUT / f"maskable-{size}.png")
    # iOS ignores the manifest and wants this exact asset.
    draw(180).save(OUT / "apple-touch-icon.png")
    draw(32).save(OUT / "favicon-32.png")
    print("\n".join(sorted(path.name for path in OUT.iterdir())))


if __name__ == "__main__":
    main()
