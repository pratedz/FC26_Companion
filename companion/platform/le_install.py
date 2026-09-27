"""Install the resident Lua core + autorun stub into the Live Editor data dir.

SI-3: arm **only** via ``lua/autorun/``. This module never reads or writes
``live_editor.lua`` (or any other LE core file under ``lua/libs/``).

SI-2: ``verify_le_integrity`` checks that ``FCLiveEditor.DLL`` is present and
unchanged from a recorded pin when one is supplied; it never replaces the DLL.

Layout written (DECISIONS.md + V2_INGAME_CORE §2):

    <LE data dir>/lua/
      autorun/00_le_companion_autorun.lua   — arms only, never drains
      scripts/le_companion/                 — resident core (from ingame/)
        config.json                         — queue_dir + version stamp
        version.lua, util.lua, json.lua, …

The core source tree ships as ``ingame/le_companion/`` next to the ``companion``
package (dev checkout) or under the frozen resources root. Install copies those
files byte-for-byte and writes ``config.json`` beside them so the core stays
machine-independent (no QUEUE_DIR source patching).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import registry

__all__ = [
    "AUTORUN_STUB_NAME",
    "CORE_PACKAGE_DIR",
    "InstallReport",
    "IntegrityReport",
    "core_source_dir",
    "install",
    "installed_core_dir",
    "reconfigure_current_worker_snippet",
    "repair_queue_config",
    "repair_experimental_add_to_team",
    "status",
    "set_experimental_add_to_team",
    "uninstall",
    "verify_le_integrity",
]

#: v2 owns a distinct autorun file.  v1 owns ``00_le_companion_autorun.lua``;
#: sharing that name made whichever installer ran last silently disable the
#: other product.  LE intentionally executes every file in this directory, so
#: separate, idempotent loaders are the safe coexistence mechanism.
AUTORUN_STUB_NAME = "01_le_companion_v2_autorun.lua"
LEGACY_V1_AUTORUN_STUB_NAME = "00_le_companion_autorun.lua"

#: Directory name under ``lua/scripts/`` holding the resident core.
CORE_PACKAGE_DIR = "le_companion"
_REQUIRED_CORE_FILES = (
    "init.lua",
    "version.lua",
    "util.lua",
    "json.lua",
    "log.lua",
    "events.lua",
    "status.lua",
    "db.lua",
    "ops.lua",
    "runner.lua",
    "ops/_registry.lua",
    "ops/add_to_team.lua",
    "ops/repair_partial_add.lua",
)

#: Files that must never appear as install targets (SI-3).
_FORBIDDEN_NAME_FRAGMENTS = (
    "live_editor.lua",
    "FakeEAAC",
    "FCLiveEditor.DLL",
)


# --- paths ------------------------------------------------------------------


def _package_root() -> Path:
    """``LE_Profile_Executor/`` (or the frozen install root)."""
    # companion/platform/le_install.py -> companion/ -> app root
    return Path(__file__).resolve().parents[2]


def core_source_dir(resources: Path | None = None) -> Path | None:
    """Locate the shipped ``ingame/le_companion`` tree, or ``None`` if absent."""
    roots: list[Path] = []
    if resources is not None:
        roots.append(Path(resources))
    roots.append(_package_root())
    # Frozen builds may put data next to the exe or under _internal.
    try:
        import sys

        if getattr(sys, "frozen", False):
            roots.insert(0, Path(sys.executable).resolve().parent)
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                roots.insert(0, Path(meipass))
    except Exception:  # noqa: BLE001
        pass

    for root in roots:
        candidate = Path(root) / "ingame" / "le_companion"
        if candidate.is_dir() and any(candidate.glob("*.lua")):
            return candidate
    return None


def installed_core_dir(data_dir: Path) -> Path:
    """``<data dir>/lua/scripts/le_companion`` — the only core install target."""
    return Path(data_dir) / "lua" / "scripts" / CORE_PACKAGE_DIR


def autorun_path(data_dir: Path) -> Path:
    return Path(data_dir) / "lua" / "autorun" / AUTORUN_STUB_NAME


def _legacy_v1_autorun_path(data_dir: Path) -> Path:
    return Path(data_dir) / "lua" / "autorun" / LEGACY_V1_AUTORUN_STUB_NAME


# --- integrity (SI-2) -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    ok: bool
    install_root: str = ""
    dll_path: str = ""
    dll_present: bool = False
    sha256: str = ""
    expected_sha256: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_le_integrity(
    install_root: Path | None = None,
    *,
    expected_sha256: str | None = None,
) -> IntegrityReport:
    """Confirm ``FCLiveEditor.DLL`` is present; optionally pin its hash (SI-2).

    Never modifies the DLL. A missing install root is not ok; a hash mismatch
    is not ok. Callers that arm the worker should refuse on ``ok is False``.
    """
    root = Path(install_root) if install_root else registry.le_install_root()
    if root is None:
        return IntegrityReport(False, detail="LE install root not resolved")
    dll = root / "FCLiveEditor.DLL"
    if not dll.is_file():
        return IntegrityReport(
            False,
            install_root=str(root),
            dll_path=str(dll),
            detail="FCLiveEditor.DLL missing",
        )
    digest = hashlib.sha256(dll.read_bytes()).hexdigest()
    expected = (expected_sha256 or "").strip().lower()
    if expected and digest.lower() != expected:
        return IntegrityReport(
            False,
            install_root=str(root),
            dll_path=str(dll),
            dll_present=True,
            sha256=digest,
            expected_sha256=expected,
            detail="FCLiveEditor.DLL hash does not match pin (refusing to arm)",
        )
    return IntegrityReport(
        True,
        install_root=str(root),
        dll_path=str(dll),
        dll_present=True,
        sha256=digest,
        expected_sha256=expected,
        detail="DLL present" + (" and hash matches pin" if expected else ""),
    )


# --- install report ---------------------------------------------------------


@dataclass
class InstallReport:
    ok: bool
    dry_run: bool = False
    data_dir: str = ""
    data_dir_source: str = ""
    core_source: str = ""
    core_dest: str = ""
    autorun_path: str = ""
    queue_dir: str = ""
    core_version: str = ""
    files_planned: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    patches_live_editor: bool = False  # always False — SI-3 invariant
    integrity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "data_dir": self.data_dir,
            "data_dir_source": self.data_dir_source,
            "core_source": self.core_source,
            "core_dest": self.core_dest,
            "autorun_path": self.autorun_path,
            "queue_dir": self.queue_dir,
            "core_version": self.core_version,
            "files_planned": list(self.files_planned),
            "files_written": list(self.files_written),
            "errors": list(self.errors),
            "notes": list(self.notes),
            "patches_live_editor": self.patches_live_editor,
            "integrity": dict(self.integrity),
        }


# --- helpers ----------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_core_version(source: Path) -> str:
    version_lua = source / "version.lua"
    if not version_lua.is_file():
        return ""
    try:
        text = version_lua.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # version.VERSION = "2.0.0"
    for line in text.splitlines():
        if "VERSION" in line and "=" in line and '"' in line:
            start = line.find('"')
            end = line.find('"', start + 1)
            if start >= 0 and end > start:
                return line[start + 1 : end]
    return ""


def _sha256_tree(root: Path) -> str:
    """Stable content hash of every file under *root* (relative paths, sorted)."""
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for path in files:
        rel = path.relative_to(root).as_posix().encode("utf-8")
        h.update(rel)
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _core_source_sha256(source: Path) -> str:
    """Hash exactly the authored core files, excluding generated config."""
    digest = hashlib.sha256()
    for src_file in _iter_core_files(source):
        rel = src_file.relative_to(source).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(src_file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _iter_core_files(source: Path) -> Iterable[Path]:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            yield path


def _assert_safe_target(path: Path) -> None:
    text = str(path).replace("\\", "/").lower()
    for frag in _FORBIDDEN_NAME_FRAGMENTS:
        if frag.lower() in text:
            raise RuntimeError(f"SI-3/SI-1/SI-2: refusing to touch forbidden path {path}")


def _autorun_stub(*, scripts_dir: Path, core_dir: Path, queue_dir: Path) -> str:
    """Generate the autorun loader. Arms only — never drains (SI-6).

    Loads the v2 core when present. If a legacy bridge is still installed next
    to it, that bridge is left alone (v1 coexistence); this stub does not
    rewrite ``00_le_companion_bridge.lua``.
    """
    scripts = str(scripts_dir.resolve()).replace("\\", "/")
    core = str(core_dir.resolve()).replace("\\", "/")
    queue = str(queue_dir.resolve()).replace("\\", "/")
    return f"""-- LE Companion v2 auto-arm loader (generated — do not edit by hand)
--
-- LE executes every .lua in <data dir>/lua/autorun at startup.
-- SI-3: this is the ONLY arm path. live_editor.lua is never patched.
-- SI-6: arms handlers only; never drains the queue during LE init.
if _G.__LE_COMPANION_V2_AUTORUN_DONE then return end
_G.__LE_COMPANION_V2_AUTORUN_DONE = true

local SCRIPTS = [==[{scripts}]==]
local CORE_DIR = [==[{core}]==]
-- Fallback only. Prefer queue_dir from config.json so a visual-test / repair
-- path cannot leave the loader pointing at a disposable temp queue forever.
local QUEUE_DIR = [==[{queue}]==]
local CORE_OPS = {{}}

-- Read companion-owned config: queue target + narrow experimental gate.
-- Some Live Editor hosts loaded the core before its own config reader could
-- see config.json; reading here keeps install/repair as the single source.
local cfg = io.open(CORE_DIR .. "/config.json", "r")
if cfg then
  local cfg_text = cfg:read("*a") or ""
  cfg:close()
  local qd = cfg_text:match([["queue_dir"%s*:%s*"(.-)"]])
  if type(qd) == "string" and qd ~= "" then
    QUEUE_DIR = qd
  end
  if cfg_text:find('"add_to_team"', 1, true) then
    CORE_OPS[1] = "add_to_team"
  end
end

package.path = SCRIPTS .. "/?.lua;" .. SCRIPTS .. "/?/init.lua;" .. CORE_DIR .. "/?.lua;" .. package.path

_G.__LE_COMPANION_SAFE_ARM = true
local ok, mod = pcall(require, "le_companion.init")
if not ok then
  -- Partial core (version/util/json only): still stamp identity for doctor.
  ok, mod = pcall(require, "le_companion.version")
end
_G.__LE_COMPANION_SAFE_ARM = nil

if ok and type(mod) == "table" then
  if type(mod.configure) == "function" then
    pcall(mod.configure, {{ queue_dir = QUEUE_DIR, core_ops = CORE_OPS }})
  end
  if type(mod.arm) == "function" then
    pcall(mod.arm)
  end
  _G.LECompanion = _G.LECompanion or mod
  if Log then
    local banner = (type(mod.banner) == "function" and mod.banner()) or (mod.VERSION or "le_companion")
    Log("[LE_Companion] autorun armed (handlers only) <- " .. tostring(banner))
  end
else
  if Log then
    Log("[LE_Companion] autorun: core not loadable at " .. CORE_DIR .. " (" .. tostring(mod) .. ")")
  end
end
"""


def _v1_autorun_stub(bridge_path: Path) -> str:
    """Restore v1's arm path when upgrading an old, colliding v2 install."""
    bridge = str(bridge_path.resolve()).replace("\\", "/")
    return f"""-- LE Companion v1 auto-arm loader (coexistence repair)
-- v2 owns {AUTORUN_STUB_NAME}; this file remains dedicated to v1.
if _G.__LE_COMPANION_V1_AUTORUN_DONE then return end
_G.__LE_COMPANION_V1_AUTORUN_DONE = true

local BRIDGE = [==[{bridge}]==]
local f = io.open(BRIDGE, "r")
if not f then
  if Log then Log("[LE_Companion] v1 bridge not found at " .. BRIDGE) end
  return
end
f:close()

_G.__LE_COMPANION_SAFE_ARM = true
local ok, err = pcall(dofile, BRIDGE)
_G.__LE_COMPANION_SAFE_ARM = nil
if Log then
  if ok then
    Log("[LE_Companion] v1 autorun armed (handlers only) <- " .. BRIDGE)
  else
    Log("[LE_Companion] v1 autorun FAILED: " .. tostring(err))
  end
end
"""


def _legacy_slot_needs_repair(data_dir: Path) -> bool:
    """True only when the shared v1 filename contains our old v2 loader."""
    path = _legacy_v1_autorun_path(data_dir)
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "LE Companion v2 auto-arm loader" in text


def _config_payload(
    *,
    queue_dir: Path,
    core_version: str,
    core_sha256: str,
    app_version: str,
    core_ops: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "v": 3,
        "queue_dir": str(queue_dir.resolve()).replace("\\", "/"),
        "installed_utc": _utc_now(),
        "app_version": app_version,
        "core_version": core_version,
        "core_sha256": core_sha256,
        # Phase 4 remains explicitly disabled until measured in a real save.
        "core_ops": sorted({str(op) for op in core_ops if str(op) == "add_to_team"}),
        "patches_live_editor": False,
    }


# --- public API -------------------------------------------------------------


def install(
    *,
    queue_dir: Path | str,
    dry_run: bool = False,
    data_dir: Path | str | None = None,
    resources: Path | None = None,
    expected_dll_sha256: str | None = None,
) -> dict[str, Any]:
    """Install the resident core + autorun stub.

    Parameters
    ----------
    queue_dir:
        Absolute path the core should drain (written into ``config.json``).
    dry_run:
        When True, compute the plan and return it without writing anything.
    data_dir:
        Override LE data dir (tests). Default: registry / env resolution.
    resources:
        Override where to look for ``ingame/le_companion``.
    expected_dll_sha256:
        Optional SI-2 pin for ``FCLiveEditor.DLL``.
    """
    from .. import CORE_VERSION, __version__  # local import keeps platform light

    report = InstallReport(ok=False, dry_run=dry_run, patches_live_editor=False)
    qdir = Path(queue_dir)
    report.queue_dir = str(qdir)

    # Resolve data dir
    if data_dir is not None:
        ddir = Path(data_dir)
        report.data_dir = str(ddir)
        report.data_dir_source = "explicit"
        if not ddir.is_dir() and not dry_run:
            # Allow dry-run against a planned path; real install needs the dir.
            try:
                ddir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                report.errors.append(f"cannot create data dir {ddir}: {e}")
                return report.to_dict()
    else:
        resolved = registry.resolve_data_dir()
        report.data_dir_source = resolved.source.value
        if resolved.path is None:
            report.errors.append(
                f"LE data dir not resolved ({resolved.detail}). "
                f"Set {registry.ENV_DATA_DIR} or install Live Editor."
            )
            return report.to_dict()
        ddir = resolved.path
        report.data_dir = str(ddir)

    # Replacing a resident Lua package while FC/Live Editor can read it risks
    # a mixed old/new worker.  A dry-run remains available for diagnosis and
    # test callers may use an explicit temporary data directory; real installs
    # must wait until both host processes are fully closed.
    if not dry_run and data_dir is None:
        try:
            _assert_game_and_live_editor_stopped()
        except RuntimeError as exc:
            report.errors.append(str(exc))
            report.notes.append(
                "Close FC 26 and Live Editor completely, then install the Companion worker."
            )
            return report.to_dict()

    # Integrity (informational for dry-run; hard fail only when pin mismatches)
    integrity = verify_le_integrity(expected_sha256=expected_dll_sha256)
    report.integrity = integrity.to_dict()
    if expected_dll_sha256 and not integrity.ok:
        report.errors.append(integrity.detail or "LE integrity check failed")
        return report.to_dict()
    if not integrity.ok:
        report.notes.append(f"integrity: {integrity.detail}")

    source = core_source_dir(resources)
    if source is None:
        report.errors.append(
            "ingame/le_companion source not found — reinstall the companion package"
        )
        return report.to_dict()
    report.core_source = str(source)
    report.core_version = _read_core_version(source) or CORE_VERSION

    dest = installed_core_dir(ddir)
    ar_path = autorun_path(ddir)
    report.core_dest = str(dest)
    report.autorun_path = str(ar_path)

    # Plan file writes — scripts + config + autorun only.
    planned: list[str] = []
    for src_file in _iter_core_files(source):
        rel = src_file.relative_to(source)
        planned.append(str(dest / rel))
    planned.append(str(dest / "config.json"))
    planned.append(str(ar_path))
    v1_bridge = ddir / "lua" / "scripts" / "00_le_companion_bridge.lua"
    repair_v1_slot = _legacy_slot_needs_repair(ddir) and v1_bridge.is_file()
    if repair_v1_slot:
        planned.append(str(_legacy_v1_autorun_path(ddir)))
    report.files_planned = planned

    for target in planned:
        _assert_safe_target(Path(target))

    if dry_run:
        report.ok = True
        report.notes.append(
            f"dry-run: would install {len(list(_iter_core_files(source)))} core file(s) "
            f"+ config.json + autorun to {ddir}"
        )
        report.notes.append("SI-3: live_editor.lua is not a target")
        return report.to_dict()

    # Real install
    written: list[str] = []
    try:
        dest.mkdir(parents=True, exist_ok=True)
        ar_path.parent.mkdir(parents=True, exist_ok=True)

        for src_file in _iter_core_files(source):
            rel = src_file.relative_to(source)
            target = dest / rel
            _assert_safe_target(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, target)
            written.append(str(target))

        core_sha = _core_source_sha256(source)

        # Preserve an explicit Phase 4 opt-in across worker upgrades. Missing,
        # malformed, or unknown values always collapse to the safe default.
        previous_ops: list[str] = []
        old_config = dest / "config.json"
        if old_config.is_file():
            try:
                old_payload = json.loads(old_config.read_text(encoding="utf-8"))
                if isinstance(old_payload, dict) and isinstance(
                    old_payload.get("core_ops"), list
                ):
                    previous_ops = [
                        str(op)
                        for op in old_payload["core_ops"]
                        if str(op) == "add_to_team"
                    ]
            except (OSError, ValueError, TypeError):
                previous_ops = []
        config = _config_payload(
            queue_dir=qdir,
            core_version=report.core_version,
            core_sha256=core_sha,
            app_version=__version__,
            core_ops=previous_ops,
        )
        config_path = dest / "config.json"
        _assert_safe_target(config_path)
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        written.append(str(config_path))

        scripts_dir = dest.parent  # lua/scripts
        stub = _autorun_stub(scripts_dir=scripts_dir, core_dir=dest, queue_dir=qdir)
        _assert_safe_target(ar_path)
        ar_path.write_text(stub, encoding="utf-8", newline="\n")
        written.append(str(ar_path))

        if repair_v1_slot:
            legacy_path = _legacy_v1_autorun_path(ddir)
            legacy_path.write_text(
                _v1_autorun_stub(v1_bridge), encoding="utf-8", newline="\n"
            )
            written.append(str(legacy_path))
            report.notes.append(
                "repaired the former shared autorun slot for v1; v2 now uses "
                f"{AUTORUN_STUB_NAME}"
            )

    except Exception as e:  # noqa: BLE001
        report.errors.append(f"install failed: {type(e).__name__}: {e}")
        report.files_written = written
        return report.to_dict()

    report.files_written = written
    report.ok = True
    report.notes.append(
        f"installed core {report.core_version} ({len(written)} path(s)); "
        "restart Live Editor to arm"
    )
    report.notes.append("SI-3: live_editor.lua was not patched")
    return report.to_dict()


_AUTORUN_QUEUE_RE = re.compile(
    r"local QUEUE_DIR = \[==\[(.*?)\]==\]",
    re.DOTALL,
)


def _patch_autorun_queue_dir(autorun_file: Path, target: str) -> tuple[bool, str]:
    """Rewrite only the loader's QUEUE_DIR fallback. Returns (changed, previous)."""
    if not autorun_file.is_file():
        return False, ""
    text = autorun_file.read_text(encoding="utf-8")
    match = _AUTORUN_QUEUE_RE.search(text)
    if match is None:
        return False, ""
    previous = match.group(1)
    if previous == target:
        return False, previous
    new_text, count = _AUTORUN_QUEUE_RE.subn(
        f"local QUEUE_DIR = [==[{target}]==]",
        text,
        count=1,
    )
    if count != 1:
        return False, previous
    temporary = autorun_file.with_name(autorun_file.name + ".repair.tmp")
    _assert_safe_target(temporary)
    temporary.write_text(new_text, encoding="utf-8", newline="\n")
    temporary.replace(autorun_file)
    return True, previous


def repair_queue_config(
    *,
    queue_dir: Path | str,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Atomically repoint an existing worker without replacing its Lua core.

    Safe while FC 26 is running: only Companion-owned ``config.json`` and the
    generated autorun loader's ``QUEUE_DIR`` fallback are rewritten. Core
    scripts and Career data are never touched.

    Earlier visual-test installs left the autorun hardcoded to a temp queue
    while ``config.json`` looked correct. Repair must fix both, or a live
    session keeps writing markers the Companion cannot see.
    """
    qdir = Path(queue_dir).resolve()
    if not qdir.is_dir():
        raise FileNotFoundError(f"Companion queue directory does not exist: {qdir}")
    if data_dir is None:
        resolved = registry.resolve_data_dir()
        if resolved.path is None:
            raise FileNotFoundError("Live Editor data directory was not found")
        ddir = resolved.path
    else:
        ddir = Path(data_dir)
    config_path = installed_core_dir(ddir) / "config.json"
    ar_path = autorun_path(ddir)
    _assert_safe_target(config_path)
    if not config_path.is_file():
        raise FileNotFoundError("Install the Companion worker before repairing its queue")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Worker config is invalid; reinstall after closing FC 26") from exc
    if not isinstance(raw, dict) or raw.get("v") != 3:
        raise ValueError("Worker config is invalid; reinstall after closing FC 26")

    previous = str(raw.get("queue_dir") or "")
    target = str(qdir).replace("\\", "/")
    config_changed = previous != target
    if config_changed:
        raw["queue_dir"] = target
        temporary = config_path.with_name(config_path.name + ".repair.tmp")
        _assert_safe_target(temporary)
        try:
            temporary.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            temporary.replace(config_path)
        except OSError as exc:
            raise RuntimeError(f"Could not repair worker queue config: {exc}") from exc

    try:
        autorun_changed, previous_autorun = _patch_autorun_queue_dir(ar_path, target)
    except OSError as exc:
        raise RuntimeError(f"Could not repair worker autorun queue path: {exc}") from exc

    return {
        "changed": config_changed or autorun_changed,
        "config_changed": config_changed,
        "autorun_changed": autorun_changed,
        "config_path": str(config_path),
        "autorun_path": str(ar_path),
        "queue_dir": target,
        "previous_queue_dir": previous,
        "previous_autorun_queue_dir": previous_autorun,
        "rearm_required": True,
    }


def reconfigure_current_worker_snippet(
    queue_dir: Path | str | None = None,
) -> str:
    """Lua Engine snippet that reloads only the worker's corrected config.

    It does not call the runner or force-drain any queued work.  Its sole
    externally visible effect is a fresh ``session.json`` marker for the
    current Companion queue.

    When *queue_dir* is provided, the path is forced into configure() so a
    live process that still holds a temp visual-test queue is corrected even
    if the in-memory path differs from ``config.json``.
    """
    if queue_dir is not None:
        target = str(Path(queue_dir).resolve()).replace("\\", "/")
        configure = (
            f'c.configure({{ queue_dir = [==[{target}]==] }}) '
            f"-- force real Companion queue; does not run jobs"
        )
    else:
        configure = (
            "c.configure({}) -- rereads the repaired config.json; does not run jobs"
        )
    return f"""-- LE Companion: reconnect this already-open Live Editor session
local ok, result = pcall(function()
  local c = _G.LEC
  if type(c) ~= "table" or type(c.configure) ~= "function" then
    error("LE Companion v2 is not loaded")
  end
  {configure}
  local function host_true(value)
    return value == true or tonumber(value) == 1
  end
  local in_career = rawget(_G, "LEC_CAREER_SEEN") == true
  if not in_career then
    for _, name in ipairs({{ "IsInCM", "IsInCareerMode" }}) do
      local fn = rawget(_G, name)
      if type(fn) == "function" then
        local probe_ok, value = pcall(fn)
        if probe_ok and host_true(value) then
          in_career = true
          break
        end
      end
    end
  end
  if in_career and type(c.runner) == "table" and type(c.runner.on_career) == "function" then
    c.runner.on_career(nil, "reconnect")
  end
  local wrote, why = c.status.write_session({{
    phase = "manual_reconfigure",
    capabilities = {{ in_career = in_career }},
  }})
  if not wrote then error(tostring(why or "could not write session")) end
  return c.core_info().queue_dir
end)
if Log then
  Log("[LEC] queue reconnect " .. (ok and "OK: " .. tostring(result) or "FAILED: " .. tostring(result)))
end
"""


def uninstall(
    *,
    data_dir: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove the v2 core directory and autorun stub. Never touches LE core files."""
    report: dict[str, Any] = {
        "ok": False,
        "dry_run": dry_run,
        "removed": [],
        "errors": [],
        "patches_live_editor": False,
    }
    if data_dir is not None:
        ddir = Path(data_dir)
    else:
        ddir = registry.le_data_dir()
        if ddir is None:
            report["errors"].append("LE data dir not resolved")
            return report

    targets = [autorun_path(ddir), installed_core_dir(ddir)]
    for t in targets:
        _assert_safe_target(t)
    if dry_run:
        report["ok"] = True
        report["removed"] = [str(t) for t in targets if t.exists()]
        return report

    removed: list[str] = []
    for t in targets:
        try:
            if t.is_file():
                t.unlink()
                removed.append(str(t))
            elif t.is_dir():
                shutil.rmtree(t)
                removed.append(str(t))
        except OSError as e:
            report["errors"].append(f"{t}: {e}")
    report["removed"] = removed
    report["ok"] = not report["errors"]
    return report


def set_experimental_add_to_team(
    enabled: bool, *, data_dir: Path | str | None = None
) -> dict[str, Any]:
    """Persist the Phase 4 worker gate in the companion-owned config.

    It takes effect after Live Editor restarts. Unknown values are deliberately
    discarded so this cannot be used as a general capability escalation API.
    """
    if data_dir is None:
        resolved = registry.resolve_data_dir()
        if resolved.path is None:
            raise FileNotFoundError("Live Editor data directory was not found")
        ddir = resolved.path
    else:
        ddir = Path(data_dir)
    cfg_path = installed_core_dir(ddir) / "config.json"
    _assert_safe_target(cfg_path)
    if not cfg_path.is_file():
        raise FileNotFoundError("Install the v2 worker before enabling Phase 4")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("v") != 3:
        raise ValueError("Worker config is invalid; reinstall the worker")
    raw["core_ops"] = ["add_to_team"] if enabled else []
    temp = cfg_path.with_name(cfg_path.name + ".tmp")
    _assert_safe_target(temp)
    temp.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp.replace(cfg_path)
    return {
        "enabled": bool(enabled),
        "restart_required": True,
        "config_path": str(cfg_path),
    }


def _unsafe_add_player_job(payload: object) -> bool:
    """True only for an old Add Player job that can create a Career row."""
    if not isinstance(payload, dict):
        return False
    for op in payload.get("ops") or ():
        if not isinstance(op, dict) or op.get("op") != "add_to_team":
            continue
        return op.get("strategy") != "dummy_overwrite"
    return False


def _quarantine_unsafe_add_player_jobs(queue_dir: Path | str) -> tuple[str, ...]:
    """Archive legacy generated-slot Add Player jobs before a repair.

    This helper is deliberately called only while FC 26 / Live Editor are
    stopped.  It preserves the job and its cursor in ``poison/`` plus a final
    audit result, rather than deleting work that an old worker could resume.
    """
    from ..core.transport.jobfile import atomic_write_json, read_json, rewrite_job_index

    root = Path(queue_dir)
    jobs = root / "jobs"
    claimed = root / "claimed"
    poison = root / "poison"
    state = root / "state"
    results = root / "results"
    for directory in (jobs, claimed, poison, state, results):
        directory.mkdir(parents=True, exist_ok=True)
    quarantined: list[str] = []
    for source in (jobs, claimed):
        for path in source.glob("*.json"):
            if path.name == "index.json":
                continue
            payload = read_json(path)
            if not _unsafe_add_player_job(payload):
                continue
            job_id = path.stem
            destination = poison / path.name
            if destination.exists():
                destination = poison / f"{job_id}.legacy-create.json"
            path.replace(destination)
            for suffix in (".state.json",):
                cursor = state / f"{job_id}{suffix}"
                if cursor.exists():
                    cursor.replace(poison / cursor.name)
            partial = results / f"{job_id}.partial.json"
            if partial.exists():
                partial.replace(poison / partial.name)
            atomic_write_json(
                results / f"{job_id}.json",
                {
                    "schema": 3,
                    "job_id": job_id,
                    "label": str((payload or {}).get("label") or "Add Player"),
                    "state": "poisoned",
                    "outcome": "failed",
                    "ok": False,
                    "finished_at": int(datetime.now(timezone.utc).timestamp()),
                    "diagnostic": (
                        "Quarantined by Add Player safety repair: legacy generated "
                        "Career-player creation is disabled."
                    ),
                    "counts": {},
                    "ops": [],
                    "failures": [
                        {
                            "reason": "create_strategy_disabled",
                            "detail": "Use a fresh review with a live-verified free-agent dummy.",
                            "phase": "repair",
                        }
                    ],
                    "error": {
                        "phase": "repair",
                        "message": "create_strategy_disabled",
                        "detail": "Legacy generated Career-player creation was quarantined.",
                    },
                },
            )
            quarantined.append(job_id)
    rewrite_job_index(jobs)
    rewrite_job_index(claimed)
    return tuple(quarantined)


def _assert_game_and_live_editor_stopped() -> None:
    """Never swap the resident Lua tree while either host can read it."""
    from . import procs

    processes = procs.list_processes()
    game = procs.find_game(processes=processes)
    launcher = procs.find_le_launcher(processes=processes)
    if game or launcher:
        names = ", ".join(
            item.name for item in (game, launcher) if item is not None
        )
        raise RuntimeError(
            "Close FC 26 and Live Editor before repairing Add Player"
            + (f" ({names} is still running)." if names else ".")
        )


def repair_experimental_add_to_team(
    *,
    queue_dir: Path | str,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Reinstall the companion-owned worker, then enable Add Player safely.

    This is intentionally narrower than a generic repair: it only refreshes
    our own Lua package, its autorun loader, and the one opt-in operation.
    It exists for the real-world case where an interrupted/old install leaves
    ``config.json`` malformed, so a restart can never make the capability
    appear.  It never touches Live Editor's own files.
    """
    _assert_game_and_live_editor_stopped()
    quarantined = _quarantine_unsafe_add_player_jobs(queue_dir)
    report = install(queue_dir=queue_dir, data_dir=data_dir, dry_run=False)
    if not report.get("ok"):
        detail = "; ".join(str(item) for item in report.get("errors") or ())
        raise RuntimeError(detail or "could not reinstall the Companion worker")
    setting = set_experimental_add_to_team(True, data_dir=data_dir)
    return {
        "enabled": True,
        "restart_required": True,
        "config_path": setting["config_path"],
        "core_version": report.get("core_version", ""),
        "files_written": tuple(report.get("files_written") or ()),
        "quarantined_jobs": quarantined,
    }


def status(data_dir: Path | str | None = None) -> dict[str, Any]:
    """What is installed where — for doctor / CLI."""
    if data_dir is not None:
        ddir: Path | None = Path(data_dir)
        source = "explicit"
    else:
        resolved = registry.resolve_data_dir()
        ddir = resolved.path
        source = resolved.source.value

    out: dict[str, Any] = {
        "data_dir": str(ddir) if ddir else "",
        "data_dir_source": source,
        "core_installed": False,
        "core_valid": False,
        "autorun_installed": False,
        "autorun_valid": False,
        "installed": False,
        "core_dir": "",
        "autorun_path": "",
        "v1_autorun_path": "",
        "v1_autorun_present": False,
        "config": None,
        "core_source_available": core_source_dir() is not None,
        "patches_live_editor": False,
    }
    if ddir is None:
        return out

    core = installed_core_dir(ddir)
    ar = autorun_path(ddir)
    out["core_dir"] = str(core)
    out["autorun_path"] = str(ar)
    v1_ar = ddir / "lua" / "autorun" / LEGACY_V1_AUTORUN_STUB_NAME
    out["v1_autorun_path"] = str(v1_ar)
    out["v1_autorun_present"] = v1_ar.is_file()
    out["core_installed"] = core.is_dir() and any(core.glob("*.lua"))
    out["autorun_installed"] = ar.is_file()
    source_core = core_source_dir()
    expected_version = _read_core_version(source_core) if source_core else ""
    expected_sha = _core_source_sha256(source_core) if source_core else ""
    installed_version = _read_core_version(core)
    out["expected_core_version"] = expected_version
    out["installed_core_version"] = installed_version
    out["expected_core_sha256"] = expected_sha
    out["core_files_valid"] = all(
        (core / Path(name)).is_file() for name in _REQUIRED_CORE_FILES
    )
    out["core_content_matches"] = bool(
        source_core
        and core.is_dir()
        and all(
            (core / src_file.relative_to(source_core)).is_file()
            and hashlib.sha256(
                (core / src_file.relative_to(source_core)).read_bytes()
            ).digest()
            == hashlib.sha256(src_file.read_bytes()).digest()
            for src_file in _iter_core_files(source_core)
        )
    )
    out["core_valid"] = bool(
        out["core_files_valid"]
        and installed_version
        and (not expected_version or installed_version == expected_version)
        and (not source_core or out["core_content_matches"])
    )
    if ar.is_file():
        try:
            ar_text = ar.read_text(encoding="utf-8", errors="replace")
            out["autorun_valid"] = (
                "LE Companion v2 auto-arm loader" in ar_text
                and str(core.resolve()).replace("\\", "/") in ar_text.replace("\\", "/")
            )
        except OSError as e:
            out["autorun_error"] = str(e)
    cfg = core / "config.json"
    if cfg.is_file():
        try:
            out["config"] = json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            out["config_error"] = str(e)
    config = out["config"]
    out["experimental_add_to_team"] = bool(
        isinstance(config, dict)
        and isinstance(config.get("core_ops"), list)
        and "add_to_team" in config["core_ops"]
    )
    out["config_valid"] = bool(
        isinstance(config, dict)
        and config.get("v") == 3
        and config.get("queue_dir")
        and config.get("core_version")
        and (not installed_version or config.get("core_version") == installed_version)
        and (not expected_version or config.get("core_version") == expected_version)
        and (not expected_sha or config.get("core_sha256") == expected_sha)
    )
    out["installed"] = bool(
        out["core_valid"] and out["autorun_valid"] and out["config_valid"]
    )
    return out
