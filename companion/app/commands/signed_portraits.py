"""Save-scoped signed identities, cached portraits and explicit LE overrides."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import threading
from pathlib import Path
from typing import Any, Mapping

from ...domain.ids import new_ulid
from ...domain.legacy_assets import portrait_paths
from ...domain.outcome import JobResult
from ...domain.signing_identity import signed_player_identities
from ...platform.base_players import resolve_card_base_profile
from ...platform.registry import le_mods_dir
from . import legacy_assets

_LOCK = threading.RLock()


def identities(svc: Any, save_uid: str) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if not save_uid or svc.db is None:
        return out
    for record in svc.db.job_records(save_uid=save_uid, op="add_to_team", limit=None):
        if not isinstance(record.result_json, Mapping) or not isinstance(record.job_json, Mapping):
            continue
        try:
            result = JobResult.from_wire(record.result_json)
            if result.job_id != record.job_id:
                continue
            resolved = signed_player_identities(record.job_json, result, save_uid=save_uid)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        for pid, identity in resolved.items():
            if pid in out:
                continue
            identity = dict(identity)
            source = identity.get("source_playerid")
            if source is None:
                # Old receipts omitted the source ID. Only recover it when an
                # authoritative base row matches ALL four native dictionary IDs
                # and the saved real-face asset; a head ID alone is insufficient.
                player = identity["player"]
                face, fields = player.get("face") or {}, player.get("fields") or {}
                if isinstance(face, Mapping) and isinstance(fields, Mapping):
                    base = resolve_card_base_profile({"person_id": face.get("headassetid")})
                    keys = ("firstnameid", "lastnameid", "commonnameid", "playerjerseynameid")
                    if (base is not None and base.face_verified
                            and base.appearance.get("headassetid") == face.get("headassetid")
                            and all(key in fields and fields[key] == base.identity.get(key) for key in keys)):
                        source = base.source_playerid
            if isinstance(source, int) and not isinstance(source, bool) and 0 < source <= 499999:
                identity["source_playerid"] = source
            else:
                identity["source_playerid"] = None
            out[pid] = identity
    return out


def _root(svc: Any) -> Path:
    root = svc.paths.root.resolve()
    path = (root / "portraits").resolve()
    path.relative_to(root)
    return path


def _source_dir(svc: Any, source: int) -> Path:
    if isinstance(source, bool) or not isinstance(source, int) or not 0 < source <= 499999:
        raise ValueError("Invalid portrait source ID")
    base = _root(svc)
    path = (base / "sources" / str(source)).resolve()
    path.relative_to(base)
    return path


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _result(svc: Any, record: Any) -> JobResult | None:
    result = svc.transport.result(record.job_id)
    if result is not None:
        return result
    if isinstance(record.result_json, Mapping):
        try:
            return JobResult.from_wire(record.result_json)
        except (AttributeError, ValueError, TypeError):
            pass
    return None


def _json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(dict(data), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_portrait(path: Path) -> None:
    if not 128 <= path.stat().st_size <= legacy_assets.MAX_IMAGE_BYTES:
        raise ValueError("Portrait DDS size is invalid")
    with path.open("rb") as stream:
        header = stream.read(128)
    if (header[:4] != b"DDS " or struct.unpack_from("<I", header, 4)[0] != 124
            or struct.unpack_from("<II", header, 12) != (180, 180)
            or header[84:88] != b"DXT5"):
        raise ValueError("In-game portraits must be 180×180 DDS/BC3 (DXT5)")
    from PIL import Image
    with Image.open(path) as image:
        image.load()
        if image.format != "DDS" or image.size != (180, 180):
            raise ValueError("Portrait could not be decoded")


def _cache_report(svc: Any, source: int, report: Mapping[str, Any]) -> dict[str, Any]:
    base = _source_dir(svc, source)
    allowed = set(portrait_paths(source))
    for row in report.get("assets") or ():
        if not isinstance(row, Mapping) or row.get("path") not in allowed or row.get("exported") is not True:
            continue
        try:
            path = legacy_assets.report_file(svc, report["job_id"], str(row.get("output") or ""))
            validate_portrait(path)
            if _hash(path) != row.get("sha256"):
                raise ValueError("Exported portrait changed since verification")
            base.mkdir(parents=True, exist_ok=True)
            temp = base / "portrait.tmp"
            shutil.copyfile(path, temp)
            os.replace(temp, base / "portrait.dds")
            info = legacy_assets._inspect_file(base / "portrait.dds", portrait=True)
            state = {"state": "ready", "source_playerid": source, "job_id": report["job_id"],
                     "sha256": info["sha256"], "preview": str(base / "preview.png")}
            _json(base / "state.json", state)
            return state
        except (OSError, ValueError, TypeError) as exc:
            error = str(exc)
            continue
    else:
        error = str(report.get("error") or "No usable game portrait exists for this player.")
    state = {"state": "missing", "source_playerid": source, "job_id": report.get("job_id", ""), "error": error}
    _json(base / "state.json", state)
    return state


def ensure_source(svc: Any, source: int, *, retry: bool = False) -> dict[str, Any]:
    """Recover exports after restart, or queue one bounded request per source."""
    from ...domain.job import Job, Op
    from ...domain.legacy_assets import portrait_paths
    with _LOCK:
        base = _source_dir(svc, source)
        state = _read(base / "state.json")
        if state.get("state") == "ready" and (base / "portrait.dds").is_file() and (base / "preview.png").is_file():
            return state
        if state.get("job_id") and state.get("state") == "queued":
            record = svc.db.job_record(state["job_id"]) if svc.db else None
            result = _result(svc, record) if record else None
            if result is None or not result.outcome.is_terminal:
                return state
            if record is not None:
                job = Job(job_id=record.job_id, ops=(Op("assets.legacy", "assets", record.job_json["ops"][0]),))
                return _cache_report(svc, source, legacy_assets.process_result(svc, job, result))
        if state.get("state") == "missing" and not retry:
            return state
        # Reuse an already verified export rather than scheduling another one.
        if svc.db:
            for record in svc.db.job_records(op="assets.legacy", limit=None):
                raw = record.job_json or {}
                ops = raw.get("ops") or ()
                if len(ops) != 1 or ops[0].get("action") != "export" or raw.get("dry_run"):
                    continue
                if not any(path in portrait_paths(source) for path in ops[0].get("paths") or ()):
                    continue
                result = _result(svc, record)
                if result is None or not result.outcome.is_terminal:
                    continue
                job = Job(job_id=record.job_id, ops=(Op("assets.legacy", "assets", ops[0]),))
                report = legacy_assets.process_result(svc, job, result)
                if any(row.get("exported") and row.get("path") in portrait_paths(source) for row in report["assets"]):
                    return _cache_report(svc, source, report)
        reason = legacy_assets.availability(svc, export=True)
        if reason:
            return {"state": "unavailable", "error": reason}
        def received(report: dict[str, Any]) -> None:
            # Native export completion is already off the UI thread; cache work
            # stays there too. Recovered queued requests use this same path.
            svc.executor.submit(f"portrait-cache:{source}", lambda _token: _cache_locked(svc, source, report))
        jid = legacy_assets.request(svc, portrait_paths(source), export=True, on_report=received)
        state = {"state": "queued", "job_id": jid, "source_playerid": source}
        # Inline executors may have already delivered a terminal result.
        if _read(base / "state.json").get("job_id") != jid:
            _json(base / "state.json", state)
        return _read(base / "state.json")


def _cache_locked(svc: Any, source: int, report: Mapping[str, Any]) -> dict[str, Any]:
    with _LOCK:
        return _cache_report(svc, source, report)


def sync(svc: Any) -> None:
    squad = svc.store.snapshot().squad
    live = svc.transport.liveness()
    if not squad.save_uid or live.save_uid != squad.save_uid or not live.armed:
        return
    present = {int(p.get("playerid", p.get("id", 0))) for p in squad.players}
    sources = {i["source_playerid"] for pid, i in identities(svc, squad.save_uid).items()
               if pid in present and i.get("source_playerid")}
    for source in sorted(sources):
        try:
            ensure_source(svc, source)
        except (ValueError, OSError) as exc:
            if svc.log:
                svc.log.warning("Signed portrait %s: %s", source, exc)


def portrait_state(svc: Any, pid: int) -> dict[str, Any]:
    squad = svc.store.snapshot().squad
    identity = identities(svc, squad.save_uid).get(pid)
    if identity is None:
        return {}
    source = identity.get("source_playerid")
    state = _read(_source_dir(svc, source) / "state.json") if source else {"state": "unknown"}
    return {**state, "name": identity["name"], "source_playerid": source, "playerid": pid,
            "save_uid": squad.save_uid}


def preview_path(svc: Any, state: Mapping[str, Any]) -> Path | None:
    source = state.get("source_playerid")
    if not source or state.get("state") != "ready":
        return None
    path = _source_dir(svc, source) / "preview.png"
    return path if path.is_file() else None


def _selection(svc: Any, pid: int) -> dict[str, Any]:
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 499999:
        raise ValueError("Invalid Career player ID")
    squad, live = svc.store.snapshot().squad, svc.transport.liveness()
    if (not live.armed or not (live.capabilities or {}).get("hub_ready")
            or not squad.save_uid or live.save_uid != squad.save_uid or squad.stale
            or squad.session_id != live.session_id):
        raise ValueError("Refresh your current Career squad before changing its portrait.")
    if pid not in {int(p.get("playerid", p.get("id", 0))) for p in squad.players}:
        raise ValueError("This player is no longer in the current squad.")
    state = portrait_state(svc, pid)
    if not state:
        raise ValueError("No verified signing identity exists for this player.")
    return state


def _destination(pid: int) -> Path:
    mods = le_mods_dir()
    if mods is None:
        raise ValueError("Live Editor's mods folder could not be resolved.")
    base = mods.resolve()
    path = (base / "legacy" / "data" / "ui" / "imgAssets" / "heads" / f"p{pid}.DDS").resolve()
    path.relative_to(base)
    return path


def _ledger(svc: Any, pid: int) -> Path:
    base = _root(svc)
    path = (base / "installations" / f"p{pid}.json").resolve()
    path.relative_to(base)
    return path


def install(svc: Any, pid: int) -> dict[str, Any]:
    """Explicit opt-in; affects this ID globally while LE is running."""
    with _LOCK:
        state = _selection(svc, pid)
        if state.get("state") != "ready":
            raise ValueError("Load the signed player's portrait before installing it.")
        source = _source_dir(svc, state["source_playerid"]) / "portrait.dds"
        validate_portrait(source)
        digest = _hash(source)
        if digest != state.get("sha256"):
            raise ValueError("The cached portrait changed; load it again.")
        target, ledger = _destination(pid), _ledger(svc, pid)
        old = _read(ledger)
        if old.get("active") or (old.get("status") == "prepared" and target.is_file() and _hash(target) == old.get("sha256")):
            if old.get("destination") != str(target) or not target.is_file() or _hash(target) != old.get("sha256"):
                raise ValueError("The installed portrait changed externally; preserve it before installing another.")
            if old.get("sha256") == digest:
                return old
            raise ValueError("Restore the previous portrait before installing a different one.")
        target.parent.mkdir(parents=True, exist_ok=True)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        backup = ledger.parent / f"p{pid}-{new_ulid()}.backup"
        existed = target.exists()
        if existed:
            shutil.copyfile(target, backup)
        record = {"active": False, "status": "prepared", "destination": str(target), "backup": str(backup) if existed else "",
                  "previous_sha256": _hash(backup) if existed else "", "sha256": digest,
                  "save_uid": state["save_uid"], "source_playerid": state["source_playerid"], "playerid": pid}
        _json(ledger, record)  # Backup receipt exists before replacing the file.
        temp = target.with_suffix(".companion.tmp")
        try:
            shutil.copyfile(source, temp)
            os.replace(temp, target)
            if _hash(target) != digest:
                raise ValueError("Could not verify the installed portrait")
            record["active"] = True
            record["status"] = "installed"
            _json(ledger, record)
        except BaseException:
            if existed:
                shutil.copyfile(backup, temp)
                os.replace(temp, target)
            elif target.exists() and _hash(target) == digest:
                target.unlink()
            raise
        return record


def restore(svc: Any, pid: int) -> dict[str, Any]:
    with _LOCK:
        state = _selection(svc, pid)
        ledger, target = _ledger(svc, pid), _destination(pid)
        record = _read(ledger)
        if not (record.get("active") or record.get("status") == "prepared") or record.get("save_uid") != state["save_uid"]:
            raise ValueError("No installed portrait belongs to this signing save.")
        if record.get("destination") != str(target) or not target.is_file() or _hash(target) != record.get("sha256"):
            raise ValueError("The portrait changed externally; it was left untouched.")
        if record.get("backup"):
            backup = Path(record["backup"]).resolve()
            backup.relative_to(ledger.parent.resolve())
            if _hash(backup) != record.get("previous_sha256"):
                raise ValueError("The previous portrait backup changed; restore stopped.")
            temp = target.with_suffix(".companion.tmp")
            shutil.copyfile(backup, temp)
            os.replace(temp, target)
        else:
            target.unlink()
        record["active"] = False
        record["status"] = "restored"
        _json(ledger, record)
        return record


def installed(svc: Any, pid: int) -> bool:
    record = _read(_ledger(svc, pid))
    return bool((record.get("active") or record.get("status") == "prepared")
                and record.get("save_uid") == svc.store.snapshot().squad.save_uid)
