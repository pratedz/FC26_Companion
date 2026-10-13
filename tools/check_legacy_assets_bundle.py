"""Exercise the packaged CLI's real DDS decoder with isolated data, no game."""
from pathlib import Path
import argparse
import json
import logging
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from companion.bootstrap import build_services
from companion.app.commands.legacy_assets import prepare_job
from companion.domain.legacy_assets import portrait_paths
from companion.domain.outcome import JobResult


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="companion-assets-bundle-") as tmp:
        root = Path(tmp)
        svc = build_services(root, inline=True, logger=logging.getLogger("asset-bundle-smoke"))
        job = prepare_job(svc, portrait_paths(190045), export=True)
        dds = svc.paths.assets / job.job_id / "1" / "p190045.DDS"
        Image.new("RGBA", (180, 180), (20, 100, 200, 100)).save(dds, pixel_format="DXT5")
        raw = {"schema": 3, "job_id": job.job_id, "state": "done", "ok": True,
            "ops": [{"op": "assets.legacy", "id": "assets", "ok": True,
                "counts": {"side_effects": 1}, "data": {"le_version": "v26.3.6", "assets": [
                    {"path": portrait_paths(190045)[0], "exists": True, "exported": True,
                     "bytes": dds.stat().st_size, "output": str(dds)},
                    {"path": portrait_paths(190045)[1], "exists": False, "exported": False}]}}]}
        svc.db.record_submitted(job_id=job.job_id, job=job.to_wire())
        svc.db.record_finished(job.job_id, outcome=JobResult.from_wire(raw).outcome, result=raw)
        (svc.paths.results / f"{job.job_id}.json").write_text(json.dumps(raw), encoding="utf-8")
        svc.db.close()
        svc.executor.shutdown()
        completed = subprocess.run([str(args.executable.resolve()), "--root", str(root), "assets", "reports"],
                                   capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode:
            raise RuntimeError(f"Packaged asset CLI failed ({completed.returncode})")
        report = json.loads((svc.paths.assets / job.job_id / "report.json").read_text(encoding="utf-8"))
        assert report["ok"] and report["assets"][0]["exported"]
        with Image.open(report["assets"][0]["preview"]) as image:
            assert image.size == (180, 180) and image.mode == "RGBA"
        assert report["assets"][1]["exists"] is False
        print("Packaged assets CLI passed: persisted result recovery, DDS/BC3 decoder and transparent PNG preview.")


if __name__ == "__main__":
    main()
