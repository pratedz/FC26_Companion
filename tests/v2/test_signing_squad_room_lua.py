"""Squad-room precheck, pre-write capture, restore and names.sync under lupa.

The real Lua modules run against a small fake Live Editor host: DB tables
with the LE cursor API, TransferPlayer that behaves like FC's (refuses at
52 including loaned-out players), IsPlayerLoanedOut, GetPlayerName that
resolves dictionary vs. editedplayernames like Career does.

Every DB write (SetRecordFieldValue / InsertDBTableRow) and every
TransferPlayer call is recorded, so "zero writes" is asserted directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "ingame" / "le_companion"
TEAM = 241
FREE = 111592


def _runtime():
    try:
        from lupa import lua54  # type: ignore[attr-defined]

        return lua54.LuaRuntime(unpack_returned_tuples=True)
    except ImportError:  # pragma: no cover
        return lupa.LuaRuntime(unpack_returned_tuples=True)


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


HOST = r"""
WRITES = {}          -- { {table, rec, field, value} }
TRANSFERS = {}       -- { {pid, teamid} }
LOANED = {}          -- [pid] = true
CONTRACTS = {}       -- [pid] = true (GetUserSeniorTeamPlayerIDs)
NATIVE_NAMES = {}    -- [pid] = name FC shows while the dictionary is attached
REFUSE_TRANSFER = false
FILL_ON_TRANSFER = 0 -- add this many squad links when TransferPlayer is called
DROP_NAME_WRITES = false
STALE_VISIBLE = false
USER_TEAM = 241

local function make_table(name, fields)
    local t = { name = name, rows = {}, fields = fields }
    t.written_records = 0
    function t:GetFirstRecord()
        self.cursor = 1
        if #self.rows >= 1 then return 1 end
        return 0
    end
    function t:GetNextValidRecord()
        self.cursor = (self.cursor or 0) + 1
        if self.cursor <= #self.rows then return self.cursor end
        return 0
    end
    function t:GetRecordFieldValue(rec, field)
        local row = self.rows[rec]
        if row == nil or row[field] == nil then error("no field " .. tostring(field)) end
        return row[field]
    end
    function t:SetRecordFieldValue(rec, field, value)
        WRITES[#WRITES + 1] = { self.name, rec, field, value }
        if self.name == "editedplayernames" and DROP_NAME_WRITES and field ~= "playerid" then
            return
        end
        self.rows[rec][field] = value
    end
    function t:add(row)
        self.rows[#self.rows + 1] = row
        self.written_records = #self.rows
        return #self.rows
    end
    return t
end

PLAYER_FIELDS = {
    "playerid", "overallrating", "potential", "isretiring", "acceleration",
    "headassetid", "firstnameid", "lastnameid", "commonnameid",
    "playerjerseynameid", "usercaneditname", "birthdate",
}
TABLES = {
    players = make_table("players", PLAYER_FIELDS),
    teamplayerlinks = make_table("teamplayerlinks", { "playerid", "teamid", "jerseynumber" }),
    editedplayernames = make_table("editedplayernames",
        { "playerid", "firstname", "surname", "playerjerseyname", "commonname" }),
}

GetDBTable = function(name) return TABLES[name] end
GetDBMeta = function()
    local shorts, descs = {}, {}
    for name, t in pairs(TABLES) do
        shorts[name] = name
        local list = {}
        for _, f in ipairs(t.fields) do
            -- editedplayernames strings: depth/8 is the max string length.
            local depth = (name == "editedplayernames" and f ~= "playerid") and 512 or 24
            list[#list + 1] = { name = f, depth = depth, min = 0 }
        end
        descs[name] = list
    end
    return { shortname_name_tables_map = shorts, field_desc_map = descs }
end
InsertDBTableRow = function(name, data)
    WRITES[#WRITES + 1] = { name, "insert", data.playerid, data.commonname }
    local row = {}
    for k, v in pairs(data) do row[k] = v end
    if name == "editedplayernames" and DROP_NAME_WRITES then
        row.commonname, row.firstname, row.surname, row.playerjerseyname = "", "", "", ""
    end
    local rec = TABLES[name]:add(row)
    return { addr = rec }
end

function player_row(pid)
    for i, row in ipairs(TABLES.players.rows) do
        if row.playerid == pid then return row, i end
    end
end

function link_of(pid)
    for _, row in ipairs(TABLES.teamplayerlinks.rows) do
        if row.playerid == pid then return row end
    end
end

function add_player(pid, teamid, values)
    local row = {
        playerid = pid, overallrating = 60, potential = 62, isretiring = 0,
        acceleration = 50, headassetid = 0, firstnameid = 100 + pid % 1000,
        lastnameid = 200 + pid % 1000, commonnameid = 0, playerjerseynameid = 0,
        usercaneditname = 0, birthdate = 150000,
    }
    for k, v in pairs(values or {}) do row[k] = v end
    TABLES.players:add(row)
    if teamid then TABLES.teamplayerlinks:add({ playerid = pid, teamid = teamid, jerseynumber = 0 }) end
end

function squad_total(teamid)
    local n = 0
    for _, row in ipairs(TABLES.teamplayerlinks.rows) do
        if row.teamid == teamid and not LOANED[row.playerid] then n = n + 1 end
    end
    for _ in pairs(LOANED) do n = n + 1 end
    return n
end

GetUserTeamID = function() return USER_TEAM end
PlayerExists = function(pid) return player_row(pid) ~= nil end
GetTeamIdFromPlayerId = function(pid)
    local link = link_of(pid)
    if link then return link.teamid end
    return -1
end
IsPlayerPresigned = function() return false end
IsPlayerLoanedOut = function(pid) return LOANED[pid] == true end
GetUserSeniorTeamPlayerIDs = function()
    local out = {}
    for _, row in ipairs(TABLES.teamplayerlinks.rows) do
        if row.teamid == USER_TEAM then out[row.playerid] = true end
    end
    for pid in pairs(CONTRACTS) do out[pid] = true end
    return out
end
TransferPlayer = function(pid, teamid)
    TRANSFERS[#TRANSFERS + 1] = { pid, teamid }
    for _ = 1, FILL_ON_TRANSFER do
        local filler = 900000 + #TABLES.teamplayerlinks.rows
        TABLES.teamplayerlinks:add({ playerid = filler, teamid = teamid, jerseynumber = 0 })
    end
    FILL_ON_TRANSFER = 0
    if REFUSE_TRANSFER or squad_total(teamid) >= 52 then return end
    link_of(pid).teamid = teamid
end
GetPlayerName = function(pid)
    local row = player_row(pid)
    if not row then return "" end
    local detached = row.firstnameid == 0 and row.lastnameid == 0 and row.commonnameid == 0
        and row.playerjerseynameid == 0 and row.usercaneditname == 1
    if detached and not STALE_VISIBLE then
        for _, name in ipairs(TABLES.editedplayernames.rows) do
            if tonumber(name.playerid) == pid then return name.commonname end
        end
        return ""
    end
    return NATIVE_NAMES[pid] or ("Dummy " .. pid)
end

function count_writes(pid)
    local n = 0
    for _, w in ipairs(WRITES) do
        if w[1] == "players" then
            if TABLES.players.rows[w[2]].playerid == pid then n = n + 1 end
        elseif w[1] == "editedplayernames" and w[2] == "insert" then
            if tonumber(w[3]) == pid then n = n + 1 end
        elseif w[1] == "editedplayernames" then
            if tonumber(TABLES.editedplayernames.rows[w[2]].playerid) == pid then n = n + 1 end
        elseif w[1] == "teamplayerlinks" then
            if TABLES.teamplayerlinks.rows[w[2]].playerid == pid then n = n + 1 end
        end
    end
    return n
end

-- Drive one op module the way the runner does: resume with progress until a
-- terminal result. An awaiting_event yield stands in for a settled hub event.
function drive(module_name, entry, op, job, ctx)
    local m = require(module_name)
    local res = m[entry](op, job, ctx)
    local guard = 0
    while res.deferred and guard < 80 do
        ctx.resume_state = res.progress or (res.data or {}).progress
        res = m[entry](op, job, ctx)
        guard = guard + 1
    end
    return res
end
"""


class Host:
    def __init__(self, lua, queue: Path):
        self.lua = lua
        self.g = lua.globals()
        self.queue = queue

    def run(self, code: str):
        return self.lua.execute(code)

    def eval(self, expr: str):
        return self.lua.eval(expr)

    def writes(self) -> int:
        return int(self.eval("#WRITES"))

    def transfers(self) -> int:
        return int(self.eval("#TRANSFERS"))

    def to_py(self, value):
        """Lua table -> plain Python (via the core's own JSON encoder)."""
        self.g._tmp = value
        text = self.eval("require('json').encode(_tmp)")
        return json.loads(text)

    def ctx(self, save="SAVE"):
        self.g._queue = _posix(self.queue)
        return self.eval(
            f"{{ save_uid = '{save}', queue_dir = _queue, cfg = {{ core_ops = {{ add_to_team = true }} }} }}"
        )

    def job(self, save="SAVE", grant=True):
        return self.eval(
            "{ grants = { allow_add_to_team = %s }, requires = { save_uid = '%s' } }"
            % ("true" if grant else "false", save)
        )

    def table(self, py):
        return self.eval(f"require('json').decode([==[{json.dumps(py)}]==])")

    def drive(self, module, entry, op, *, save="SAVE", job_save="SAVE", grant=True):
        ctx = self.ctx(save)
        res = self.g.drive(module, entry, self.table(op), self.job(job_save, grant), ctx)
        return self.to_py(res)


@pytest.fixture
def host(tmp_path: Path) -> Host:
    lua = _runtime()
    queue = tmp_path / "queue"
    (queue / "state").mkdir(parents=True)
    g = lua.globals()
    g.core_dir = _posix(CORE)
    lua.execute("package.path = core_dir .. '/?.lua;' .. package.path")
    lua.execute(HOST)
    return Host(lua, queue)


def _squad(host: Host, present: int, loaned: int) -> None:
    """``present`` players at the club, ``loaned`` loaned out to club 5."""
    host.run(
        f"""
        for i = 1, {present} do add_player(1000 + i, {TEAM}) end
        for i = 1, {loaned} do
            add_player(2000 + i, 5)
            LOANED[2000 + i] = true
            CONTRACTS[2000 + i] = true
        end
        """
    )


def _dummies(host: Host, *pids: int) -> None:
    for pid in pids:
        host.run(f"add_player({pid}, {FREE}, {{ overallrating = 55, potential = 60, acceleration = 41, headassetid = 7 }})")


def _member(pid: int, name: str = "Lionel Messi") -> dict:
    first, _, sur = name.partition(" ")
    return {
        "dummy_pool": [pid],
        "dummy_filter": {"max_overall": 75, "expected_overallrating": 55},
        "player": {
            "fields": {"overallrating": 90, "potential": 90, "acceleration": 88, "isretiring": 0},
            "face": {"headassetid": 158023},
            "names": {"firstname": first, "surname": sur, "commonname": name,
                      "playerjerseyname": sur},
            "name_strategy": "custom",
            "detach_name_dictionary": True,
        },
        "contract": {"transfersum": 0, "wage": 5000, "months": 60},
    }


def _sign_op(*members: dict) -> dict:
    return {"op": "add_to_team", "id": "add", "v": 4, "teamid": TEAM,
            "strategy": "dummy_overwrite", "batch": list(members),
            "verify": {"name": True, "team": True, "fields": True}}


# ---------------------------------------------------------------------------
# squad-room precheck
# ---------------------------------------------------------------------------


def test_full_squad_with_loans_is_refused_before_any_write(host):
    _squad(host, 42, 10)
    _dummies(host, 301)
    res = host.drive("ops.add_to_team", "step", _sign_op(_member(301)))
    assert res["ok"] is False
    assert res["reason"] == "squad_full"
    assert res["detail"] == "42 senior + 10 on loan = 52 of 52"
    assert res["failures"][0]["step"] == "resolve_team"
    room = res["data"]["progress"]["squad_room"]
    assert (room["present"], room["loaned"], room["total"], room["limit"], room["free"]) == (42, 10, 52, 52, 0)
    assert host.writes() == 0
    assert host.transfers() == 0
    # The dummy is untouched.
    assert host.eval("player_row(301).overallrating") == 55


def test_batch_needs_one_free_place_per_player_and_is_rejected_whole(host):
    _squad(host, 40, 10)
    _dummies(host, 301, 302, 303)
    res = host.drive("ops.add_to_team", "step",
                     _sign_op(_member(301), _member(302, "Pele Nascimento"), _member(303, "Zico Coimbra")))
    assert res["reason"] == "squad_full"
    assert res["detail"] == "40 senior + 10 on loan = 50 of 52; room for 2 of 3 players"
    assert res["data"]["progress"]["squad_room"]["needed"] == 3
    assert host.writes() == 0 and host.transfers() == 0


def test_loan_counting_does_not_double_count_a_loaned_player_still_linked(host):
    # Some saves keep the parent-club roster row while the player is away.
    _squad(host, 41, 0)
    host.run(f"add_player(2001, {TEAM}); LOANED[2001] = true")
    _dummies(host, 301)
    host.run("REFUSE_TRANSFER = false")
    res = host.drive("ops.add_to_team", "step", _sign_op(_member(301)))
    # 41 present + 1 on loan = 42: room, so the signing completes.
    assert res["ok"] is True, res
    assert res["data"]["playerids"] == [301]


def test_unreadable_loan_check_refuses_with_zero_writes(host):
    _squad(host, 30, 0)
    _dummies(host, 301)
    host.run("IsPlayerLoanedOut = nil")
    res = host.drive("ops.add_to_team", "step", _sign_op(_member(301)))
    assert res["reason"] in ("squad_check_unavailable", "slot_probe_unavailable")
    assert host.writes() == 0 and host.transfers() == 0


def test_signing_with_room_completes_and_names_are_visible(host):
    _squad(host, 40, 10)
    _dummies(host, 301, 302)
    res = host.drive("ops.add_to_team", "step",
                     _sign_op(_member(301), _member(302, "Pele Nascimento")))
    assert res["ok"] is True, res
    assert res["data"]["playerids"] == [301, 302]
    assert host.eval("GetPlayerName(301)") == "Lionel Messi"
    assert host.eval("link_of(302).teamid") == TEAM


def test_refusal_after_writes_captures_originals_and_never_overfills(host):
    """The squad fills between precheck and transfer: a partial, honestly."""
    _squad(host, 50, 1)
    _dummies(host, 301)
    host.run("FILL_ON_TRANSFER = 1")
    res = host.drive("ops.add_to_team", "step", _sign_op(_member(301)))
    assert res["ok"] is False and res["reason"] == "squad_full"
    assert "51 senior + 1 on loan = 52 of 52" in res["detail"]
    member = res["data"]["progress"]["members"][0]
    assert member["playerid"] == 301 and member["fields_written"] is True
    assert member["original_values"] == {
        "overallrating": 55, "potential": 60, "acceleration": 41,
        "isretiring": 0, "headassetid": 7,
    }
    assert member["written_values"] == {
        "overallrating": 90, "potential": 90, "acceleration": 88,
        "isretiring": 0, "headassetid": 158023,
    }
    # The roster-row fallback did not push the club to 53.
    assert host.eval("link_of(301).teamid") == FREE
    assert host.eval(f"squad_total({TEAM})") == 52


def test_roster_row_fallback_still_places_player_when_there_is_room(host):
    _squad(host, 30, 0)
    _dummies(host, 301)
    host.run("REFUSE_TRANSFER = true")
    res = host.drive("ops.add_to_team", "step", _sign_op(_member(301)))
    assert res["ok"] is True, res
    assert host.eval("link_of(301).teamid") == TEAM


def test_v3_single_path_has_the_same_precheck(host):
    _squad(host, 42, 10)
    _dummies(host, 301)
    member = _member(301)
    op = {"op": "add_to_team", "id": "add", "v": 3, "teamid": TEAM,
          "strategy": "dummy_overwrite", "dummy_pool": member["dummy_pool"],
          "dummy_filter": member["dummy_filter"], "player": member["player"],
          "contract": member["contract"]}
    res = host.drive("ops.add_to_team", "step", op)
    assert res["reason"] == "squad_full"
    assert host.writes() == 0 and host.transfers() == 0


# ---------------------------------------------------------------------------
# restore_partial_add
# ---------------------------------------------------------------------------


def _partial(host: Host) -> dict:
    _squad(host, 50, 1)
    _dummies(host, 301)
    host.run("FILL_ON_TRANSFER = 1")
    res = host.drive("ops.add_to_team", "step", _sign_op(_member(301)))
    member = res["data"]["progress"]["members"][0]
    return {"op": "restore_partial_add", "id": "restore", "source_job_id": "SIGN",
            "members": [{"playerid": 301, "expected": member["written_values"],
                         "restore": {k: member["original_values"][k] for k in member["written_values"]}}]}


def test_restore_puts_back_exactly_the_original_values(host):
    op = _partial(host)
    before = host.writes()
    res = host.drive("ops.restore_partial_add", "run", op)
    assert res["ok"] is True, res
    assert host.eval("player_row(301).overallrating") == 55
    assert host.eval("player_row(301).headassetid") == 7
    assert host.writes() - before == 5
    assert res["counts"]["fields_written"] == 5


def test_restore_refuses_with_zero_writes_when_values_changed(host):
    op = _partial(host)
    host.run("player_row(301).acceleration = 70")
    before = host.writes()
    res = host.drive("ops.restore_partial_add", "run", op)
    assert res["ok"] is False and res["reason"] == "dummy_changed"
    assert "acceleration is 70" in res["detail"]
    assert host.writes() == before


def test_restore_refuses_when_dummy_joined_a_club_or_save_differs(host):
    op = _partial(host)
    before = host.writes()
    host.run(f"link_of(301).teamid = {TEAM}")
    assert host.drive("ops.restore_partial_add", "run", op)["reason"] == "already_signed"
    host.run("link_of(301).teamid = 77")
    assert host.drive("ops.restore_partial_add", "run", op)["reason"] == "dummy_not_free"
    host.run(f"link_of(301).teamid = {FREE}")
    assert host.drive("ops.restore_partial_add", "run", op, job_save="OTHER")["reason"] == "save_mismatch"
    assert host.writes() == before


def test_restore_releases_a_consumed_dummy_entry(host):
    op = _partial(host)
    path = host.queue / "state" / "consumed_free_agent_dummies_SAVE.json"
    path.write_text(json.dumps({"schema": 1, "save_uid": "SAVE",
                                "playerids": {"301": {"consumed_at": 1}, "999": {"consumed_at": 1}}}))
    res = host.drive("ops.restore_partial_add", "run", op)
    assert res["ok"] is True and res["data"]["released_consumed"] == 1
    assert set(json.loads(path.read_text())["playerids"]) == {"999"}


# ---------------------------------------------------------------------------
# names.sync
# ---------------------------------------------------------------------------


def _signed(host: Host, pid: int, *, detached: bool, name: str | None = None) -> None:
    host.run(f"add_player({pid}, {TEAM})")
    if detached:
        host.run(
            f"""
            local r = player_row({pid})
            r.firstnameid, r.lastnameid, r.commonnameid, r.playerjerseynameid = 0, 0, 0, 0
            r.usercaneditname = 1
            """
        )
    if name:
        first, _, sur = name.partition(" ")
        host.run(
            f"TABLES.editedplayernames:add({{ playerid = '{pid}', firstname = '{first}', "
            f"surname = '{sur}', playerjerseyname = '{sur}', commonname = '{name}' }})"
        )


def _sync_entry(pid: int, name: str, **extra) -> dict:
    first, _, sur = name.partition(" ")
    entry = {"playerid": pid, "names": {"firstname": first, "surname": sur,
                                        "commonname": name, "playerjerseyname": sur},
             "name_strategy": "custom", "detach_name_dictionary": True}
    entry.update(extra)
    return entry


def _sync_op(*entries: dict, teamid: int = TEAM) -> dict:
    return {"op": "names.sync", "id": "names", "teamid": teamid, "players": list(entries)}


def test_names_sync_fixes_a_mismatch_and_skips_a_match(host):
    _squad(host, 5, 0)
    _signed(host, 701, detached=False)                       # shows "Dummy 701"
    _signed(host, 702, detached=True, name="Pele Nascimento")  # already right
    res = host.drive("ops.names_sync", "step",
                     _sync_op(_sync_entry(701, "Diego Maradona"), _sync_entry(702, "Pele Nascimento")))
    assert res["ok"] is True, res
    rows = {row["playerid"]: row for row in res["data"]["players"]}
    assert rows[701]["status"] == "fixed" and rows[701]["visible_name"] == "Diego Maradona"
    assert rows[701]["before_name"] == "Dummy 701"
    assert rows[702]["status"] == "already"
    assert host.eval("count_writes(702)") == 0
    assert host.eval("count_writes(701)") > 0
    assert host.eval("GetPlayerName(701)") == "Diego Maradona"


def test_names_sync_refuses_wrong_team_wrong_save_and_departed_player(host):
    _squad(host, 5, 0)
    _signed(host, 701, detached=False)
    op = _sync_op(_sync_entry(701, "Diego Maradona"))
    res = host.drive("ops.names_sync", "step", _sync_op(_sync_entry(701, "Diego Maradona"), teamid=999))
    assert res["reason"] == "team_changed"
    res = host.drive("ops.names_sync", "step", op, job_save="OTHER")
    assert res["reason"] == "save_mismatch"
    res = host.drive("ops.names_sync", "step", op, grant=False)
    assert res["reason"] == "grant_denied"
    host.run("link_of(701).teamid = 5")
    res = host.drive("ops.names_sync", "step", op)
    assert res["ok"] is False and res["data"]["players"][0]["status"] == "refused"
    assert res["data"]["players"][0]["reason"] == "not_on_team"
    assert host.writes() == 0


def test_names_sync_reports_a_read_back_failure_per_player(host):
    _squad(host, 5, 0)
    _signed(host, 701, detached=False)
    _signed(host, 702, detached=True, name="Pele Nascimento")
    host.run("DROP_NAME_WRITES = true")
    res = host.drive("ops.names_sync", "step",
                     _sync_op(_sync_entry(701, "Diego Maradona"), _sync_entry(702, "Pele Nascimento")))
    assert res["ok"] is False
    rows = {row["playerid"]: row for row in res["data"]["players"]}
    assert rows[701]["status"] == "failed"
    assert rows[701]["reason"] in ("name_not_applied", "verify_mismatch", "name_write_failed")
    assert rows[702]["status"] == "already"
    # The dictionary was never detached for a row that did not read back.
    assert host.eval("player_row(701).firstnameid") != 0
    assert [f["target"] for f in res["failures"]] == [701]


def test_names_sync_trusts_the_stored_identity_over_a_stale_le_cache(host):
    """2.6.42: GetPlayerName is LE's cache (rebuilt on a day advance / load).

    A stale cached name must neither fail the fix nor undo a detach whose
    stored identity reads back; it is only reported.
    """
    _squad(host, 5, 0)
    _signed(host, 701, detached=False)
    host.run("STALE_VISIBLE = true")
    res = host.drive("ops.names_sync", "step", _sync_op(_sync_entry(701, "Diego Maradona")))
    assert res["ok"] is True, res
    row = res["data"]["players"][0]
    assert row["status"] == "fixed" and row["visible_name"] == "Diego Maradona"
    assert row["le_cache_current"] is False
    assert row["le_cache_name"] == "Dummy 701"
    assert not row.get("restored")
    assert host.eval("player_row(701).firstnameid") == 0
    assert host.eval("player_row(701).usercaneditname") == 1


def test_names_sync_undoes_a_detach_whose_identity_does_not_read_back(host):
    _squad(host, 5, 0)
    _signed(host, 701, detached=False)
    original = host.eval("player_row(701).firstnameid")
    # FC refuses the dictionary detach for this row: the ids never become 0.
    host.run(
        """
        local t = TABLES.players
        local real = t.SetRecordFieldValue
        t.SetRecordFieldValue = function(self, rec, field, value)
            if field == "usercaneditname" then return end
            return real(self, rec, field, value)
        end
        """
    )
    res = host.drive("ops.names_sync", "step", _sync_op(_sync_entry(701, "Diego Maradona")))
    row = res["data"]["players"][0]
    assert row["status"] == "failed"
    assert row["restored"] is True
    assert host.eval("player_row(701).firstnameid") == original


def test_names_sync_native_identity_writes_dictionary_ids(host):
    _squad(host, 5, 0)
    _signed(host, 701, detached=False)
    host.run("NATIVE_NAMES[701] = 'Messi'")
    host.run("player_row(701).firstnameid = 1")  # wrong ids; FC shows "Messi" only after fix
    host.run("""
        local real = GetPlayerName
        GetPlayerName = function(pid)
            local r = player_row(pid)
            if pid == 701 and r.firstnameid ~= 11 then return 'Dummy 701' end
            return real(pid)
        end
    """)
    entry = _sync_entry(701, "Lionel Messi", name_strategy="native_ids",
                        native_name_ids={"firstnameid": 11, "lastnameid": 12,
                                         "commonnameid": 13, "playerjerseynameid": 0})
    entry["names"]["playerjerseyname"] = "Messi"
    res = host.drive("ops.names_sync", "step", _sync_op(entry))
    assert res["ok"] is True, res
    assert res["data"]["players"][0]["status"] == "fixed"
    assert host.eval("player_row(701).commonnameid") == 13
    assert host.eval("player_row(701).usercaneditname") == 0
    assert host.eval("#TABLES.editedplayernames.rows") == 0


def test_names_sync_is_registered_and_guarded_in_the_core():
    ops = (CORE / "ops.lua").read_text(encoding="utf-8")
    runner = (CORE / "runner.lua").read_text(encoding="utf-8")
    version = (CORE / "version.lua").read_text(encoding="utf-8")
    assert 'ops.handlers["names.sync"]' in ops and 'ops.handlers["restore_partial_add"]' in ops
    assert 'op.op == "names.sync"' in runner and 'op.op == "restore_partial_add"' in runner
    assert '["names.sync"] = "allow_add_to_team"' in version
    assert 'restore_partial_add = "allow_add_to_team"' in version
    assert "max_squad_with_loans = 52" in version


# ---------------------------------------------------------------------------
# Through the real runner: claim, validate, grant, save precondition, result.
# ---------------------------------------------------------------------------

_ULID = "01J0000000000000000000NAME"


def _runner_core(tmp_path: Path):
    import os

    lua = _runtime()
    queue = tmp_path / "rqueue"
    for sub in ("jobs", "claimed", "results", "state", "poison"):
        (queue / sub).mkdir(parents=True)
    g = lua.globals()
    g.core_dir = _posix(CORE)
    g.ingame_root = _posix(CORE.parent)
    g.queue_dir = _posix(queue)
    g.py_dir_list = lambda path: lua.table_from(sorted(os.listdir(str(path)))) if Path(str(path)).is_dir() else None
    lua.execute(HOST)
    lua.execute(
        """
        CLOCK = 100.0
        _G.__mock_clock = function() return CLOCK end
        _G.__mock_time = function() return 1700000000 end
        IsInCM = function() return true end
        GetSaveUID = function() return "SAVE" end
        LEC_DIR_LIST = function(dir) return py_dir_list(dir) end
        package.loadlib = function() return nil, "stubbed", "open" end
        handlers = {}
        AddEventHandler = function(name, fn) handlers[name] = fn end
        package.path = ingame_root .. "/?.lua;" .. ingame_root .. "/?/init.lua;" .. package.path
        require("le_companion.init")
        LEC.configure({ queue_dir = queue_dir, core_ops = { "add_to_team" } })
        assert(LEC.arm())
        """
    )
    return lua, queue


def _run_job(lua, queue: Path, op: dict, *, save: str = "SAVE") -> dict:
    body = {"schema": 3, "job_id": _ULID, "label": "Fix names", "ops": [op],
            "budget_ms": 5000, "grants": {"allow_add_to_team": True},
            "requires": {"save_uid": save}}
    (queue / "jobs" / f"{_ULID}.json").write_text(json.dumps(body), encoding="utf-8")
    for _ in range(4):
        lua.eval("LEC.runner.on_career(26, 'post')")
        done = queue / "results" / f"{_ULID}.json"
        if done.exists():
            return json.loads(done.read_text(encoding="utf-8"))
    raise AssertionError("job did not finish")


def test_runner_executes_names_sync_end_to_end(tmp_path):
    lua, queue = _runner_core(tmp_path)
    lua.execute(f"for i = 1, 5 do add_player(1000 + i, {TEAM}) end; add_player(701, {TEAM})")
    result = _run_job(lua, queue, {"id": "names", **_sync_op(_sync_entry(701, "Diego Maradona"))})
    assert result["state"] == "done", result
    assert result["ops"][0]["data"]["players"][0]["status"] == "fixed"
    assert lua.eval("GetPlayerName(701)") == "Diego Maradona"


def test_runner_refuses_names_sync_for_another_save_before_any_write(tmp_path):
    lua, queue = _runner_core(tmp_path)
    lua.execute(f"add_player(701, {TEAM})")
    result = _run_job(lua, queue, {"id": "names", **_sync_op(_sync_entry(701, "Diego Maradona"))},
                      save="OTHER")
    assert result["state"] == "rejected"
    assert result["failures"][0]["reason"] == "wrong_save"
    assert lua.eval("#WRITES") == 0
