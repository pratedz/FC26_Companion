"""Scrub poison queue + verify SAFE v4 Lua shape."""
from __future__ import annotations

from src import add_player, le_apply, paths


def main() -> None:
    q = paths.queue_dir()
    for p in list(q.glob("*.lua")) + [q / "_run_now.lua"]:
        if not p.is_file():
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        if "player_data_numeric" in t or "collect_runtime" in t:
            p.unlink(missing_ok=True)
            print("scrub", p.name)
    le_apply.clear_stale_jobs()
    le_apply.rebuild_pending()
    lua = add_player.generate_add_to_team_lua(
        {"name": "Zidane", "overallrating": 94, "skillmoves": 5},
        teamid=243,
    )
    assert "CreatePlayer, pid, pdata" in lua.replace(" ", "") or "CreatePlayer,pid,pdata" in lua.replace(
        " ", ""
    )
    assert '["skillmoves"] = "4"' in lua
    assert "create_enter" in lua
    assert "player_data_numeric" not in lua
    print("v4 OK")
    print("crash log path in lua:", "_add_team_crash.log" in lua)


if __name__ == "__main__":
    main()
