"""Squad-level snapshot / restore safety net.

Every test runs against a tmp_path app root (conftest's `isolated_app_root`
monkeypatches paths.app_root); the session guard in conftest fails the run if
real user state is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src import squad_snapshot


PLAYERS = [
    {
        "playerid": 158023,
        "name": "L. Messi",
        "position": "RW",
        "overallrating": 90,
        "potential": 93,
        "jerseynumber": 10,
    },
    {
        "playerid": 20801,
        "name": "Cristiano Ronaldo",
        "position": "ST",
        "overallrating": 88,
        "potential": 88,
        "jerseynumber": 7,
    },
    {
        "playerid": 231747,
        "name": "K. Mbappe",
        "position": "LW",
        "overallrating": 91,
        "potential": 95,
        "jerseynumber": 9,
    },
]


def _write_squad(root: Path, players: Optional[List[Dict[str, Any]]] = None) -> Path:
    rows = PLAYERS if players is None else players
    data = {
        "mode": "career",
        "teamid": 243,
        "teamname": "Test FC",
        "count": len(rows),
        "players": rows,
        "free_agents": [],
    }
    p = root / "current_squad.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _put_raw_snapshot(payload: Dict[str, Any]) -> str:
    """Write a snapshot file + index row without going through capture."""
    sid = str(payload["id"])
    (squad_snapshot.squad_dir() / f"{sid}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    idx_path = squad_snapshot.squad_dir() / "index.json"
    rows: List[Dict[str, Any]] = []
    if idx_path.is_file():
        rows = json.loads(idx_path.read_text(encoding="utf-8"))
    rows.insert(
        0,
        {
            "id": sid,
            "ts": payload.get("ts", 0),
            "label": payload.get("label", ""),
            "kind": payload.get("kind", "batch"),
            "source": payload.get("source", "db_capture"),
            "count": len(payload.get("players") or []),
            "n_fields": 0,
            "path": f"{sid}.json",
        },
    )
    idx_path.write_text(json.dumps(rows), encoding="utf-8")
    return sid


# ── capture / list / get ─────────────────────────────────────────────

def test_capture_list_get_roundtrip(isolated_app_root: Path) -> None:
    _write_squad(isolated_app_root)

    snap = squad_snapshot.capture_from_squad("Before pack", kind="pack")
    assert snap is not None
    sid = snap["id"]
    assert snap["count"] == 3
    assert snap["teamname"] == "Test FC"
    assert snap["source"] == "squad_export"
    assert snap["label"] == "Before pack"
    assert snap["ts"] > 0

    rows = squad_snapshot.list_squad_snapshots()
    assert len(rows) == 1
    assert rows[0]["id"] == sid
    assert rows[0]["count"] == 3

    got = squad_snapshot.get_squad_snapshot(sid)
    assert got is not None
    assert got["id"] == sid
    by_id = {p["playerid"]: p for p in got["players"]}
    assert by_id[158023]["values"]["overallrating"] == 90
    assert by_id[158023]["values"]["potential"] == 93
    assert by_id[158023]["name"] == "L. Messi"

    line = squad_snapshot.format_snapshot_line(rows[0], 0)
    assert "Before pack" in line
    assert "players=3" in line
    assert sid in line

    # stored on disk, index paths are bare basenames only
    assert (isolated_app_root / "snapshots" / "squad" / f"{sid}.json").is_file()
    raw_index = json.loads(
        (isolated_app_root / "snapshots" / "squad" / "index.json").read_text(
            encoding="utf-8"
        )
    )
    for row in raw_index:
        assert row["path"] == f"{row['id']}.json"
        assert "/" not in row["path"] and "\\" not in row["path"]

    assert squad_snapshot.delete_squad_snapshot(sid) is True
    assert squad_snapshot.get_squad_snapshot(sid) is None
    assert squad_snapshot.list_squad_snapshots() == []


def test_cap_evicts_oldest(isolated_app_root: Path) -> None:
    _write_squad(isolated_app_root)
    cap = squad_snapshot.MAX_SQUAD_SNAPSHOTS
    made = []
    for i in range(cap + 2):
        snap = squad_snapshot.capture_from_squad(f"batch {i}")
        assert snap is not None
        made.append(snap["id"])

    rows = squad_snapshot.list_squad_snapshots(limit=100)
    assert len(rows) == cap
    ids = [r["id"] for r in rows]
    # newest first
    assert ids[0] == made[-1]
    # the two oldest were evicted from index *and* disk
    for sid in made[:2]:
        assert sid not in ids
        assert squad_snapshot.get_squad_snapshot(sid) is None
        assert not (isolated_app_root / "snapshots" / "squad" / f"{sid}.json").is_file()
    files = list((isolated_app_root / "snapshots" / "squad").glob("*.json"))
    assert len(files) == cap + 1  # snapshots + index.json


# ── path confinement ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "bad",
    [
        "../../evil",
        "..\\evil",
        "sub/dir",
        "C:\\Windows\\System32\\evil",
        "..",
        "",
        "a" * 200,
        "weird;name",
    ],
)
def test_path_escape_ids_rejected(isolated_app_root: Path, bad: str) -> None:
    _write_squad(isolated_app_root)
    assert squad_snapshot.capture_from_squad("ok") is not None
    assert squad_snapshot.get_squad_snapshot(bad) is None
    assert squad_snapshot.delete_squad_snapshot(bad) is False
    with pytest.raises(ValueError):
        squad_snapshot.generate_squad_restore_lua(bad)


def test_index_path_cannot_escape_snapshots_dir(isolated_app_root: Path) -> None:
    """A hand-edited index pointing outside snapshots/squad must not be read."""
    outside = isolated_app_root.parent / "outside.json"
    outside.write_text(
        json.dumps({"id": "escaped", "players": [{"playerid": 1, "values": {}}]}),
        encoding="utf-8",
    )
    idx = squad_snapshot.squad_dir() / "index.json"
    idx.write_text(
        json.dumps(
            [{"id": "escaped", "ts": 1, "label": "x", "path": "../../outside.json"}]
        ),
        encoding="utf-8",
    )
    assert squad_snapshot.get_squad_snapshot("escaped") is None
    assert outside.is_file()  # untouched
    assert squad_snapshot.delete_squad_snapshot("escaped") is True
    assert outside.is_file()  # deletion never escaped either


# ── restore Lua ──────────────────────────────────────────────────────

def test_restore_lua_contains_only_validated_fields(isolated_app_root: Path) -> None:
    _write_squad(isolated_app_root)
    snap = squad_snapshot.capture_from_squad("Before batch")
    assert snap is not None
    lua = squad_snapshot.generate_squad_restore_lua(snap["id"])

    assert '{"overallrating", 90}' in lua
    assert '{"potential", 93}' in lua
    assert '"158023"' in lua and '"20801"' in lua and '"231747"' in lua
    # jerseynumber is not a players-table edit field → captured but never written
    assert "jerseynumber" not in lua
    assert "name" not in lua.split("local PLAYERS")[1].split("\n}")[0]
    # freeze-safe pattern + status line contract
    assert 'GetDBTableRows, "players"' in lua
    assert "job=squad_restore" in lua
    assert "ok=%s" in lua and "written=%d" in lua and "failed=%d" in lua
    assert "ReloadPlayersManager" in lua  # only inside the "never call" comment
    assert "ReloadPlayersManager(" not in lua
    assert f"sid={snap['id']}" in lua

    # every quoted key inside the PLAYERS table is a real schema field
    from src import player_schema

    table = lua.split("local PLAYERS = {", 1)[1].split("\n}", 1)[0]
    import re

    for name in re.findall(r'\{"([A-Za-z_][A-Za-z0-9_]*)",', table):
        assert name in player_schema.ALL_EDIT_FIELDS


def test_restore_lua_rejects_injected_field_name(isolated_app_root: Path) -> None:
    """A hand-edited / legacy snapshot cannot smuggle Lua through a field name."""
    evil = '0}} pcall(os.execute, "calc") --'
    sid = _put_raw_snapshot(
        {
            "id": "inject01",
            "ts": 1.0,
            "label": "tampered",
            "kind": "batch",
            "source": "db_capture",
            "teamname": "Test FC",
            "count": 1,
            "players": [
                {
                    "playerid": 158023,
                    "name": "L. Messi",
                    "values": {
                        "overallrating": 90,
                        evil: 1,
                        "notarealfield": 5,
                        "../../etc": 7,
                        "potential": "not-an-int",
                    },
                }
            ],
        }
    )
    lua = squad_snapshot.generate_squad_restore_lua(sid)
    assert '{"overallrating", 90}' in lua
    assert "os.execute" not in lua
    assert "notarealfield" not in lua
    assert "../../etc" not in lua
    assert "not-an-int" not in lua

    # a snapshot whose fields are *all* invalid restores nothing, loudly
    bad_sid = _put_raw_snapshot(
        {
            "id": "inject02",
            "ts": 2.0,
            "label": "all bad",
            "kind": "batch",
            "players": [{"playerid": 5, "values": {"notarealfield": 1}}],
        }
    )
    with pytest.raises(ValueError):
        squad_snapshot.generate_squad_restore_lua(bad_sid)


def test_restore_lua_label_and_comment_cannot_break_out(
    isolated_app_root: Path,
) -> None:
    _write_squad(isolated_app_root)
    snap = squad_snapshot.capture_from_squad('pack ]] os.execute("calc") --[[')
    assert snap is not None
    lua = squad_snapshot.generate_squad_restore_lua(snap["id"])
    # The label survives as inert text, but it can neither close the header
    # comment early nor break out of the LABEL string literal.
    header = lua.split("\n", 1)[0]
    assert header.startswith("--[[")
    assert header.endswith("]]")
    assert header.count("[[") == 1
    assert header.count("]]") == 1
    label_line = next(ln for ln in lua.splitlines() if ln.startswith("local LABEL ="))
    assert label_line.count('"') == 2  # one closed string literal, no escape-out


# ── capture Lua ──────────────────────────────────────────────────────

def test_capture_lua_is_guarded_and_never_throws(isolated_app_root: Path) -> None:
    lua = squad_snapshot.generate_squad_capture_lua([158023, "20801", 158023, 0, -4, None, "x"])
    assert 'type(GetDBTableRows) ~= "function"' in lua
    assert "pcall(GetDBTableRows" in lua
    assert 'local ok_main = pcall(function()' in lua
    assert "job=squad_capture" in lua
    # ids deduped / sanitised
    assert '["158023"] = true' in lua
    assert '["20801"] = true' in lua
    assert "local WANT_N = 2" in lua
    assert '["-4"]' not in lua and '["x"]' not in lua and '["0"]' not in lua
    # rich field set, all schema-validated
    fields_block = lua.split("local FIELDS = {", 1)[1].split("\n}", 1)[0]
    from src import player_schema

    names = [ln.strip().strip('",') for ln in fields_block.splitlines() if ln.strip()]
    assert len(names) > 20
    for n in names:
        assert n in player_schema.ALL_EDIT_FIELDS
    assert "overallrating" in names and "sprintspeed" in names
    # writes JSON under the app root
    out = str((isolated_app_root / "snapshots" / "squad" / "_db_capture.json").resolve())
    assert out.replace("\\", "/") in lua


def test_capture_lua_field_list_is_validated(isolated_app_root: Path) -> None:
    lua = squad_snapshot.generate_squad_capture_lua(
        [1],
        fields=[
            "overallrating",
            'x"} pcall(os.execute, "calc") --',
            "notarealfield",
            "potential",
        ],
    )
    assert '"overallrating",' in lua
    assert '"potential",' in lua
    assert "os.execute" not in lua
    assert "notarealfield" not in lua
    # empty input must not raise either
    assert "job=squad_capture" in squad_snapshot.generate_squad_capture_lua([])


def test_ingest_db_capture_gives_full_fidelity(isolated_app_root: Path) -> None:
    _write_squad(isolated_app_root)
    squad_snapshot.db_capture_path().write_text(
        json.dumps(
            {
                "kind": "squad_db_capture",
                "teamid": 243,
                "teamname": "Test FC",
                "count": 1,
                "players": [
                    {
                        "playerid": 158023,
                        "values": {
                            "overallrating": 90,
                            "sprintspeed": 80,
                            "trait1": 4096,
                            "notarealfield": 3,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    snap = squad_snapshot.ingest_db_capture("Before mass edit")
    assert snap is not None
    assert snap["source"] == "db_capture"
    vals = snap["players"][0]["values"]
    assert vals["sprintspeed"] == 80
    assert vals["trait1"] == 4096
    assert "notarealfield" not in vals
    # name merged from the squad export for display
    assert snap["players"][0]["name"] == "L. Messi"

    lua = squad_snapshot.generate_squad_restore_lua(snap["id"])
    assert '{"sprintspeed", 80}' in lua
    assert '{"trait1", 4096}' in lua


# ── diff ─────────────────────────────────────────────────────────────

def test_diff_detects_changed_ovr(isolated_app_root: Path) -> None:
    _write_squad(isolated_app_root)
    snap = squad_snapshot.capture_from_squad("Before boost")
    assert snap is not None

    after = [dict(p) for p in PLAYERS]
    after[0]["overallrating"] = 99  # Messi boosted
    after[0]["potential"] = 99
    after.pop(1)  # Ronaldo sold
    after.append(
        {
            "playerid": 999999,
            "name": "New Guy",
            "position": "CB",
            "overallrating": 70,
            "potential": 80,
            "jerseynumber": 4,
        }
    )
    _write_squad(isolated_app_root, after)

    d = squad_snapshot.diff_against_squad(snap["id"])
    assert d is not None
    assert d["ok"] is True
    assert len(d["changed"]) == 1
    ch = d["changed"][0]
    assert ch["playerid"] == 158023
    fields = {c["field"]: (c["from"], c["to"]) for c in ch["changes"]}
    assert fields["overallrating"] == (90, 99)
    assert fields["potential"] == (93, 99)
    assert "OVR 90→99" in ch["line"]
    assert "L. Messi" in d["lines"][0]
    assert [m["playerid"] for m in d["missing"]] == [20801]
    assert [a["playerid"] for a in d["added"]] == [999999]
    assert d["unchanged"] == 1
    assert "1 changed" in d["summary"]

    assert squad_snapshot.diff_against_squad("nope") is None


def test_diff_reports_no_changes_when_squad_untouched(isolated_app_root: Path) -> None:
    _write_squad(isolated_app_root)
    snap = squad_snapshot.capture_from_squad("Before nothing")
    assert snap is not None
    d = squad_snapshot.diff_against_squad(snap["id"])
    assert d is not None
    assert d["changed"] == [] and d["missing"] == [] and d["added"] == []
    assert d["unchanged"] == 3


# ── graceful degradation ─────────────────────────────────────────────

def test_no_squad_export_degrades_gracefully(isolated_app_root: Path) -> None:
    assert not (isolated_app_root / "current_squad.json").exists()
    assert squad_snapshot.capture_from_squad("nothing to capture") is None
    assert squad_snapshot.list_squad_snapshots() == []
    assert squad_snapshot.get_squad_snapshot("whatever") is None
    assert squad_snapshot.delete_squad_snapshot("whatever") is False
    assert squad_snapshot.ingest_db_capture("no capture file") is None
    assert squad_snapshot.load_db_capture() is None

    # an empty / malformed export is not a snapshot either
    _write_squad(isolated_app_root, [])
    assert squad_snapshot.capture_from_squad("empty") is None
    (isolated_app_root / "current_squad.json").write_text("{ not json", encoding="utf-8")
    assert squad_snapshot.capture_from_squad("broken") is None


def test_diff_without_squad_export_is_not_an_error(isolated_app_root: Path) -> None:
    _write_squad(isolated_app_root)
    snap = squad_snapshot.capture_from_squad("Before batch")
    assert snap is not None
    (isolated_app_root / "current_squad.json").unlink()

    d = squad_snapshot.diff_against_squad(snap["id"])
    assert d is not None
    assert d["ok"] is False
    assert d["reason"] == "no_squad_export"
    assert d["changed"] == [] and d["lines"] == []
    assert "Export squad" in d["summary"]
    # the snapshot itself is still usable
    assert squad_snapshot.generate_squad_restore_lua(snap["id"])
