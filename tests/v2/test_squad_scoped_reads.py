from dataclasses import replace

from companion.app import events as E
from companion.app.commands.squad_session import snapshot_players
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport


def test_read_refuses_callback_after_save_switch_and_binds_job(tmp_path):
    svc = Services(paths=TempAppPaths(tmp_path), clock=FakeClock(), executor=InlineExecutor(),
                   store=Store(), transport=FakeTransport())
    svc.store.dispatch(E.SquadSynced(save_uid="A", teamid=11, players=({"playerid": 1},), taken=svc.clock.now()))
    loaded, errors = [], []

    def submit(job):
        assert job.requires["save_uid"] == "A"
        svc.store.dispatch(E.SquadSynced(save_uid="B", teamid=22, players=({"playerid": 1},), taken=svc.clock.now()))
        svc.transport.complete(job.job_id, {"state": "done", "ok": True, "env": {"save_uid": "A"},
            "ops": [{"op": "snapshot", "id": "squad.session.read", "ok": True,
                     "data": {"rows": [{"playerid": 1, "overallrating": 90}]}}]})

    svc.transport.on_submit = submit
    snapshot_players(svc, [1], on_rows=loaded.append, on_error=errors.append)
    assert loaded == [] and len(errors) == 1 and "changed" in str(errors[0])
