"""Check real drawer geometry in an invisible disabled window, using a DDS fixture."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import customtkinter as ctk
from PIL import Image

from companion.app.commands import legacy_assets as commands
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.legacy_assets import portrait_paths
from companion.domain.outcome import JobResult
from companion.ui.surfaces.activity import build


def main():
    with tempfile.TemporaryDirectory(prefix="companion-assets-ui-") as tmp:
        paths = TempAppPaths(Path(tmp))
        transport = FakeTransport()
        transport.set_liveness(replace(transport.liveness(), core_version="2.6.30",
            capabilities={"hub_ready": True, "legacy_file_check": True, "legacy_file_export": True}))
        db = Database(paths.state_db)
        svc = Services(paths, FakeClock(), InlineExecutor(), transport, Store(), db=db)
        job = commands.prepare_job(svc, portrait_paths(190045), export=True)
        image_path = paths.assets / job.job_id / "1" / "p190045.DDS"
        Image.new("RGBA", (180, 180), (20, 100, 200, 100)).save(image_path, pixel_format="DXT5")
        raw = {"job_id": job.job_id, "state": "done", "ok": True,
            "ops": [{"op": "assets.legacy", "id": "assets", "ok": True,
                "counts": {"side_effects": 1}, "data": {"le_version": "v26.3.6", "assets": [
                    {"path": portrait_paths(190045)[0], "exists": True, "exported": True,
                     "bytes": image_path.stat().st_size, "output": str(image_path)},
                    {"path": portrait_paths(190045)[1], "exists": False, "exported": False}]}}]}
        result = JobResult.from_wire(raw)
        db.record_submitted(job_id=job.job_id, job=job.to_wire())
        db.record_finished(job.job_id, outcome=result.outcome, result=raw)
        commands.process_result(svc, job, result)
        root = ctk.CTk()
        root.withdraw()
        # Tk does not settle geometry of deeply nested withdrawn widgets.
        # Map an invisible, disabled, undecorated window far off-screen so
        # geometry is real while the test cannot appear or take input focus.
        root.overrideredirect(True)
        root.attributes("-alpha", 0.0)
        root.attributes("-disabled", True)
        root.geometry("360x780+30000+30000")
        def callback_error(kind, value, trace):
            print(f"Callback error: {kind.__name__}: {value}", flush=True)
            while trace is not None:
                print(trace.tb_frame.f_code.co_filename, trace.tb_lineno, flush=True)
                trace = trace.tb_next
            root.destroy()
        root.report_callback_exception = callback_error
        try:
            drawer = ctk.CTkFrame(root, width=360, height=780)
            drawer.pack_propagate(False)
            drawer.place(x=0, y=0)
            body = build(drawer, svc, vm={"activity_segment": "assets"})
            body.pack(fill="both", expand=True, padx=16, pady=16)
            root.deiconify()
            root.update()
            root.update_idletasks()
            labels = []
            images = []

            def walk(widget):
                if isinstance(widget, (ctk.CTkLabel, ctk.CTkButton)):
                    labels.append(widget.cget("text"))
                    if isinstance(widget, ctk.CTkLabel) and widget.cget("image"):
                        images.append(widget)
                for child in widget.winfo_children():
                    walk(child)
            walk(body)
            assert "Export database" in labels and "Export & preview" in labels
            assert any("Missing" in label for label in labels)
            assert len(images) == 1
            controls = []
            def check_bounds(widget):
                if isinstance(widget, ctk.CTkButton) and widget.cget("text") in {
                    "Assets", "Export database", "Export & preview", "Check portrait", "Check", "Export"}:
                    if widget.winfo_width() <= 20:
                        current = widget
                        while current is not root:
                            print(type(current).__name__, current.winfo_width(), current.winfo_height(), current.winfo_reqwidth(), current.winfo_x(), current.winfo_y(), flush=True)
                            current = current.master
                    assert widget.winfo_width() > 20, f"Hidden control: {widget.cget('text')}"
                    parent = widget.master
                    assert widget.winfo_x() + widget.winfo_width() <= parent.winfo_width() + 1, widget.cget("text")
                    # Include the tab's containing column: its button can fit
                    # while the column itself is clipped out of the rail.
                    if widget.cget("text") == "Assets":
                        assert parent.winfo_x() + parent.winfo_width() <= parent.master.winfo_width() + 1
                    controls.append(widget.cget("text"))
                for child in widget.winfo_children():
                    check_bounds(child)
            check_bounds(body)
            assert len(controls) >= 6
            print("Hidden 360px Activity drawer smoke passed: visible asset tab/controls, missing-file row, DDS preview.")
        finally:
            try:
                root.destroy()
            except Exception:
                pass
            db.close()


if __name__ == "__main__":
    main()
