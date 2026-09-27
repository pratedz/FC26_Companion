"""Shared pytest fixtures.

The suite used to run against the *live* application directory: a plain
`pytest` run overwrote the user's companion_config.json, last_apply_snapshot.json,
queue/_pending.txt and bridge/RUN_BRIDGE_ONCE.lua. Anyone running the tests on
their own install silently lost their config and their undo snapshot.

The autouse guard below fails the run if a protected file is modified, so the
problem can never come back silently. Individual tests that legitimately need a
writable app root should monkeypatch `paths.app_root`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# Live user state that tests must never touch.
_PROTECTED = (
    "companion_config.json",
    "last_apply_snapshot.json",
    "profiles.json",
    "current_squad.json",
    "favorites.json",
)


def _fingerprint() -> dict[str, tuple[int, int] | None]:
    out: dict[str, tuple[int, int] | None] = {}
    for name in _PROTECTED:
        p = APP_ROOT / name
        try:
            st = p.stat()
            out[name] = (st.st_size, st.st_mtime_ns)
        except OSError:
            out[name] = None
    return out


@pytest.fixture(autouse=True, scope="session")
def _guard_live_user_state() -> object:
    """Fail the session if a test mutated real user state."""
    before = _fingerprint()
    yield
    after = _fingerprint()
    changed = [name for name in _PROTECTED if before.get(name) != after.get(name)]
    if changed:
        pytest.fail(
            "tests mutated live user state: "
            + ", ".join(changed)
            + " — monkeypatch paths.app_root instead of writing to the real app root",
            pytrace=False,
        )


@pytest.fixture()
def isolated_app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every consumer of paths.app_root at a throwaway directory.

    `paths` is imported as a module object into ~34 other modules, each holding
    its own reference, so patching the function on the module itself is what
    actually redirects all of them.
    """
    from src import paths

    root = tmp_path / "app"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths, "app_root", lambda: root)
    (root / "queue").mkdir(exist_ok=True)
    (root / "bridge").mkdir(exist_ok=True)
    return root
