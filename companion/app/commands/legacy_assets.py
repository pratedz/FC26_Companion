"""Submit asset reads and inspect exports off the UI thread.

Reports and previews live beside their job's exports, so restart/recovery never
confuses a missing portrait with one from a previous request or another ID.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import threading
from pathlib import Path
from typing import Any, Callable, Sequence

from ... import core_at_least
from ...domain.ids import is_ulid
from ...domain.job import Job, op_legacy_assets
from ...domain.legacy_assets import ASSET_CORE_MIN, DATABASE_ASSET, asset_paths, portrait_id
from ...domain.outcome import JobResult
from ..services import Services

_REPORT_LOCK = threading.Lock()
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 1024 * 1024


def availability(svc: Services, *, export: bool = False) -> str:
    live = svc.transport.liveness()
    if not live.armed:
        return "Start Live Editor and FC 26, then open your Career hub."
    if not core_at_least(live.core_version, ASSET_CORE_MIN):
        return "Install the updated Companion worker and restart Live Editor and FC 26."
    caps = live.capabilities or {}
    key = "legacy_file_export" if export else "legacy_file_check"
    if not caps.get("legacy_file_check") or not caps.get(key):
        return "This running LE session lacks the legacy file API. Use LE v26.3.6 or later."
    if not caps.get("hub_ready"):
        return "Open your Career hub before requesting game assets."
    return ""


def prepare_job(svc: Services, paths: Sequence[str], *, export: bool = False,
                required: bool = False, origin: str = "ui.assets") -> Job:
    paths = asset_paths(paths, export=export)
    job = Job(ops=(op_legacy_assets(paths, export=export, required=required),),
              label="Export game assets" if export else "Check game assets",
              origin=origin, budget_ms=5000, max_attempts=1,
              requires={"core_version": ASSET_CORE_MIN})
    base = _job_dir(svc, job.job_id)
    base.mkdir(parents=True, exist_ok=False)
    if export:
        for index, path in enumerate(paths, 1):
            # Support both flat and original relative-path exports.
            (base / str(index) / Path(path).parent).mkdir(parents=True)
    return job


def request(svc: Services, paths: Sequence[str], *, export: bool = False,
            required: bool = False, on_report: Callable[[dict[str, Any]], None] | None = None) -> str:
    from .apply import submit_job

    reason = availability(svc, export=export)
    if reason:
        raise ValueError(reason)
    job = prepare_job(svc, paths, export=export, required=required)

    def finished(result: JobResult) -> None:
        if not result.outcome.is_terminal:
            return
        def inspect(_token: Any) -> None:
            report = process_result(svc, job, result)
            if on_report is not None:
                svc.executor.on_ui_thread(lambda: on_report(report))
        svc.executor.submit(f"asset-report:{job.job_id}", inspect)

    return submit_job(svc, job, on_result=finished)


def _job_dir(svc: Services, job_id: str) -> Path:
    if not is_ulid(job_id):
        raise ValueError("Invalid asset job ID")
    base = (svc.paths.queue / "assets").resolve()
    base.relative_to(svc.paths.queue.resolve())
    path = (base / job_id).resolve()
    path.relative_to(base)
    return path


def _file_under(path: Path, base: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(base.resolve())
    if not resolved.is_file():
        raise ValueError("Exported file is missing")
    return resolved


def _inspect_file(path: Path, *, portrait: bool) -> dict[str, Any]:
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("Exported file is empty")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        header = stream.read(32)
        digest.update(header)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    detail: dict[str, Any] = {"bytes": size, "sha256": digest.hexdigest(),
                              "header_hex": header.hex(), "output": str(path)}
    if portrait:
        if size > MAX_IMAGE_BYTES or not header.startswith(b"DDS "):
            raise ValueError("Portrait is not a supported DDS image (maximum 8 MB)")
        if len(header) < 20:
            raise ValueError("Portrait has a truncated DDS header")
        height, width = struct.unpack_from("<II", header, 12)
        if not height or not width or width * height > MAX_IMAGE_PIXELS:
            raise ValueError("Portrait dimensions exceed the preview limit")
        # Pillow's DDS decoder supports the game's documented BC3/DXT5 format.
        from PIL import Image

        with Image.open(path) as image:
            if image.format != "DDS" or image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Portrait dimensions exceed the preview limit")
            image.load()  # Detect truncated/compressed payload errors now.
            detail["dimensions"] = [image.width, image.height]
            preview = path.parent / "preview.png"
            temp = path.parent / "preview.tmp"
            rgba = image.convert("RGBA")
            rgba.thumbnail((180, 180))
            rgba.save(temp, format="PNG")
            os.replace(temp, preview)
            detail["preview"] = str(preview)
    return detail


def process_result(svc: Services, job: Job, result: JobResult) -> dict[str, Any]:
    """Use the recorded request to confine filenames; distrust result paths."""
    if job.job_id != result.job_id or len(job.ops) != 1 or job.ops[0].op != "assets.legacy":
        raise ValueError("Asset result does not match its request")
    job.validate()
    base = _job_dir(svc, job.job_id)
    with _REPORT_LOCK:
        base.mkdir(parents=True, exist_ok=True)
        report_path = base / "report.json"
        if report_path.is_file():
            try:
                cached = json.loads(report_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("job_id") == job.job_id and isinstance(cached.get("assets"), list):
                    return cached
            except (OSError, ValueError):
                pass
        op = next((o for o in result.ops if o.op == "assets.legacy"), None)
        rows = (op.data.get("assets") or ()) if op is not None else ()
        report: dict[str, Any] = {
            "job_id": job.job_id, "action": job.ops[0].body["action"],
            "dry_run": job.dry_run, "outcome": result.outcome.name.lower(),
            "le_version": op.data.get("le_version", "unknown") if op else "unknown",
            "finished_at": result.finished.isoformat() if result.finished else "",
            "error": result.diagnostic or "; ".join(f.detail or f.reason for f in result.failures),
            "assets": [],
        }
        for index, path in enumerate(job.ops[0].body["paths"], 1):
            wire = next((r for r in rows if isinstance(r, dict) and r.get("path") == path), {})
            row = {"path": path, "exists": wire.get("exists") if isinstance(wire.get("exists"), bool) else None,
                   "exported": False, "error": str(wire.get("error") or "")}
            if not wire:
                row["error"] = "Worker returned no record for this asset."
            if wire.get("exported") is True and not job.dry_run and report["action"] == "export":
                try:
                    item_dir = (base / str(index)).resolve()
                    item_dir.relative_to(base)
                    allowed = [(item_dir / Path(path).name).resolve(), (item_dir / path).resolve()]
                    output = _file_under(Path(str(wire.get("output") or "")), item_dir)
                    if output not in allowed:
                        raise ValueError("Worker returned an unexpected export filename")
                    info = _inspect_file(output, portrait=portrait_id(path) is not None)
                    row.update(info)
                    if int(wire.get("bytes") or 0) != info["bytes"]:
                        raise ValueError("Export size differs from the worker's verified size")
                    row["exported"] = True
                    row["portrait_id"] = portrait_id(path)
                    if path == DATABASE_ASSET:
                        row["note"] = "Raw game database; this is not a Career save backup or a catalog import."
                except (OSError, ValueError, TypeError) as exc:
                    row.pop("preview", None)
                    row["error"] = f"Export inspection failed: {exc}"
            elif report["action"] == "export" and not job.dry_run and row["exists"] is True and not row["error"]:
                row["error"] = "Worker did not verify an exported file."
            report["assets"].append(row)
        report["ok"] = (result.ok and not any(r["error"] for r in report["assets"]))
        temp = base / "report.tmp"
        temp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, report_path)
        return report


def recent_reports(svc: Services, limit: int = 12) -> list[dict[str, Any]]:
    if svc.db is None:
        return []
    reports = []
    for record in svc.db.job_records(limit=limit, op="assets.legacy"):
        base = _job_dir(svc, record.job_id)
        try:
            report = json.loads((base / "report.json").read_text(encoding="utf-8"))
            if not isinstance(report, dict) or report.get("job_id") != record.job_id or not isinstance(report.get("assets"), list):
                raise ValueError("Malformed asset report")
            reports.append(report)
            continue
        except (OSError, ValueError):
            pass
        # Late results and app restarts use the same inspector as the live path.
        result = svc.transport.result(record.job_id)
        if result is not None and result.outcome.is_terminal:
            raw = record.job_json
            from ...domain.job import Op
            job = Job(job_id=record.job_id, ops=(Op("assets.legacy", "assets", raw["ops"][0]),),
                      dry_run=bool(raw.get("dry_run")))
            svc.executor.submit(f"asset-report:{record.job_id}",
                                lambda _token, j=job, r=result: process_result(svc, j, r))
            try:
                # Inline CLI inspectors can provide their completed report now.
                reports.append(json.loads((base / "report.json").read_text(encoding="utf-8")))
                continue
            except (OSError, ValueError):
                pass
        reports.append({"job_id": record.job_id, "action": record.job_json.get("ops", [{}])[0].get("action"),
                        "outcome": "inspecting" if result is not None else record.outcome.name.lower(),
                        "assets": [], "error": ""})
    return reports


def report_file(svc: Services, job_id: str, value: str) -> Path:
    """Confinement also applies when opening a persisted report's file."""
    return _file_under(Path(value), _job_dir(svc, job_id))
