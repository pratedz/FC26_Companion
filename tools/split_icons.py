"""Split generated icon sheets into individual PNGs + build app.ico."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "assets"
SHEETS = ROOT / "sheets"
ICONS = ROOT / "icons"

SHEET_MAP = {
    "sheet_01_nav.jpg": [
        "home",
        "squad",
        "card",
        "edit",
        "download",
        "settings",
        "search",
        "target",
        "apply",
        "ai",
        "shield_check",
        "fitness",
        "energy",
        "chart",
        "link",
        "help",
    ],
    "sheet_02_player.jpg": [
        "player",
        "jersey",
        "boot",
        "run",
        "star",
        "foot",
        "body",
        "face",
        "height",
        "playstyle",
        "clipboard",
        "export",
        "import",
        "refresh",
        "copy",
        "paste",
    ],
    "sheet_03_actions.jpg": [
        "connect",
        "key",
        "browser",
        "logout",
        "user",
        "team",
        "message",
        "status_online",
        "status_offline",
        "loading",
        "progress",
        "folder",
        "code",
        "terminal",
        "bridge",
        "queue",
    ],
    "sheet_04_meta.jpg": [
        "calendar",
        "whistle",
        "contract",
        "unlock",
        "lock",
        "sole",
        "glove",
        "trophy",
        "medal",
        "flag",
        "location",
        "crest",
        "transfer",
        "ban",
        "success",
        "warning",
    ],
    "sheet_05_stats.jpg": [
        "ovr",
        "potential",
        "pace",
        "shoot",
        "pass",
        "dribble",
        "defend",
        "physical",
        "gk",
        "chemistry",
        "modifier",
        "globe",
        "birthday",
        "hair",
        "tattoo",
        "kit",
    ],
    "sheet_06_chrome.jpg": [
        "back",
        "forward",
        "close",
        "add",
        "minus",
        "trash",
        "edit_box",
        "save",
        "filter",
        "sort",
        "eye",
        "eye_off",
        "pin",
        "favorite",
        "info",
        "tip",
    ],
}


def split_sheet(path: Path, names: list[str], cols: int = 4, rows: int = 4, inset: float = 0.05) -> list[Path]:
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    cw, ch = w / cols, h / rows
    out: list[Path] = []
    for i, name in enumerate(names):
        r, c = divmod(i, cols)
        x0 = int(c * cw + cw * inset)
        y0 = int(r * ch + ch * inset)
        x1 = int((c + 1) * cw - cw * inset)
        y1 = int((r + 1) * ch - ch * inset)
        cell = im.crop((x0, y0, x1, y1)).resize((128, 128), Image.Resampling.LANCZOS)
        dest = ICONS / f"{name}.png"
        cell.save(dest, "PNG")
        out.append(dest)
    return out


def make_app_ico(src: Path, out_png: Path, out_ico: Path) -> None:
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    im = im.crop((left, top, left + s, top + s))
    im_256 = im.resize((256, 256), Image.Resampling.LANCZOS)
    im_256.save(out_png, "PNG")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icons = [im.resize(sz, Image.Resampling.LANCZOS) for sz in sizes]
    icons[0].save(out_ico, format="ICO", sizes=sizes, append_images=icons[1:])


def main() -> int:
    ICONS.mkdir(parents=True, exist_ok=True)
    total = 0
    for sheet_name, names in SHEET_MAP.items():
        p = SHEETS / sheet_name
        if not p.is_file():
            print("missing", p)
            continue
        created = split_sheet(p, names)
        total += len(created)
        print(f"{sheet_name}: {len(created)}")

    for src_name, stem in (
        ("app_icon_bright.jpg", "app"),
        ("app_icon_source.jpg", "app_alt"),
    ):
        src = ROOT / src_name
        if src.is_file():
            make_app_ico(src, ICONS / f"{stem}.png", ROOT / f"{stem}.ico")
            print("ico", ROOT / f"{stem}.ico")

    manifest = sorted(p.stem for p in ICONS.glob("*.png"))
    (ROOT / "icons_manifest.txt").write_text(
        "\n".join(manifest) + f"\n\ncount={len(manifest)}\n", encoding="utf-8"
    )
    print("TOTAL", len(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
