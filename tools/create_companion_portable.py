"""Create a Companion-only deployment, preserving user data during relocation.

The source is retained. SQLite backup includes committed WAL data; copied
snapshots and queue JSON receive the new root without changing player IDs,
save IDs, outcomes, deadlines or timestamps. Session/wake markers are excluded.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any


def rebase(value: Any, old_root: Path, new_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: rebase(item, old_root, new_root) for key, item in value.items()}
    if isinstance(value, list):
        return [rebase(item, old_root, new_root) for item in value]
    if isinstance(value, str):
        old = str(old_root).replace("\\", "/").rstrip("/")
        normalized = value.replace("\\", "/")
        if (normalized.casefold() == old.casefold()
                or normalized.casefold().startswith(old.casefold() + "/")):
            return str(new_root).replace("\\", "/") + normalized[len(old):]
    return value


def backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer)
            if writer.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError(f"Database verification failed: {destination.name}")


def _rebase_database(path: Path, old_root: Path, new_root: Path) -> None:
    columns = {
        "prefs": ("value_json",),
        "job_record": ("job_json", "result_json"),
        "snapshot": ("fields_json", "extra_json"),
        "favorite": ("card_json",),
        "save_snapshot": ("payload",),
    }
    with closing(sqlite3.connect(path)) as conn, conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, fields in columns.items():
            if table not in tables:
                continue
            for field in fields:
                for rowid, raw in conn.execute(f"SELECT rowid, {field} FROM {table}").fetchall():
                    if raw is None:
                        continue
                    value = json.loads(raw)
                    moved = rebase(value, old_root, new_root)
                    if moved != value:
                        conn.execute(f"UPDATE {table} SET {field}=? WHERE rowid=?",
                                     (json.dumps(moved, ensure_ascii=False), rowid))
        # A copied live squad must wait for the new worker to verify its save.
        if "save_snapshot" in tables:
            conn.execute("UPDATE save_snapshot SET stale=1")


def create(source: Path, destination: Path, *, bundle: Path | None = None) -> dict[str, Any]:
    source, destination = source.resolve(), destination.resolve()
    bundle = (bundle or source).resolve()
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Source and destination must be separate application folders")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    exe = bundle / "FC26_Companion.exe"
    runtime = bundle / "_internal_v2"
    if not exe.is_file() or not (runtime / "ingame/le_companion/init.lua").is_file():
        raise FileNotFoundError("A built FC26_Companion.exe and its complete _internal_v2 runtime are required")
    if not any((source / "card_db" / name).is_file() for name in ("catalog.sqlite", "universe.sqlite")):
        raise FileNotFoundError("The Companion card library is missing")
    # Stage beside the final folder. Never delete an existing deployment.
    stage = destination.with_name(destination.name + ".staging")
    stage = stage.resolve()
    if stage.parent != destination.parent:
        raise ValueError("Staging folder must stay in the destination directory")
    if stage.exists():
        raise FileExistsError(f"Staging folder already exists: {stage}")
    stage.mkdir(parents=True)
    shutil.copy2(exe, stage / exe.name)
    shutil.copytree(runtime, stage / runtime.name)
    for name in ("profiles.json", "planner_requests.json"):
        path = source / name
        if path.is_file():
            value = rebase(json.loads(path.read_text(encoding="utf-8-sig")), source, destination)
            (stage / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not (stage / "profiles.json").is_file():
        raise FileNotFoundError("profiles.json is required")
    for name in ("catalog.sqlite", "universe.sqlite", "teams.sqlite"):
        path = source / "card_db" / name
        if path.is_file():
            backup_database(path, stage / "card_db" / name)
    if (source / "state.sqlite").is_file():
        backup_database(source / "state.sqlite", stage / "state.sqlite")
        _rebase_database(stage / "state.sqlite", source, destination)
    copied_json = 0
    unreadable: list[str] = []
    directories = ["snapshots", "squad_plans"] + [
        "queue/" + name for name in ("jobs", "claimed", "results", "state", "poison", "archive")
    ]
    for directory in directories:
        (stage / directory).mkdir(parents=True, exist_ok=True)
        for path in (source / directory).rglob("*.json"):
            # Backup files and legacy flat Lua queues are outside this manifest.
            if any(".bak" in part or "backup" in part.casefold() for part in path.relative_to(source).parts):
                continue
            target = (stage / path.relative_to(source)).resolve()
            if not target.is_relative_to(stage):
                raise ValueError("Data file escaped the staging folder")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (ValueError, UnicodeError):
                if directory in ("queue/jobs", "queue/claimed", "squad_plans", "queue/state"):
                    raise ValueError(f"Cannot relocate malformed execution data: {path}")
                # Historic workers sometimes emitted unescaped Windows paths.
                # Apply the same narrow path repair as the result collector.
                from companion.core.transport.jobfile import read_worker_json

                value = read_worker_json(path)
                if value is None:
                    preserved = stage / "migration_unreadable" / path.relative_to(source)
                    preserved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, preserved)
                    unreadable.append(str(path.relative_to(source)))
                    continue
            value = rebase(value, source, destination)
            target.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
            copied_json += 1
    report = {"source": str(source), "destination": str(destination),
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "copied_data_json": copied_json, "session_markers_copied": False,
              "source_preserved": True, "unreadable_history_preserved": unreadable}
    (stage / "migration.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (stage / "README.txt").write_text(
        "FC 26 Companion\n\nOpen FC26_Companion.exe from this folder.\n"
        "Keep _internal_v2, card_db, state.sqlite, profiles.json and queue beside it.\n"
        "Live Editor's worker belongs in its registered data directory, which can\n"
        "differ from the folder holding Launcher.exe.\n", encoding="utf-8")
    # Both absolute paths were resolved and checked above, within the same parent.
    stage.rename(destination)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    print(json.dumps(create(args.source, args.destination, bundle=args.bundle), indent=2))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
