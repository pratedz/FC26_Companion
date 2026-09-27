"""Install LE queue worker + wait until queued jobs are applied in-game.

The companion never injects into FC26 and never bypasses anticheat.
User launches the game via Live Editor / FakeEAAC as usual. This module:

  1. Installs the queue worker script under lua/scripts/ only
  2. Does NOT patch LE core (live_editor.lua) — auto-arm broke LE launch
  3. User arms worker once per session: Lua Engine → Execute worker script
  4. Drops jobs into queue/; the in-LE worker drains them

All game writes go through already-running Live Editor only.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths


BRIDGE_SCRIPT_NAME = "00_le_companion_bridge.lua"
HEARTBEAT_NAME = "_bridge_alive.txt"
ARMED_NAME = "_bridge_armed.txt"
AUTOARM_BEGIN = "-- LE_COMPANION_AUTOARM_BEGIN"
AUTOARM_END = "-- LE_COMPANION_AUTOARM_END"


def queue_dir() -> Path:
    return paths.queue_dir()


def done_dir() -> Path:
    d = queue_dir() / "done"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bridge_source() -> Path:
    # Prefer app_root/bridge, then frozen _internal/bridge
    try:
        return paths.bridge_source_path()
    except Exception:
        return paths.bridge_dir() / "le_profile_bridge.lua"


# Single LIVE threshold (seconds) — used by UI, arm_status, apply, export
HEARTBEAT_LIVE_SEC = 90.0


def le_scripts_bridge_path() -> Path:
    return paths.le_root() / "lua" / "scripts" / BRIDGE_SCRIPT_NAME


def le_live_editor_lua_path() -> Path:
    return paths.le_root() / "lua" / "libs" / "v2" / "imports" / "core" / "live_editor.lua"


def _bridge_source_text_with_abs_queue() -> str:
    """Bridge Lua with a single absolute QUEUE_DIR (never double-declare)."""
    src = bridge_source()
    if not src.is_file():
        raise FileNotFoundError(f"Bridge source missing: {src}")
    text = src.read_text(encoding="utf-8")
    q = str(queue_dir().resolve()).replace("\\", "/")
    text2, n = re.subn(
        r"^local QUEUE_DIR\s*=\s*.*$",
        f'local QUEUE_DIR = "{q}"',
        text,
        count=1,
        flags=re.M,
    )
    if n == 0:
        text2 = f'local QUEUE_DIR = "{q}"\n' + text
    lines = []
    seen = False
    for line in text2.splitlines(keepends=True):
        if re.match(r"^\s*local QUEUE_DIR\s*=", line):
            if seen:
                continue
            seen = True
            lines.append(f'local QUEUE_DIR = "{q}"\n')
        else:
            lines.append(line)
    return "".join(lines)


def _autoarm_snippet() -> str:
    """SAFE auto-arm: set flag so bridge only heartbeats + handlers (no job drain).

    Full ForceDrain during Load froze LE (14 queued jobs ran mid-init).
    """
    abs_bridge = str(le_scripts_bridge_path().resolve()).replace("\\", "/")
    gsub_bs = r'):gsub("\\", "/")'
    return (
        f"{AUTOARM_BEGIN}\n"
        "    -- LE Companion SAFE auto-arm (no queue drain at init — that froze LE).\n"
        "    pcall(function()\n"
        "        _G.__LE_COMPANION_SAFE_ARM = true\n"
        "        local paths = {\n"
        f"            [[{abs_bridge}]],\n"
        f'            (tostring(LE_DATA_PATH or "") .. "lua/scripts/{BRIDGE_SCRIPT_NAME}"'
        f"{gsub_bs},\n"
        f'            (tostring(LE_DATA_PATH or "") .. "/lua/scripts/{BRIDGE_SCRIPT_NAME}"'
        f"{gsub_bs},\n"
        "        }\n"
        "        for i = 1, #paths do\n"
        "            local path = paths[i]\n"
        '            if path and path ~= "" then\n'
        '                local f = io.open(path, "rb")\n'
        "                if f then\n"
        '                    local src = f:read("*a")\n'
        "                    f:close()\n"
        "                    if src and #src > 20 then\n"
        f'                        local chunk, err = load(src, "@{BRIDGE_SCRIPT_NAME}")\n'
        "                        if chunk then\n"
        "                            pcall(chunk)\n"
        "                            if LOGGER then\n"
        '                                LOGGER:LogInfo("[LE_Companion] SAFE auto-armed (handlers only)")\n'
        "                            end\n"
        "                            _G.__LE_COMPANION_SAFE_ARM = nil\n"
        "                            return\n"
        "                        elseif LOGGER then\n"
        '                            LOGGER:LogError("[LE_Companion] auto-arm load: " .. tostring(err))\n'
        "                        end\n"
        "                    end\n"
        "                end\n"
        "            end\n"
        "        end\n"
        "        _G.__LE_COMPANION_SAFE_ARM = nil\n"
        "        if LOGGER then\n"
        '            LOGGER:LogInfo("[LE_Companion] auto-arm: worker script not found yet")\n'
        "        end\n"
        "    end)\n"
        f"{AUTOARM_END}\n"
    )


def remove_autoarm_hook() -> Dict[str, Any]:
    """Strip any companion auto-arm block from live_editor.lua (safe LE restore)."""
    target = le_live_editor_lua_path()
    if not target.is_file():
        return {"ok": False, "path": str(target), "error": "live_editor.lua missing"}
    original = target.read_text(encoding="utf-8")
    if AUTOARM_BEGIN not in original:
        return {"ok": True, "path": str(target), "removed": False, "clean": True}
    cleaned = re.sub(
        re.escape(AUTOARM_BEGIN) + r".*?" + re.escape(AUTOARM_END) + r"\n?",
        "",
        original,
        flags=re.S,
    )
    # Prefer known-good backup if available
    bak = target.with_suffix(".lua.companion_bak")
    if bak.is_file() and AUTOARM_BEGIN not in bak.read_text(encoding="utf-8", errors="replace"):
        target.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        return {"ok": True, "path": str(target), "removed": True, "from_backup": True}
    target.write_text(cleaned, encoding="utf-8", newline="\n")
    return {"ok": True, "path": str(target), "removed": True, "from_backup": False}


def install_autoarm_hook() -> Dict[str, Any]:
    """SAFE auto-arm: load companion bridge on LIVE_EDITOR:Load (no drain at init).

    Requires bridge SAFE_ARM path (handlers + heartbeat only). Full queue drain
    during Load historically froze LE — that is why SAFE_ARM must stay on.
    Does not touch FakeEAAC or FCLiveEditor.DLL.
    """
    target = le_live_editor_lua_path()
    if not target.is_file():
        return {"ok": False, "path": str(target), "error": "live_editor.lua missing"}

    # Always reinstall worker text first so SAFE_ARM branch is present
    try:
        text_bridge = _bridge_source_text_with_abs_queue()
        dest = le_scripts_bridge_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text_bridge, encoding="utf-8", newline="\n")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": str(target), "error": f"bridge install failed: {e}"}

    original = target.read_text(encoding="utf-8")
    # Backup stock once
    bak = target.with_suffix(".lua.companion_bak")
    if not bak.is_file():
        try:
            bak.write_text(original, encoding="utf-8", newline="\n")
        except OSError:
            pass

    # Strip any previous companion block so re-inject is clean
    if AUTOARM_BEGIN in original:
        original = re.sub(
            re.escape(AUTOARM_BEGIN) + r".*?" + re.escape(AUTOARM_END) + r"\n?",
            "",
            original,
            flags=re.S,
        )

    if "function LIVE_EDITOR:Load()" not in original:
        return {
            "ok": False,
            "path": str(target),
            "error": "LIVE_EDITOR:Load() not found in live_editor.lua",
        }

    snippet = _autoarm_snippet()
    # Insert snippet just before the end of Load()
    m = re.search(
        r"(function LIVE_EDITOR:Load\(\)[\s\S]*?)(\nend\b)",
        original,
        flags=re.M,
    )
    if not m:
        return {
            "ok": False,
            "path": str(target),
            "error": "could not locate Load() body to patch",
        }
    # Avoid double-indent: snippet already uses 4-space body lines
    body, end = m.group(1), m.group(2)
    if AUTOARM_BEGIN in body:
        return {"ok": True, "path": str(target), "already": True}
    patched = original[: m.start()] + body.rstrip() + "\n" + snippet + end + original[m.end() :]
    target.write_text(patched, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "path": str(target),
        "mode": "safe_autoarm",
        "bridge": str(le_scripts_bridge_path()),
        "backup": str(bak) if bak.is_file() else None,
        "note": (
            "SAFE auto-arm installed. Launch FC26 with LE Launcher once — "
            "worker arms on LE Load without pasting. Career events drain jobs."
        ),
        "le_owns_anticheat": True,
        "fakeeaac_touched": False,
    }


def autoarm_installed() -> bool:
    """True only if a leftover auto-arm patch is still present (should be scrubbed)."""
    p = le_live_editor_lua_path()
    if not p.is_file():
        return False
    try:
        t = p.read_text(encoding="utf-8", errors="replace")
        return AUTOARM_BEGIN in t or "__LE_COMPANION_SAFE_ARM" in t
    except OSError:
        return False


def ensure_le_core_clean() -> Dict[str, Any]:
    """Remove any companion patch from live_editor.lua; restore stock if needed."""
    cleaned = remove_autoarm_hook()
    if autoarm_installed():
        # Hard restore via stock tool
        try:
            restore = paths.app_root() / "tools" / "restore_live_editor.py"
            if restore.is_file():
                import runpy

                runpy.run_path(str(restore), run_name="__restore__")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "cleaned": cleaned, "error": str(e)}
        cleaned = remove_autoarm_hook()
    return {
        "ok": not autoarm_installed(),
        "cleaned": cleaned,
        "path": str(le_live_editor_lua_path()),
    }


AUTORUN_STUB_NAME = "00_le_companion_autorun.lua"

_AUTORUN_STUB = """-- LE Companion auto-arm loader (generated - do not edit)
--
-- LE executes every .lua in <data dir>/lua/autorun at startup. Loading the
-- bridge from here removes the per-session ritual of pasting it into the Lua
-- Engine by hand.
--
-- SAFE_ARM is deliberate: the bridge registers its Career Mode event handlers
-- and writes one heartbeat, then returns WITHOUT draining. Draining during LE
-- init is what froze the editor when auto-arm was previously attempted by
-- patching live_editor.lua; this hook is LE's own sanctioned entry point and
-- never touches core files.
if _G.__LE_COMPANION_AUTORUN_DONE then return end
_G.__LE_COMPANION_AUTORUN_DONE = true

local BRIDGE = [==[{bridge_path}]==]

local f = io.open(BRIDGE, "r")
if not f then
  if Log then Log("[LE_Companion] autorun: bridge not found at " .. BRIDGE) end
  return
end
f:close()

_G.__LE_COMPANION_SAFE_ARM = true
local ok, err = pcall(dofile, BRIDGE)
_G.__LE_COMPANION_SAFE_ARM = nil

if Log then
  if ok then
    Log("[LE_Companion] autorun armed (handlers only) <- " .. BRIDGE)
  else
    Log("[LE_Companion] autorun FAILED: " .. tostring(err))
  end
end
"""


def install_autorun_loader() -> Dict[str, Any]:
    """Drop a loader stub into LE's lua/autorun so the worker arms itself.

    This is the fix for the single biggest usability problem: without it the
    user must alt-tab into LE's Lua Engine and paste the bridge every session.
    """
    bridge_path = str(le_scripts_bridge_path().resolve()).replace("\\", "/")
    stub = _AUTORUN_STUB.replace("{bridge_path}", bridge_path)
    written: List[str] = []
    errors: List[str] = []
    for d in paths.le_autorun_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            target = d / AUTORUN_STUB_NAME
            target.write_text(stub, encoding="utf-8", newline="\n")
            written.append(str(target))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{d}: {e}")
    return {
        "ok": bool(written),
        "written": written,
        "errors": errors,
        "bridge": bridge_path,
        "patches_le_core": False,
    }


def remove_autorun_loader() -> Dict[str, Any]:
    """Remove the auto-arm stub (back to manual paste)."""
    removed: List[str] = []
    for d in paths.le_autorun_dirs():
        target = d / AUTORUN_STUB_NAME
        try:
            if target.is_file():
                target.unlink()
                removed.append(str(target))
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "removed": removed}


def autorun_status() -> Dict[str, Any]:
    """Where the stub is installed, and whether it points at the current bridge."""
    bridge_path = str(le_scripts_bridge_path().resolve()).replace("\\", "/")
    found: List[Dict[str, Any]] = []
    for d in paths.le_autorun_dirs():
        target = d / AUTORUN_STUB_NAME
        if target.is_file():
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = ""
            found.append(
                {
                    "path": str(target),
                    "points_at_current_bridge": bridge_path in text,
                }
            )
    return {
        "installed": bool(found),
        "entries": found,
        "candidates": [str(p) for p in paths.le_autorun_dirs()],
        "bridge": bridge_path,
        "bridge_installed": le_scripts_bridge_path().is_file(),
    }


def install_bridge() -> Dict[str, Any]:
    """Install queue worker script + inject-side DLL helper. Never patches LE core / FakeEAAC."""
    core = ensure_le_core_clean()
    text = _bridge_source_text_with_abs_queue()
    dest = le_scripts_bridge_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    run_me = paths.bridge_dir() / "RUN_BRIDGE_ONCE.lua"
    run_me.write_text(text, encoding="utf-8", newline="\n")
    q = queue_dir()
    q.mkdir(parents=True, exist_ok=True)
    done_dir()
    pending = q / "_pending.txt"
    if not pending.is_file():
        pending.write_text("", encoding="utf-8")
    rebuild_pending()

    inject_info: Dict[str, Any]
    try:
        from . import inject_side as _inject_side

        inject_info = _inject_side.install_inject_dll()
    except Exception as e:  # noqa: BLE001
        inject_info = {
            "ok": False,
            "error": str(e),
            "fakeeaac_touched": False,
            "replaces_fcliveeditor": False,
        }

    # Auto-arm via LE's own lua/autorun hook so the user never has to paste
    # the bridge into the Lua Engine again.
    try:
        autorun_info = install_autorun_loader()
    except Exception as e:  # noqa: BLE001
        autorun_info = {"ok": False, "errors": [str(e)], "written": []}

    return {
        "source": str(bridge_source()),
        "installed": str(dest),
        "run_once": str(run_me),
        "queue": str(q),
        "autorun": autorun_info,
        "ok": dest.is_file() and bool(core.get("ok")),
        "autoarm": {
            "ok": False,
            "disabled": True,
            "error": "auto-arm disabled",
            "cleaned": core.get("cleaned"),
        },
        "le_core_clean": bool(core.get("ok")),
        "queue_abs": str(q.resolve()).replace("\\", "/"),
        "inject_dll": inject_info,
        "le_owns_anticheat": True,
        "companion_swaps_fakeeaac": False,
    }


def ensure_sync_installed() -> Dict[str, Any]:
    """Idempotent install on startup: worker only, never rewrites LE core silently.

    Core scrub only if auto-arm leftovers detected (repair).
    """
    if autoarm_installed():
        ensure_le_core_clean()
    # Install/refresh worker script without touching live_editor again
    text = _bridge_source_text_with_abs_queue()
    dest = le_scripts_bridge_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="\n")
        run_me = paths.bridge_dir() / "RUN_BRIDGE_ONCE.lua"
        run_me.write_text(text, encoding="utf-8", newline="\n")
        queue_dir().mkdir(parents=True, exist_ok=True)
        done_dir()
        rebuild_pending()
        return {
            "ok": dest.is_file(),
            "installed": str(dest),
            "le_core_clean": not autoarm_installed(),
            "silent_core_write": False,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "le_core_clean": not autoarm_installed()}


def bridge_script_text_for_clipboard() -> str:
    """Installed (or source) worker Lua, ready for LE paste (CRLF normalized by copy)."""
    dest = le_scripts_bridge_path()
    if dest.is_file():
        return dest.read_text(encoding="utf-8")
    # Fallback: bake absolute queue into source without writing
    return _bridge_source_text_with_abs_queue()


def bridge_installed() -> bool:
    return le_scripts_bridge_path().is_file()


def heartbeat_path() -> Path:
    return queue_dir() / HEARTBEAT_NAME


def armed_path() -> Path:
    return queue_dir() / ARMED_NAME


def bridge_heartbeat_age_sec() -> Optional[float]:
    """Seconds since last bridge pulse, or None if never."""
    p = heartbeat_path()
    if not p.is_file():
        return None
    try:
        return max(0.0, time.time() - p.stat().st_mtime)
    except OSError:
        return None


def bridge_armed() -> bool:
    """True if worker wrote _bridge_armed.txt this OS session (file exists)."""
    p = armed_path()
    return p.is_file()


def bridge_alive(max_age_sec: float | None = None) -> bool:
    """True if bridge wrote a recent heartbeat (actively draining)."""
    if max_age_sec is None:
        max_age_sec = HEARTBEAT_LIVE_SEC
    age = bridge_heartbeat_age_sec()
    if age is None:
        return False
    return age <= float(max_age_sec)


_pending_cache: tuple[float, List[Path]] | None = None
try:
    from . import ui_perf as _ui_perf

    _PENDING_TTL = float(getattr(_ui_perf, "CHROME_PENDING_TTL", 2.0) or 2.0)
except Exception:  # pragma: no cover
    _PENDING_TTL = 2.0  # seconds — UI poll must not re-scan disk every tick



def list_pending_lua(*, force: bool = False) -> List[Path]:
    """List real job *.lua in queue/ (cached briefly for UI responsiveness)."""
    global _pending_cache
    now = time.time()
    if (
        not force
        and _pending_cache is not None
        and (now - _pending_cache[0]) < _PENDING_TTL
    ):
        return list(_pending_cache[1])
    q = queue_dir()
    try:
        paths = sorted(
            [
                p
                for p in q.glob("*.lua")
                if p.is_file() and not p.name.startswith("_")
            ],
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        paths = []
    _pending_cache = (now, paths)
    return list(paths)


def invalidate_pending_cache() -> None:
    global _pending_cache
    _pending_cache = None


def rebuild_pending() -> List[str]:
    """Rewrite _pending.txt from every real job *.lua still in queue/."""
    invalidate_pending_cache()
    q = queue_dir()
    q.mkdir(parents=True, exist_ok=True)
    names = [p.name for p in list_pending_lua(force=True)]
    pending = q / "_pending.txt"
    pending.write_text(
        ("\n".join(names) + ("\n" if names else "")),
        encoding="utf-8",
        newline="\n",
    )
    return names


def clear_stale_jobs(
    *,
    keep: Optional[Path | str] = None,
    rebuild: bool = True,
) -> int:
    """Move old queue *.lua jobs to done/ so only the latest apply waits.

    Speeds up LE drain (one job instead of a pile of stuck exports/applies).
    Does not execute them. keep = path/name to leave in queue.
    Also archives leftover _run_now.lua so turbo/install cannot re-fire stale work.

    rebuild=False skips rewriting _pending.txt (turbo apply sets it right after).
    """
    keep_name = Path(keep).name if keep else None
    q = queue_dir()
    done = done_dir()
    n = 0
    for p in list_pending_lua(force=True):
        if keep_name and p.name == keep_name:
            continue
        try:
            dest = done / f"skipped_{int(time.time())}_{p.name}"
            p.replace(dest)
            n += 1
        except OSError:
            try:
                p.unlink(missing_ok=True)  # type: ignore[call-arg]
                n += 1
            except OSError:
                pass
    # Clear slot file that bridge drains first (stale turbo re-apply)
    run_now = q / "_run_now.lua"
    if run_now.is_file() and keep_name != "_run_now.lua":
        try:
            dest = done / f"skipped_{int(time.time())}_run_now.lua"
            run_now.replace(dest)
            n += 1
        except OSError:
            try:
                run_now.unlink(missing_ok=True)  # type: ignore[call-arg]
                n += 1
            except OSError:
                pass
    if rebuild:
        rebuild_pending()
    else:
        invalidate_pending_cache()
    return n


def last_result_text() -> str:
    p = queue_dir() / "_last_result.txt"
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def job_status_text() -> str:
    """Side-file written by apply/editor Lua: found=true/false etc."""
    p = queue_dir() / "_job_status.txt"
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def last_result_age_sec() -> Optional[float]:
    p = queue_dir() / "_last_result.txt"
    if not p.is_file():
        return None
    try:
        return max(0.0, time.time() - p.stat().st_mtime)
    except OSError:
        return None


def _archive_orphan_job(qf: Path) -> Optional[str]:
    """If LE ran via _run_now but left a named job file, move it to done/."""
    if not qf.is_file():
        return None
    try:
        dest = done_dir() / f"orphan_{int(time.time())}_{qf.name}"
        data = qf.read_text(encoding="utf-8", errors="replace")
        dest.write_text(data, encoding="utf-8", newline="\n")
        qf.unlink(missing_ok=True)  # type: ignore[call-arg]
        # Fast pending wipe for single-job turbo (avoid full queue glob)
        try:
            pending = queue_dir() / "_pending.txt"
            pending.write_text("", encoding="utf-8", newline="\n")
            invalidate_pending_cache()
        except OSError:
            rebuild_pending()
        return str(dest)
    except OSError:
        return None


def _done_artifacts_for(name: str, done: Path) -> tuple[List[Path], List[Path]]:
    """Locate done/ copies of a job without multi-glob thrash every poll.

    Bridge writes done/<name> or done/<unix>_<name>.
    clear_stale writes skipped_<ts>_<name>; orphans write orphan_<ts>_<name>.
    """
    real: List[Path] = []
    skipped: List[Path] = []
    exact = done / name
    if exact.is_file():
        real.append(exact)
        return real, skipped
    # One directory pass (done/ can be large); suffix match only
    suffix = "_" + name
    try:
        for p in done.iterdir():
            if not p.is_file():
                continue
            n = p.name
            if n == name:
                real.append(p)
                continue
            if not n.endswith(suffix) and not n.endswith(name):
                continue
            low = n.lower()
            if low.startswith("skipped_"):
                skipped.append(p)
            elif low.startswith("orphan_"):
                # orphan archive still proves LE-side completion path ran
                real.append(p)
            elif n.endswith(suffix):
                # timestamped bridge finish: <os.time()>_<name>
                real.append(p)
    except OSError:
        pass
    return real, skipped


def bridge_busy() -> bool:
    """True while LE worker is mid-job (add_to_team / long scripts)."""
    p = queue_dir() / "_bridge_busy.txt"
    if not p.is_file():
        return False
    try:
        age = time.time() - p.stat().st_mtime
        # Stale busy flag (worker crash) — ignore after 3 min
        return age < 180.0
    except OSError:
        return False


def wait_until_applied(
    queue_file: Path | str,
    *,
    timeout_sec: float = 45.0,
    poll_sec: float = 0.06,
    on_tick: Any = None,
) -> Dict[str, Any]:
    """
    Wait until queue_file is gone (moved to done/ by the LE worker)
    or a fresh OK result proves the job (or duplicate _run_now) ran.

    Does NOT treat bare "OK idle queue_empty" as success without evidence.

    Soft-extends while worker is busy or heartbeat is advancing so long jobs
    (CreatePlayer / add_to_team) are not false-timeout while still LIVE.
    """
    qf = Path(queue_file)
    name = qf.name
    base_timeout = max(8.0, float(timeout_sec))
    # Cap total wait (busy soft-extend can grow this)
    hard_cap = max(base_timeout, 90.0)
    deadline = time.time() + base_timeout
    done = done_dir()
    start = time.time()
    run_now = queue_dir() / "_run_now.lua"
    last_res = queue_dir() / "_last_result.txt"
    try:
        res_mtime0 = last_res.stat().st_mtime if last_res.is_file() else 0.0
    except OSError:
        res_mtime0 = 0.0
    try:
        job_mtime0 = qf.stat().st_mtime if qf.is_file() else 0.0
    except OSError:
        job_mtime0 = 0.0
    try:
        hb0 = bridge_heartbeat_age_sec()
        hb_mtime0 = time.time() - float(hb0 or 0) if hb0 is not None else 0.0
    except Exception:
        hb_mtime0 = 0.0

    run_now_existed = run_now.is_file()
    last_rewake = 0.0
    soft_extends = 0
    # Cache rewake body so we do not re-read the job file every 3s
    rewake_body: Optional[str] = None

    def _success(reason: str, done_file: Optional[str] = None) -> Dict[str, Any]:
        return {
            "applied": True,
            "reason": reason,
            "done_file": done_file or str(done / name),
            "bridge_alive": bridge_alive(),
            "last_result": last_result_text(),
        }

    def _rewake_job() -> None:
        """Cheap re-arm: rewrite _run_now + wake; skip while worker busy."""
        nonlocal rewake_body
        if not qf.is_file():
            return
        if bridge_busy():
            return  # don't interrupt in-progress CreatePlayer
        try:
            if rewake_body is None:
                rewake_body = qf.read_text(encoding="utf-8", errors="replace")
            run_now.write_text(rewake_body, encoding="utf-8", newline="\n")
            pending = queue_dir() / "_pending.txt"
            try:
                cur = pending.read_text(encoding="utf-8", errors="replace")
            except OSError:
                cur = ""
            if name not in cur:
                # Prefer append over full queue glob
                with pending.open("a", encoding="utf-8", newline="\n") as f:
                    f.write(name + "\n")
                invalidate_pending_cache()
            (queue_dir() / "_wake.txt").write_text(
                f"force_drain 1\nwake {name}\n", encoding="utf-8", newline="\n"
            )
        except OSError:
            pass

    while time.time() < deadline:
        elapsed = time.time() - start
        busy = bridge_busy()
        # Soft-extend while LE is actively working (busy flag or fresh pulse)
        if soft_extends < 4 and elapsed > base_timeout * 0.55:
            hb_age = bridge_heartbeat_age_sec()
            pulse_fresh = hb_age is not None and hb_age < 15.0
            if busy or pulse_fresh:
                extra = 20.0
                new_dl = time.time() + extra
                if new_dl > deadline and (new_dl - start) <= hard_cap:
                    deadline = new_dl
                    soft_extends += 1
        if on_tick:
            try:
                if busy:
                    msg = f"LE working… {elapsed:.0f}s · {name[:24]}"
                elif bridge_alive(30):
                    msg = (
                        f"LIVE idle {elapsed:.0f}s · open Career menus "
                        f"or Force drain · {name[:18]}"
                    )
                else:
                    msg = f"Waiting LE… {elapsed:.0f}s · {name[:28]}"
                on_tick(elapsed, msg)
            except Exception:
                pass

        # Re-arm every 4s — not while busy (would restart long jobs)
        if qf.is_file() and not busy and elapsed - last_rewake >= 4.0:
            _rewake_job()
            last_rewake = elapsed

        # Primary: job file removed — only success if worker-archived (not clear_stale)
        if not qf.is_file():
            txt = last_result_text()
            upper = (txt or "").strip().upper()
            real, skipped = _done_artifacts_for(name, done)
            if upper.startswith("FAIL") and (
                name in (txt or "") or "processed=" in (txt or "").lower()
            ):
                return {
                    "applied": False,
                    "reason": f"bridge reported FAIL: {txt}",
                    "done_file": str(real[0]) if real else None,
                    "bridge_alive": bridge_alive(),
                    "last_result": txt,
                }
            if real and (not txt or upper.startswith("OK") or "last=" in (txt or "").lower()):
                return _success(
                    "queue file processed by LE worker",
                    str(real[0]),
                )
            if skipped and not real:
                return {
                    "applied": False,
                    "reason": "job was cleared/skipped before LE ran",
                    "done_file": str(skipped[0]),
                    "bridge_alive": bridge_alive(),
                    "last_result": txt,
                }
            # File gone with no evidence yet — keep polling briefly via loop
            # (fall through to result / timeout paths)

        # Fresh bridge result after we queued
        try:
            if last_res.is_file() and last_res.stat().st_mtime > res_mtime0 + 0.02:
                txt = last_result_text()
                upper = txt.upper()
                if upper.startswith("FAIL"):
                    return {
                        "applied": False,
                        "reason": f"bridge reported FAIL: {txt}",
                        "done_file": None,
                        "bridge_alive": bridge_alive(),
                        "last_result": txt,
                    }
                # Only accept OK that names this job or _run_now (not a foreign OK)
                low = txt.lower()
                ran = (
                    name in txt
                    or "last=_run_now" in low
                    or f"last={name}" in low
                    or f"last={name.replace('.lua', '')}" in low
                )
                # FAIL processed=… ok=… must never count as applied
                if upper.startswith("OK") and "processed=" in low:
                    import re as _re

                    m = _re.search(r"ok=(\d+)", txt, flags=_re.I)
                    if m and int(m.group(1)) == 0:
                        return {
                            "applied": False,
                            "reason": f"bridge ran jobs but all failed: {txt}",
                            "done_file": None,
                            "bridge_alive": bridge_alive(),
                            "last_result": txt,
                        }
                if upper.startswith("OK") and ran:
                    # Job file gone → clear success
                    if not qf.is_file():
                        return _success(f"bridge result: {txt}")
                    # _run_now executed same payload; named file orphan — clean + success
                    # Only when result explicitly says last=_run_now or names this job
                    if run_now_existed and not run_now.is_file() and (
                        "last=_run_now" in low or name in txt
                    ):
                        orphan = _archive_orphan_job(qf)
                        return _success(
                            f"bridge ran _run_now (same job): {txt}",
                            orphan,
                        )
                    if name in txt:
                        orphan = _archive_orphan_job(qf)
                        return _success(f"bridge result: {txt}", orphan)
        except OSError:
            pass

        # Heartbeat advanced after job write and _run_now gone + job gone
        try:
            if (
                run_now_existed
                and not run_now.is_file()
                and not qf.is_file()
            ):
                return _success("queue + _run_now cleared by LE worker")
        except OSError:
            pass

        time.sleep(poll_sec)

    alive = bridge_alive(90)
    lr = last_result_text()
    still = qf.is_file()
    was_busy = bridge_busy()
    if not alive:
        reason = (
            "Timed out — worker not LIVE (no recent pulse). "
            "Tools → Force drain (copies short drain script) → LE Lua Engine → Execute, "
            "or Inject arm / paste full bridge once."
        )
    elif was_busy:
        reason = (
            "Timed out while worker still busy — job may still finish. "
            "Wait a few seconds in Career, or Tools → Force drain → Execute."
        )
    else:
        reason = (
            "Timed out — pulse is LIVE but Career events did not drain the job "
            "(sitting idle in menus often does nothing). "
            "Do ONE of: advance a day / open hub, OR Tools → Force drain → "
            "LE Lua Engine → Execute (short script)."
        )
    return {
        "applied": False,
        "reason": reason,
        "done_file": None,
        "bridge_alive": alive,
        "pending": str(qf) if still else None,
        "last_result": lr,
        "run_now_exists": run_now.is_file(),
        "job_mtime0": job_mtime0,
        "bridge_busy": was_busy,
    }


def _fmt_age(sec: Optional[float]) -> str:
    if sec is None:
        return "never"
    if sec < 60:
        return f"{int(sec)}s ago"
    if sec < 3600:
        return f"{int(sec // 60)}m ago"
    return f"{int(sec // 3600)}h ago"


def format_age(sec: Optional[float]) -> str:
    """Public alias for UI (avoid gui calling private _fmt_age)."""
    return _fmt_age(sec)


def sync_state() -> str:
    """
    High-level sync state for UI:
      live | installed | missing

    Only 'live' means LE is actively draining (recent heartbeat).
    Old pulses (minutes ago) are NOT treated as synced — that was misleading.
    """
    age = bridge_heartbeat_age_sec()
    if age is not None and age <= HEARTBEAT_LIVE_SEC:
        return "live"
    if bridge_installed():
        return "installed"
    return "missing"


def apply_status_line(*, short: bool = False) -> str:
    """Status for header/pill. Prefer short=True so UI is not tripled with dock essays."""
    state = sync_state()
    age = bridge_heartbeat_age_sec()
    n = len(list_pending_lua())
    busy = bridge_busy()
    if state == "live":
        if busy:
            return f"LIVE · working… pulse {_fmt_age(age)}"
        if n:
            return f"LIVE · {n} waiting · open Career or Force drain"
        return f"LIVE · pulse {_fmt_age(age)}"
    if state == "installed":
        pulse = f" · last pulse {_fmt_age(age)}" if age is not None else ""
        q = f" · {n} queued" if n else ""
        if short:
            return f"WORKER OFF{pulse}{q}"
        return f"WORKER OFF{pulse}{q} · use How to arm (once per session)"
    if short:
        return "NO WORKER"
    return "NO WORKER · click Install worker"
