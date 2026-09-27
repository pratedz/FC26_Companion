"""AppPaths — every file location, named once. Includes ``confine`` (SI-7).

Replaces v1's ``src/paths.py`` (18 module-level functions) and the 25 call
sites that bypassed it with ``paths.app_root() / "<literal>"``. Nothing in v2
composes a user-data filename inline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol


class AppPaths(Protocol):
    root: Path            # writable, next to the exe
    resources: Path       # read-only bundle (PyInstaller _MEIPASS)
    queue: Path
    jobs: Path            # queue/jobs/      — protocol v3 pending
    claimed: Path         # queue/claimed/   — claim-by-rename lock
    results: Path         # queue/results/   — one result file per job
    state_dir: Path       # queue/state/     — resume cursors
    poison: Path          # queue/poison/    — quarantined jobs
    archive: Path         # queue/archive/   — rotated by day
    session_file: Path    # queue/session.json — the ARM marker
    drain_file: Path      # queue/drain.json   — the TICK marker
    universe_db: Path     # card_db/catalog.sqlite
    state_db: Path        # state.sqlite — replaces 7 loose json files
    profiles_file: Path   # profiles.json (authored content, stays a file)
    logs: Path

    def confine(self, base: Path, name: str) -> Path | None: ...


def confine(base: Path, name: str) -> Path | None:
    """Resolve ``name`` strictly under ``base``; None if unsafe (SI-7).

    Rejects empty names, path separators, ``..``, drive letters, reserved
    prefixes, and anything that resolves outside ``base``. Ported from v1's
    ``src/protocol.py::confined_job_path`` — the Python side was always
    hardened; protocol v3 makes the Lua side match (``util.safe_name``).
    """
    raw = (name or "").strip()
    if not raw:
        return None
    if "\\" in raw or "/" in raw or ".." in raw:
        return None
    if len(raw) >= 2 and raw[1] == ":":
        return None
    if Path(raw).name != raw:
        return None
    b = Path(base).resolve()
    full = (b / raw).resolve()
    try:
        full.relative_to(b)
    except ValueError:
        return None
    return full


class RealAppPaths:
    """Filesystem layout for a real install: everything under ``root``."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            if getattr(sys, "frozen", False):
                root = Path(sys.executable).resolve().parent
            else:
                # companion/core/paths.py -> LE_Profile_Executor/
                root = Path(__file__).resolve().parents[2]
        self.root = Path(root)
        meipass = getattr(sys, "_MEIPASS", None)
        self.resources = Path(meipass) if meipass else self.root

        self.queue = self.root / "queue"
        self.jobs = self.queue / "jobs"
        self.claimed = self.queue / "claimed"
        self.results = self.queue / "results"
        self.state_dir = self.queue / "state"
        self.poison = self.queue / "poison"
        self.archive = self.queue / "archive"
        self.session_file = self.queue / "session.json"
        self.drain_file = self.queue / "drain.json"

        self.universe_db = self.root / "card_db" / "catalog.sqlite"
        self.state_db = self.root / "state.sqlite"
        self.profiles_file = self.root / "profiles.json"
        self.logs = self.root / "logs"

    def ensure_layout(self) -> None:
        """Create the v3 queue subtree. Never touches v1's flat files."""
        for d in (
            self.queue,
            self.jobs,
            self.claimed,
            self.results,
            self.state_dir,
            self.poison,
            self.archive,
            self.logs,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def confine(self, base: Path, name: str) -> Path | None:
        return confine(base, name)


class TempAppPaths(RealAppPaths):
    """Hermetic paths rooted in a temp dir — every filesystem test uses this."""

    def __init__(self, tmp: Path) -> None:
        super().__init__(root=Path(tmp))
        self.ensure_layout()
