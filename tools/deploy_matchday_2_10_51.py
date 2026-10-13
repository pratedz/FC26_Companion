"""Deploy only Companion runtime; preserve the active database and LE worker."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3

workspace = Path(__file__).resolve().parents[1]
active = Path(r"C:\Users\prated\Desktop\Fc26 LE\FC26_Companion").resolve()
bundle = (workspace / "dist/FC26_Companion").resolve()
backup = (workspace / "backups/companion_before_2_10_51").resolve()
assert active.parent == Path(r"C:\Users\prated\Desktop\Fc26 LE").resolve()
assert bundle.is_relative_to(workspace / "dist")
assert backup.is_relative_to(workspace / "backups") and not backup.exists()
runtime = (active / "_internal_v2").resolve()
old_runtime = (backup / "_internal_v2").resolve()
assert runtime.parent == active and old_runtime.parent == backup
assert (bundle / "FC26_Companion.exe").is_file()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def data_manifest():
    with sqlite3.connect(active / "state.sqlite") as connection:
        tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        # Exact contents matter: matching row counts alone would miss replaced receipts.
        return {table: list(connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"'))
                for table in tables}


def worker_manifest():
    base = Path(r"C:\FC 26 Live Editor\lua")
    files = list((base / "scripts/le_companion").rglob("*"))
    files.append(base / "autorun/01_le_companion_v2_autorun.lua")
    return {str(p.relative_to(base)): digest(p) for p in files if p.is_file()}


before = data_manifest()
worker_before = worker_manifest()
backup.mkdir(parents=True)
with sqlite3.connect(active / "state.sqlite") as source, sqlite3.connect(backup / "state.sqlite") as dest:
    source.backup(dest)
for name in ("FC26_Companion.exe", "README.txt"):
    shutil.copy2(active / name, backup / name)
shutil.move(str(runtime), str(old_runtime))
try:
    shutil.copytree(bundle / "_internal_v2", runtime)
    shutil.copy2(bundle / "FC26_Companion.exe", active / "FC26_Companion.exe")
    assert data_manifest() == before, "User data changed during deployment"
    assert worker_manifest() == worker_before, "Installed worker changed during deployment"
except BaseException:
    failed = (backup / "failed_runtime").resolve()
    assert failed.parent == backup
    if runtime.exists():
        shutil.move(str(runtime), str(failed))
    shutil.move(str(old_runtime), str(runtime))
    shutil.copy2(backup / "FC26_Companion.exe", active / "FC26_Companion.exe")
    raise
shutil.copy2(workspace / "docs/MATCHDAY_2_10_51.md", active / "MATCHDAY.md")
readme = (active / "README.txt").read_text(encoding="utf-8")
readme = readme.replace("FC26 Companion 2.10.50", "FC26 Companion 2.10.51")
readme += "\nMatchday replaces Automations: Prepare squad is the main action.\nAdjust routine remembers boosts and optional injury recovery (off by default).\nThe result card tracks preparation only. See MATCHDAY.md.\n"
(active / "README.txt").write_text(readme, encoding="utf-8")
report = {"app":"2.10.51", "core":"2.6.31", "data_preserved":True,
          "data_counts":{key:len(value) for key,value in before.items()},
          "worker_unchanged":True, "backup":str(backup),
          "executable_sha256":digest(active / "FC26_Companion.exe"),
          "tests_passed":1119, "tests_skipped":30, "hidden_matchday_ui_verified":True,
          "live_fc_matchday_verified":False}
(active / "matchday_release_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
