"""Native asset contract, path confinement, recovery and real DDS decoding."""
from dataclasses import replace
from pathlib import Path
import hashlib
import json

import pytest
from PIL import Image

from companion.app.commands import legacy_assets as assets
from companion.app.commands.apply import _is_write_job
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import job_requests_fast_read
from companion.domain.job import Job, Op, op_legacy_assets
from companion.domain.legacy_assets import DATABASE_ASSET, legacy_path, portrait_paths
from companion.domain.outcome import JobResult

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def svc(tmp_path):
    paths = TempAppPaths(tmp_path)
    transport = FakeTransport()
    transport.set_liveness(replace(transport.liveness(), core_version="2.6.30",
        capabilities={"hub_ready": True, "legacy_file_check": True, "legacy_file_export": True}))
    service = Services(paths, FakeClock(), InlineExecutor(), transport, Store(), db=Database(paths.state_db))
    yield service
    service.db.close()


@pytest.mark.parametrize("path", ["../outside.db", "data/x/../../a", "data//a", "data/x/CON.dds",
    "data/x/COM1.dds", "data/x/file.", "data/x/a:stream", "C:/file", "data\\x\\a", "data/x/a\n"])
def test_rejects_host_paths_and_windows_edge_cases(path):
    with pytest.raises(ValueError):
        legacy_path(path)
    with pytest.raises(ValueError):
        Job(ops=(Op("assets.legacy", "assets", {"action": "export", "paths": [path]}),)).validate()


def test_export_is_a_bounded_game_read_not_a_squad_mutation(svc):
    job = assets.prepare_job(svc, portrait_paths(190045), export=True)
    assert not _is_write_job(job)
    assert not job_requests_fast_read(job)  # Native export may take time.
    assert job_requests_fast_read(Job(ops=(op_legacy_assets([DATABASE_ASSET]),)))
    with pytest.raises(ValueError):
        op_legacy_assets([DATABASE_ASSET] * 3, export=True)


def test_requires_actual_host_capabilities_and_updated_worker(svc):
    assert not assets.availability(svc, export=True)
    svc.transport.set_liveness(replace(svc.transport.liveness(), capabilities={"hub_ready": True}))
    with pytest.raises(ValueError, match="lacks"):
        assets.request(svc, [DATABASE_ASSET])
    assert not svc.transport.submitted
    svc.transport.set_liveness(replace(svc.transport.liveness(), core_version="2.6.29"))
    assert "updated" in assets.availability(svc)


def lua_host(svc, *, layout="flat", native_return=True, payload=b"game db fixture"):
    lua = pytest.importorskip("lupa").LuaRuntime(unpack_returned_tuples=True)
    lua.globals().root = (ROOT / "ingame/le_companion").as_posix()
    lua.execute('package.path=root.."/?.lua;"..package.path; LE_VERSION="v26.3.6"')
    lua.globals()._payload = payload.decode("latin1")
    lua.globals().layout = layout
    lua.globals().native_return = native_return
    lua.execute('''
        calls = 0
        function LegacyFileExist(path) return not path:find("youthheads", 1, true) end
        function LegacyFileExport(path, out)
            calls = calls + 1
            if layout == "filename" and out:sub(-1) == "/" then return false end
            local dest = out
            if out:sub(-1) == "/" then
                dest = out .. (layout == "nested" and path or path:match("([^/]+)$"))
            end
            local f = assert(io.open(dest, "wb"))
            f:write(_payload); f:close()
            return native_return
        end
        handler = require("ops.legacy_assets")
    ''')
    return lua


def execute(lua, svc, job):
    lua.globals()._wire = lua.table_from(job.to_wire(), recursive=True)
    lua.globals()._ctx = lua.table_from({"queue_dir": svc.paths.queue.as_posix()})
    raw = lua.eval("handler.run(_wire.ops[1], _wire, _ctx)")
    text = lua.eval("require('json').encode")(raw)
    data = json.loads(text)
    return data, JobResult.from_wire({"job_id": job.job_id, "state": "done" if data["ok"] else "failed",
        "ok": data["ok"], "counts": {"ops_ok": 1 if data["ok"] else 0},
        "failures": data.get("failures"), "ops": [{"op": "assets.legacy", "id": "assets", **data}]})


@pytest.mark.parametrize("layout", ["flat", "nested", "filename"])
def test_native_export_and_python_inspector_handle_documented_output_location(svc, layout):
    job = assets.prepare_job(svc, [DATABASE_ASSET], export=True, required=True)
    lua = lua_host(svc, layout=layout)
    raw, result = execute(lua, svc, job)
    assert raw["ok"] and raw["assets"][0]["exported"]
    report = assets.process_result(svc, job, result)
    assert report["ok"] and report["assets"][0]["exported"]
    assert report["assets"][0]["sha256"] == hashlib.sha256(b"game db fixture").hexdigest()
    assert "not a Career" in report["assets"][0]["note"]
    repeated, _ = execute(lua, svc, job)
    assert not repeated["ok"]  # A retry cannot bless last run's output.


def test_native_false_is_failure_even_when_it_writes_a_file(svc):
    job = assets.prepare_job(svc, [DATABASE_ASSET], export=True)
    raw, result = execute(lua_host(svc, native_return=False), svc, job)
    assert not raw["ok"] and not raw["assets"][0]["exported"]
    assert not assets.process_result(svc, job, result)["ok"]


def test_empty_native_export_is_failure(svc):
    job = assets.prepare_job(svc, [DATABASE_ASSET], export=True)
    raw, result = execute(lua_host(svc, payload=b""), svc, job)
    assert not raw["ok"] and not raw["data"]["assets"][0]["exported"]
    assert not assets.process_result(svc, job, result)["ok"]


def test_missing_required_database_export_is_failure(svc):
    job = assets.prepare_job(svc, [DATABASE_ASSET], export=True, required=True)
    lua = lua_host(svc)
    lua.execute("LegacyFileExist=function() return false end")
    raw, _ = execute(lua, svc, job)
    assert not raw["ok"] and lua.globals().calls == 0


def test_dry_run_checks_but_never_exports(svc):
    job = replace(assets.prepare_job(svc, [DATABASE_ASSET], export=True), dry_run=True)
    lua = lua_host(svc)
    raw, result = execute(lua, svc, job)
    assert raw["ok"] and raw["assets"][0]["exists"] and lua.globals().calls == 0
    report = assets.process_result(svc, job, result)
    assert report["dry_run"] and not report["assets"][0]["exported"]


def test_missing_optional_portrait_is_explicit_and_does_not_fail_other_portrait(svc):
    job = assets.prepare_job(svc, portrait_paths(190045), export=True)
    raw, _ = execute(lua_host(svc), svc, job)
    assert raw["ok"] and raw["assets"][1]["exists"] is False
    assert raw["assets"][1]["exported"] is False


def test_missing_api_and_invalid_paths_never_call_native_export(svc):
    job = assets.prepare_job(svc, [DATABASE_ASSET], export=True)
    lua = lua_host(svc)
    lua.execute("LegacyFileExport=nil")
    raw, _ = execute(lua, svc, job)
    assert raw["reason"] == "unsupported_host" and lua.globals().calls == 0
    lua.execute('bad={action="export", paths={"data/x/../secret"}}')
    lua.globals()._job = lua.table_from(job.to_wire(), recursive=True)
    lua.globals()._ctx = lua.table_from({"queue_dir": svc.paths.queue.as_posix()})
    assert lua.eval("handler.run(bad, _job, _ctx).reason") == "bad_asset_path"
    assert lua.globals().calls == 0


def python_result(job, path, *, output=None):
    return JobResult.from_wire({"job_id": job.job_id, "state": "done", "ok": True,
        "ops": [{"op": "assets.legacy", "id": "assets", "ok": True,
                 "counts": {"side_effects": 1}, "data": {"assets": [{
                     "path": job.ops[0].body["paths"][0], "exists": True, "exported": True,
                     "bytes": path.stat().st_size, "output": str(output or path)}]}}]})


def test_real_bc3_portrait_decodes_and_preserves_transparency(svc):
    job = assets.prepare_job(svc, portrait_paths(190045), export=True)
    image_path = svc.paths.assets / job.job_id / "1" / "p190045.DDS"
    Image.new("RGBA", (180, 180), (20, 100, 200, 100)).save(image_path, pixel_format="DXT5")
    report = assets.process_result(svc, job, python_result(job, image_path))
    row = report["assets"][0]
    assert row["exported"] and row["portrait_id"] == 190045 and row["dimensions"] == [180, 180]
    with Image.open(row["preview"]) as preview:
        assert preview.mode == "RGBA" and preview.getpixel((80, 80))[3] == 100


def test_corrupt_dds_is_reported_without_fake_preview(svc):
    job = assets.prepare_job(svc, portrait_paths(190045), export=True)
    image_path = svc.paths.assets / job.job_id / "1" / "p190045.DDS"
    image_path.write_bytes(b"DDS " + b"\0" * 100)
    report = assets.process_result(svc, job, python_result(job, image_path))
    assert not report["ok"] and not report["assets"][0]["exported"]
    assert "preview" not in report["assets"][0]


def test_oversized_dds_dimensions_rejected_before_decode(svc):
    import struct
    job = assets.prepare_job(svc, portrait_paths(190045), export=True)
    image_path = svc.paths.assets / job.job_id / "1" / "p190045.DDS"
    header = bytearray(b"DDS " + b"\0" * 124)
    struct.pack_into("<II", header, 12, 99999, 99999)
    image_path.write_bytes(header)
    report = assets.process_result(svc, job, python_result(job, image_path))
    assert not report["ok"] and "dimensions" in report["assets"][0]["error"]


def test_incomplete_export_receipt_does_not_claim_success(svc):
    job = assets.prepare_job(svc, [DATABASE_ASSET], export=True)
    result = JobResult.from_wire({"job_id": job.job_id, "state": "done", "ok": True,
        "ops": [{"op": "assets.legacy", "id": "assets", "ok": True,
                 "data": {"assets": [{"path": DATABASE_ASSET, "exists": True}]}}]})
    assert not assets.process_result(svc, job, result)["ok"]


def test_worker_cannot_inspect_file_outside_its_own_export(svc, tmp_path):
    job = assets.prepare_job(svc, [DATABASE_ASSET], export=True)
    outside = tmp_path / "private.db"
    outside.write_bytes(b"sensitive")
    report = assets.process_result(svc, job, python_result(job, outside))
    assert not report["ok"] and "sha256" not in report["assets"][0]
    with pytest.raises(ValueError):
        assets.report_file(svc, job.job_id, str(outside))


def test_queued_follow_does_not_freeze_report_and_late_result_recovers(svc):
    job_id = assets.request(svc, [DATABASE_ASSET], export=True)
    assert not (svc.paths.assets / job_id / "report.json").exists()
    job = svc.transport.job(job_id)
    raw, result = execute(lua_host(svc), svc, job)
    svc.transport.complete(job_id, result)
    assets.recent_reports(svc)  # Inspector runs inline in this test.
    assert assets.recent_reports(svc)[0]["assets"][0]["exported"]
