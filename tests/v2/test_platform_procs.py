"""companion/platform/procs.py — pid liveness and process enumeration.

The liveness probe is load-bearing: ``session.json`` is pid-checked rather than
TTL-expired, so a wrong answer here shows the user a bridge that is off while
the game is happily draining the queue. These tests use this interpreter and a
short-lived child process; nothing needs FC 26 to be running.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from companion.platform import procs

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only platform layer")


@pytest.fixture()
def sleeper():
    """A real child process that we can kill mid-test."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


# --- liveness ---------------------------------------------------------------


def test_self_is_alive() -> None:
    assert procs.is_pid_alive(os.getpid()) is True


@pytest.mark.parametrize("pid", [0, -1, -12345])
def test_non_pids_are_not_alive(pid: int) -> None:
    assert procs.is_pid_alive(pid) is False


def test_absurd_pid_is_not_alive() -> None:
    # Windows pids are multiples of 4 below ~4 billion; this one is never live.
    assert procs.is_pid_alive(0x7FFFFFF0) is False


def test_probe_never_raises_on_junk() -> None:
    assert procs.is_pid_alive("nope") is False  # type: ignore[arg-type]
    assert procs.is_pid_alive(None) is False  # type: ignore[arg-type]


def test_child_alive_then_dead(sleeper: subprocess.Popen) -> None:
    assert procs.is_pid_alive(sleeper.pid) is True
    sleeper.kill()
    sleeper.wait(timeout=10)
    # The handle is signalled the moment the process terminates; no sleep needed.
    assert procs.is_pid_alive(sleeper.pid) is False


def test_exit_code_is_none_while_running(sleeper: subprocess.Popen) -> None:
    assert procs.process_exit_code(sleeper.pid) is None
    assert procs.process_exit_code(os.getpid()) is None


def test_exit_code_after_termination(sleeper: subprocess.Popen) -> None:
    sleeper.kill()
    sleeper.wait(timeout=10)
    assert isinstance(procs.process_exit_code(sleeper.pid), int)


def test_signature_matches_the_transport_port() -> None:
    """``FileTransport(paths, pid_probe=...)`` must accept this function as-is."""
    from companion.core.transport.v3 import FileTransport
    from companion.core.paths import TempAppPaths
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        transport = FileTransport(TempAppPaths(Path(tmp)), pid_probe=procs.is_pid_alive)
        assert transport.liveness().armed is False   # no session.json yet


# --- enumeration ------------------------------------------------------------


def test_list_processes_includes_self() -> None:
    table = procs.list_processes()
    assert len(table) > 5
    me = [p for p in table if p.pid == os.getpid()]
    assert me and me[0].name.lower().startswith("python")
    assert me[0].parent_pid > 0
    assert me[0].threads >= 1


def test_snapshot_can_be_reused() -> None:
    """One snapshot answers many questions — the 'cheap process list' contract."""
    table = procs.list_processes()
    assert procs.find_process_by_name("python", processes=table)
    assert procs.is_process_running("python.exe", processes=table)
    assert procs.find_process_by_name("definitely-not-running.exe", processes=table) == ()


def test_name_matching_is_forgiving() -> None:
    table = procs.list_processes()
    with_ext = procs.find_process_by_name("python.exe", processes=table)
    without_ext = procs.find_process_by_name("PYTHON", processes=table)
    assert with_ext and {p.pid for p in with_ext} == {p.pid for p in without_ext}


def test_empty_name_matches_nothing() -> None:
    assert procs.find_process_by_name("") == ()
    assert procs.first_process_by_name("") is None


def test_process_info_stem() -> None:
    assert procs.ProcessInfo(1, "FC26.exe").stem == "FC26"
    assert procs.ProcessInfo(1, "System").stem == "System"


def test_process_path_of_self() -> None:
    path = procs.process_path(os.getpid())
    assert path is not None
    assert path.name.lower().startswith("python")
    assert path.exists()


def test_process_path_of_nothing() -> None:
    assert procs.process_path(0x7FFFFFF0) is None
    assert procs.process_path(0) is None


def test_find_le_launcher_filters_by_install_root(tmp_path: Path) -> None:
    """A random Launcher.exe must not be mistaken for Live Editor's."""
    fake = (procs.ProcessInfo(os.getpid(), procs.LE_LAUNCHER_PROCESS),)
    assert procs.find_le_launcher(processes=fake) is not None
    assert procs.find_le_launcher(install_root=tmp_path, processes=fake) is None
    # ...and with the right root it matches again (this interpreter's own path).
    root = Path(sys.executable).parent
    assert procs.find_le_launcher(install_root=root, processes=fake) is not None


def test_find_game_is_none_when_fc26_is_not_running() -> None:
    table = tuple(p for p in procs.list_processes() if p.name.lower() != "fc26.exe")
    assert procs.find_game(processes=table) is None


def test_describe_is_plain_data() -> None:
    described = procs.describe()
    assert described["supported"] is True
    assert described["count"] > 0
    assert set(described) >= {"game_running", "game_pid", "le_running", "le_pid"}
    assert isinstance(described["game_running"], bool)


def test_describe_accepts_a_snapshot() -> None:
    fake = (procs.ProcessInfo(4242, procs.FC26_PROCESS),)
    described = procs.describe(processes=fake)
    assert described["game_running"] is True
    assert described["game_pid"] == 4242
    assert described["count"] == 1


def test_enumeration_spawns_no_console() -> None:
    """Toolhelp32, not ``tasklist``: a polling status bar must not flash a window.

    Asserted structurally — the module must not shell out at all.
    """
    source = Path(procs.__file__).read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert "os.system" not in source
    assert "CreateToolhelp32Snapshot" in source
