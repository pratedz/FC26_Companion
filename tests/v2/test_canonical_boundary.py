"""Guard the Phase-1 canonical runtime boundary.

``src/v2`` is an earlier prototype retained temporarily for reference and its
own legacy tests.  The packaged v2 application is ``companion``; allowing a
new import edge back into ``src.v2`` would recreate the split implementation
that made the original rewrite impossible to reason about.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_v2_entrypoint_uses_companion_runtime() -> None:
    text = (ROOT / "app_entry_v2.py").read_text(encoding="utf-8")
    assert "companion.cli" in text
    assert "companion.ui.shell" in text
    assert "src.v2" not in text


def test_companion_never_imports_prototype_src_v2() -> None:
    offenders: list[str] = []
    for path in (ROOT / "companion").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "src.v2" in text or "src import v2" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []

