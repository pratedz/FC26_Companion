"""Mechanical checks for the 2.11 UI redesign. Each prints a success token or exits 1.

    python tools/redesign_checks.py hex           # no colour literal outside theme.py
    python tools/redesign_checks.py contrast      # token pairs meet WCAG 2.2 AA
    python tools/redesign_checks.py placeholders  # no CTkEntry hides its placeholder
    python tools/redesign_checks.py glyphs        # navigation and chrome use the icon pack
    python tools/redesign_checks.py kickers       # no eyebrow/kicker label above a heading
    python tools/redesign_checks.py fonts         # every font size comes from the type scale
    python tools/redesign_checks.py tests         # pytest has no failure outside the baseline
    python tools/redesign_checks.py docs          # PRODUCT.md and DESIGN.md carry their sections
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "companion" / "ui"
sys.path.insert(0, str(ROOT))


def _ui_files() -> list[Path]:
    return sorted(p for p in UI.rglob("*.py") if "__pycache__" not in p.parts)


def _fail(lines: list[str]) -> int:
    for line in lines:
        print(line)
    return 1


def check_hex() -> int:
    pattern = re.compile(r"""["']#[0-9a-fA-F]{3,8}["']""")
    bad = []
    for path in _ui_files():
        if path.name == "theme.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    if bad:
        return _fail(bad)
    print("hex-ok")
    return 0


def _luminance(hex_colour: str) -> float:
    s = hex_colour.lstrip("#")
    channels = [int(s[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def check_contrast() -> int:
    from companion.ui import theme
    from companion.ui.widgets.primitives import soft_fill

    grounds = {"BG": theme.BG, "CHROME": theme.CHROME, "PANEL": theme.PANEL, "CARD": theme.CARD,
               "CARD_HOVER": theme.CARD_HOVER, "ROW_ALT": theme.ROW_ALT}
    text_roles = {"TEXT": theme.TEXT, "MUTED": theme.MUTED, "ACCENT": theme.ACCENT,
                  "SUCCESS": theme.SUCCESS, "WARNING": theme.WARNING, "DANGER_TEXT": theme.DANGER_TEXT}
    rows: list[tuple[str, float, float]] = []
    for gname, ground in grounds.items():
        for tname, colour in text_roles.items():
            rows.append((f"{tname} on {gname}", contrast(colour, ground), 4.5))
    for tone in ("ok", "warn", "error", "info", "muted"):
        fill, text = theme.tone_colors(tone)
        rows.append((f"solid {tone} label", contrast(text, fill), 4.5))
        rows.append((f"soft {tone} label", contrast(theme.tone_fg(tone), soft_fill(tone)), 4.5))
    for ovr in (50, 65, 72, 77, 82, 87, 92, 97):
        fill, text = theme.ovr_colors(ovr)
        rows.append((f"OVR {ovr} chip", contrast(text, fill), 4.5))
    for pname in ("POS_GK", "POS_DEF", "POS_MID", "POS_ATT"):
        for gname in ("CARD", "ROW_ALT"):
            rows.append((f"{pname} on {gname}", contrast(getattr(theme, pname), grounds[gname]), 4.5))
    for gname in ("PANEL", "CARD"):
        rows.append((f"BORDER_STRONG on {gname}", contrast(theme.BORDER_STRONG, grounds[gname]), 3.0))
        rows.append((f"FOCUS ring on {gname}", contrast(theme.FOCUS, grounds[gname]), 3.0))
    bad = [f"{name}: {ratio:.2f} < {need}" for name, ratio, need in rows if ratio < need]
    if bad:
        return _fail(bad)
    print(f"contrast-ok ({len(rows)} pairs)")
    return 0


def _calls(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            label = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if label == name:
                yield node


def check_placeholders() -> int:
    bad = []
    for path in _ui_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _calls(tree, "CTkEntry"):
            keys = {k.arg for k in call.keywords}
            if "textvariable" in keys and "placeholder_text" in keys:
                bad.append(f"{path.relative_to(ROOT)}:{call.lineno}: CTkEntry with textvariable hides its placeholder")
    if bad:
        return _fail(bad)
    print("placeholders-ok")
    return 0


GLYPHS = set("⌂◉✚⟳▤☰⧉⌘⬡◌")


def check_glyphs() -> int:
    from companion.ui import icons, nav

    bad = []
    for surface in (*nav.SURFACES, nav.DRAWER):
        if not surface.icon:
            bad.append(f"surface {surface.key} has no icon")
        elif not (icons.icons_dir() / f"{surface.icon}.png").is_file():
            bad.append(f"surface {surface.key} icon {surface.icon}.png missing")
    for rel in ("shell.py", "nav.py"):
        text = (UI / rel).read_text(encoding="utf-8")
        for ch in GLYPHS:
            if ch in text:
                bad.append(f"{rel}: unicode glyph {ch!r} still used as an icon")
    if bad:
        return _fail(bad)
    print("glyphs-ok")
    return 0


def check_kickers() -> int:
    bad = []
    for path in _ui_files():
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for call in _calls(tree, "eyebrow"):
            bad.append(f"{path.relative_to(ROOT)}:{call.lineno}: eyebrow() kicker")
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if re.fullmatch(r"[A-Z][A-Z0-9 ·&'/-]{5,}", value) and " " in value:
                    bad.append(f"{path.relative_to(ROOT)}:{node.lineno}: all-caps label {value!r}")
    if bad:
        return _fail(bad)
    print("kickers-ok")
    return 0


FONT_CALLS = {"ui_font", "mono_font", "num_font", "display_font", "text_label", "muted_label", "CTkFont"}


def check_fonts() -> int:
    """Every literal font size passed to a font or label factory is on the type scale."""
    from companion.ui import theme

    allowed = set(theme.TYPE_SCALE)
    bad = []
    for path in _ui_files():
        if path.name == "theme.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in FONT_CALLS:
                continue
            values = [k.value for k in node.keywords if k.arg == "size"]
            if name.endswith("_font") and node.args:
                values.append(node.args[0])
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, int):
                    if value.value not in allowed:
                        bad.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name} size={value.value}")
    if bad:
        return _fail(bad)
    print(f"fonts-ok (scale {sorted(allowed)})")
    return 0


BASELINE = ROOT / "docs" / "redesign" / "baseline_failures.txt"


def check_tests() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "tools") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "hidden_windows_plugin"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    failed = {line.split(" - ")[0].replace("FAILED ", "").strip()
              for line in out.splitlines() if line.startswith("FAILED ")}
    errors = {line.split(" - ")[0].replace("ERROR ", "").strip()
              for line in out.splitlines() if line.startswith("ERROR ")}
    baseline = {line.replace("FAILED ", "").strip()
                for line in BASELINE.read_text(encoding="utf-8").splitlines() if line.strip()}
    new = sorted((failed | errors) - baseline)
    summary = next((line for line in reversed(out.splitlines()) if " passed" in line), "no summary")
    if new:
        return _fail([f"new failure: {name}" for name in new] + [summary])
    print(f"tests-ok: {summary}; {len(baseline - failed)} baseline failures now pass")
    return 0


def check_docs() -> int:
    needs = {
        ROOT / "PRODUCT.md": ("## Platform", "## Users", "## Product Purpose", "## Product Principles"),
        ROOT / "DESIGN.md": ("## Overview", "## Colors", "## Typography", "## Layout", "## Components",
                             "## Do's and Don'ts"),
    }
    bad = []
    for path, sections in needs.items():
        if not path.is_file():
            bad.append(f"missing {path.name}")
            continue
        text = path.read_text(encoding="utf-8")
        bad.extend(f"{path.name} lacks {s}" for s in sections if s not in text)
    if bad:
        return _fail(bad)
    print("docs-ok")
    return 0


CHECKS = {
    "hex": check_hex,
    "contrast": check_contrast,
    "placeholders": check_placeholders,
    "glyphs": check_glyphs,
    "kickers": check_kickers,
    "fonts": check_fonts,
    "tests": check_tests,
    "docs": check_docs,
}


def main(argv: list[str]) -> int:
    names = argv or list(CHECKS)
    worst = 0
    for name in names:
        worst = max(worst, CHECKS[name]())
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
