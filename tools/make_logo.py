"""Draw the Companion logo (assets/app.ico, assets/logo.png). Run: python tools/make_logo.py"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
S = 1024  # supersampled canvas
CYAN = (34, 211, 238)
GREEN = (34, 197, 94)
TOP = (18, 30, 44)
BOTTOM = (8, 12, 18)


def _gradient(size: int, a: tuple, b: tuple, diagonal: bool = False) -> Image.Image:
    strip = Image.linear_gradient("L").resize((size, size))
    if diagonal:
        strip = strip.rotate(45, resample=Image.BICUBIC, expand=False)
        strip = ImageChops.add(strip, Image.new("L", (size, size), 0))
    return Image.composite(Image.new("RGB", (size, size), b), Image.new("RGB", (size, size), a), strip)


def _font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype("C:/Windows/Fonts/bahnschrift.ttf", size)
    try:
        font.set_variation_by_name("Bold")
    except Exception:
        pass
    return font


def _rounded(mask_size: int, inset: int, radius: int) -> Image.Image:
    mask = Image.new("L", (mask_size, mask_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (inset, inset, mask_size - inset - 1, mask_size - inset - 1), radius=radius, fill=255
    )
    return mask


def draw() -> Image.Image:
    tile = _rounded(S, 0, int(S * 0.23))
    base = _gradient(S, TOP, BOTTOM).convert("RGBA")
    # soft cyan glow behind the mark
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((S * 0.18, S * 0.10, S * 0.82, S * 0.74), fill=(*CYAN, 46))
    from PIL import ImageFilter

    base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(S * 0.07)))

    # gradient ring
    ring_w = int(S * 0.045)
    inset = int(S * 0.045)
    outer = _rounded(S, inset, int(S * 0.19))
    inner = _rounded(S, inset + ring_w, int(S * 0.19) - ring_w)
    ring_mask = ImageChops.subtract(outer, inner)
    ring = _gradient(S, CYAN, GREEN, diagonal=True).convert("RGBA")
    base.paste(ring, (0, 0), ring_mask)

    draw_ = ImageDraw.Draw(base)
    font = _font(int(S * 0.50))
    text = "FC"
    box = draw_.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x = (S - w) / 2 - box[0]
    y = S * 0.20 - box[1]
    draw_.text((x, y), text, font=font, fill=(244, 248, 252))

    # pitch-line bar with a centre-circle notch, then "26"
    bar_y = int(y + box[1] + h + S * 0.07)
    bar_h = int(S * 0.045)
    bar_w = int(S * 0.52)
    bx = (S - bar_w) // 2
    grad = _gradient(S, CYAN, GREEN).convert("RGBA")
    bar_mask = Image.new("L", (S, S), 0)
    bm = ImageDraw.Draw(bar_mask)
    bm.rounded_rectangle((bx, bar_y, bx + bar_w, bar_y + bar_h), radius=bar_h // 2, fill=255)
    base.paste(grad, (0, 0), bar_mask)
    small = _font(int(S * 0.13))
    label = "26"
    lb = draw_.textbbox((0, 0), label, font=small)
    draw_.text(
        ((S - (lb[2] - lb[0])) / 2 - lb[0], bar_y + bar_h + S * 0.035 - lb[1]),
        label, font=small, fill=CYAN,
    )

    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(base, (0, 0), tile)
    return out


def main() -> None:
    art = draw()
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    frames = [art.resize((n, n), Image.LANCZOS) for n in sizes]
    ico = ROOT / "assets" / "app.ico"
    frames[-1].save(ico, format="ICO", sizes=[(n, n) for n in sizes], append_images=frames[:-1])
    art.resize((256, 256), Image.LANCZOS).save(ROOT / "assets" / "logo.png")
    print("wrote", ico, "and logo.png")


if __name__ == "__main__":
    main()
