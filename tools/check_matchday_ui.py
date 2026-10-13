"""Exercise the real Matchday widgets off-screen, with isolated fake game data."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import customtkinter as ctk
from companion.app import events as E
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.outcome import JobResult
from companion.ui.surfaces.automations import build


def main():
    with ExitStack() as resources:
        tmp = resources.enter_context(tempfile.TemporaryDirectory(prefix="companion-matchday-ui-"))
        paths = TempAppPaths(Path(tmp))
        transport = FakeTransport()
        transport.set_liveness(replace(transport.liveness(), save_uid="SAVE", core_version="2.6.31"))
        db = resources.enter_context(Database(paths.state_db))
        svc = Services(paths, FakeClock(), InlineExecutor(), transport, Store(), db=db)
        svc.store.dispatch(E.LivenessChanged(transport.liveness()))
        for width, height in ((800, 600), (1040, 700)):
            root = ctk.CTk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-alpha", 0)
            root.attributes("-disabled", True)
            root.geometry(f"{width}x{height}+30000+30000")
            errors = []
            root.report_callback_exception = lambda kind, value, trace: errors.append(str(value))
            try:
                page = build(root, svc, {})
                page.pack(fill="both", expand=True)
                root.deiconify()
                root.update()
                def check():
                    root.update()
                    buttons = []
                    def walk(widget):
                        if isinstance(widget, ctk.CTkButton) and widget.winfo_ismapped():
                            buttons.append(widget.cget("text"))
                            assert widget.winfo_rootx() >= root.winfo_rootx(), widget.cget("text")
                            assert widget.winfo_rootx()+widget.winfo_width() <= root.winfo_rootx()+root.winfo_width()+1, widget.cget("text")
                            assert widget.winfo_rooty()+widget.winfo_height() <= root.winfo_rooty()+root.winfo_height()+1, (widget.cget("text"), widget.winfo_rooty()-root.winfo_rooty(), root.winfo_height())
                        for child in widget.winfo_children():
                            walk(child)
                    walk(page)
                    assert "Open Club" in buttons and "Review injuries" in buttons, (buttons, page.winfo_reqheight(), root.winfo_height())
                    assert "Manage queue" not in buttons
                    assert not errors, errors
                check()
                page.matchday_adjust_button.invoke()
                check()
                page.matchday_variables["form"].set(False)
                page.refresh_automation_queue()
                page.matchday_adjust_button.invoke()
                check()
                count = len(transport.submitted)
                page.matchday_prepare_button.invoke()
                job_id = transport.submitted[-1].job_id
                page.refresh_automation_queue()
                assert page.matchday_prepare_button.cget("state") == "disabled"
                page.matchday_prepare_button.invoke()
                assert len(transport.submitted) == count+1
                check()
                result = JobResult.from_wire({"job_id":job_id,"state":"done","ok":True})
                svc.store.dispatch(E.JobFinished(job_id=job_id,result=result))
                db.record_finished(job_id,outcome=result.outcome,result={"job_id":job_id,"state":"done","ok":True},save_uid="SAVE")
                page.refresh_automation_queue()
                assert page.matchday_prepare_button.cget("state") == "normal"
                assert page.matchday_variables["form"].get() is False
                check()
                print(f"Hidden Matchday UI passed at {width}x{height}: routine, queued/result states, controls fit.")
            finally:
                for token in root.tk.call("after", "info"):
                    root.after_cancel(token)
                root.destroy()


if __name__ == "__main__":
    main()
