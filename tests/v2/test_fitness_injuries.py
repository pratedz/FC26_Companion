"""Run the Lua injury reader against isolated byte arrays, never the game."""
from pathlib import Path
import struct

import pytest

lupa = pytest.importorskip("lupa")
ROOT = Path(__file__).resolve().parents[2]
MANAGER, FITNESS, RECOVERY = 0x100000, 0x200000, 0x300000
CARLOS, PELE, OTHER = 73669, 85308, 123456


def fitness(pid, status=0, energy=100, date=20080101):
    return struct.pack("<III8B", pid, 0xFFFFFFFF, date, 0, 0, energy, status, 0, 31 if status > 1 else 0, 0, 0)


class Host:
    def __init__(self, rows=None):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.memory = {}
        self.reads, self.writes = [], []
        self.fail_once = None
        self.store(MANAGER, struct.pack("<Q", 0x140000000 + 0xB7A2460))
        rows = rows if rows is not None else [fitness(CARLOS, 2, 100, 20270401), fitness(PELE, 0, 60), fitness(OTHER, 3, 20, 20270501)]
        self.store(FITNESS, b"".join(rows))
        self.store(MANAGER + 0x3F38, struct.pack("<QQQ", FITNESS, FITNESS + len(rows) * 20, FITNESS + len(rows) * 20))
        self.store(RECOVERY, struct.pack("<III", CARLOS, 20270401, 1))
        self.store(MANAGER + 0x3F58, struct.pack("<QQQ", RECOVERY, RECOVERY + 12, RECOVERY + 12))
        self.lua.globals()._read = self.read
        self.lua.globals()._write = self.write
        self.lua.execute("""
            LE_VERSION = 'v26.3.5'
            LE_GAME_MODULE_BASE = 0x140000000
            function ReadBytes(addr, count) return _read(addr, count) end
            function WriteBytes(addr, bytes) return _write(addr, bytes) end
            function GetManagerObjByTypeId(id) assert(id == 47); return 0x100000 end
            function IsInCM() return true end
            function GetUserTeamID() return 2 end
            function GetUserSeniorTeamPlayerIDs() return {[73669]=true, [85308]=true} end
            function GetPlayerName(id) return id == 73669 and 'Carlos Alberto' or 'Pele' end
        """)
        self.reader = self.lua.execute((ROOT / "ingame/le_companion/ops/fitness_injuries.lua").read_text(encoding="utf-8"))
        self.lua.globals()._reader = self.reader
        self.lua.execute("package.loaded['ops.fitness_injuries'] = _reader")
        self.handler = self.lua.execute((ROOT / "ingame/le_companion/ops/injury.lua").read_text(encoding="utf-8"))

    def store(self, addr, data):
        self.memory.update({addr + i: value for i, value in enumerate(data)})

    def read(self, addr, n):
        addr, n = int(addr), int(n)
        self.reads.append((addr, n))
        return self.lua.table_from([self.memory[addr + i] for i in range(n)])

    def write(self, addr, data):
        values = bytes(data.values())
        self.writes.append((addr, values))
        if self.fail_once == addr:
            self.fail_once = None
            self.store(addr, values[:4])
            return False
        self.store(addr, values)
        return True

    def ids(self, *ids):
        return self.lua.table_from(ids)

    def bytes(self, addr, count):
        return bytes(self.memory[addr + i] for i in range(count))


def test_scan_detects_full_fitness_injury_and_excludes_tired_player():
    host = Host()
    result = host.reader.scan(host.ids(CARLOS, PELE, 99999))
    assert result.covered == 3
    assert result.squad_records == 2
    assert len(result.players) == 1
    assert result.players[1].playerid == CARLOS
    assert result.players[1].return_date == 20270401
    assert not host.writes


@pytest.mark.parametrize("status,expected", [(0, 0), (1, 0), (2, 1), (3, 1), (255, 0)])
def test_matches_native_signed_status_comparison(status, expected):
    host = Host([fitness(CARLOS, status, date=20270401)])
    assert len(host.reader.scan(host.ids(CARLOS)).players) == expected


def test_unknown_build_stops_before_vector_read_or_write():
    host = Host()
    host.store(MANAGER, struct.pack("<Q", 0x140000000 + 0x111111))
    result, error = host.reader.scan(host.ids(CARLOS))
    assert result is None and "different fitness layout" in error
    assert host.reads == [(MANAGER, 8)]
    assert not host.writes


def test_invalid_vector_stops_before_dereferencing_payload():
    host = Host()
    host.store(MANAGER + 0x3F38, struct.pack("<QQQ", 123, 456, 789))
    result, error = host.reader.scan(host.ids(CARLOS))
    assert result is None and "unsupported layout" in error
    assert all(addr != 123 for addr, _ in host.reads)
    assert not host.writes


def test_duplicate_player_is_an_error_instead_of_a_clear_scan():
    host = Host([fitness(CARLOS), fitness(CARLOS, 2, date=20270401)])
    result, error = host.reader.scan(host.ids(CARLOS))
    assert result is None and "Duplicate" in error


def test_heal_resets_both_native_records_and_leaves_other_players_untouched():
    host = Host()
    others = host.bytes(FITNESS + 20, 40)
    result = host.reader.cure(host.ids(CARLOS, PELE, CARLOS))
    assert list(result.cured_ids.values()) == [CARLOS]
    assert result.already_clear == 1
    assert host.bytes(FITNESS, 20) == fitness(0xFFFFFFFF)
    assert host.bytes(RECOVERY, 12) == struct.pack("<III", 0xFFFFFFFF, 20080101, 1)
    assert host.bytes(FITNESS + 20, 40) == others
    assert len(host.reader.scan(host.ids(CARLOS, PELE)).players) == 0


def test_failed_write_restores_both_records_and_does_not_touch_next_player():
    host = Host()
    before = dict(host.memory)
    host.fail_once = FITNESS
    result = host.reader.cure(host.ids(CARLOS, OTHER))
    assert not list(result.cured_ids.values())
    assert list(result.failed_ids.values()) == [CARLOS]
    assert host.memory == before
    assert all(address != FITNESS + 40 for address, _ in host.writes)


def test_handler_filters_team_members_and_dry_run_never_writes():
    host = Host()
    empty = host.lua.table()
    result = host.handler.scan(empty, empty, empty)
    assert result.ok and result.data.players[1].name == "Carlos Alberto"
    foreign = host.lua.table_from({"playerids": host.ids(OTHER)})
    result = host.handler.cure(foreign, empty, empty)
    assert not result.ok and result.reason == "no_targets"
    result = host.handler.cure(empty, host.lua.table_from({"dry_run": True}), empty)
    assert result.ok and not host.writes


def test_scan_and_heal_use_memory_object_without_global_bytes_api():
    host = Host()
    host.lua.execute("""
        MEMORY = {
            ReadBytes = function(_, addr, count) return _read(addr, count) end,
            WriteBytes = function(_, addr, bytes) return _write(addr, bytes) end,
        }
        ReadBytes = nil
        WriteBytes = nil
    """)
    scanned = host.reader.scan(host.ids(CARLOS, PELE))
    assert len(scanned.players) == 1
    assert scanned.players[1].playerid == CARLOS
    healed = host.reader.cure(host.ids(CARLOS))
    assert list(healed.cured_ids.values()) == [CARLOS]
    assert len(host.reader.scan(host.ids(CARLOS)).players) == 0


def test_unsupported_version_never_reads_memory():
    host = Host()
    host.lua.globals().LE_VERSION = "v26.3.6"
    result, error = host.reader.scan(host.ids(CARLOS))
    assert result is None and "not supported" in error
    assert not host.reads and not host.writes
