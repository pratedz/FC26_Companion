"""Deploy the tested runtime, preserving all active Companion data and LE files."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion.platform import le_install

workspace = Path(__file__).resolve().parents[1]
active = Path(r'C:\Users\prated\Desktop\Fc26 LE\FC26_Companion').resolve()
bundle = (workspace / 'dist' / 'FC26_Companion').resolve()
backup = (workspace / 'backups' / 'companion_before_2_10_50').resolve()
assert backup.is_relative_to(workspace / 'backups')
assert active.parent == Path(r'C:\Users\prated\Desktop\Fc26 LE').resolve()
assert bundle.is_relative_to(workspace / 'dist')
assert (bundle / '_internal_v2/ingame/le_companion/ops/fitness_injuries.lua').is_file()
resume = '--resume' in sys.argv
assert not backup.exists() or not any(backup.iterdir()) or resume, 'Never replace a prior release backup'

def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()

def state_manifest(path):
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {table: connection.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] for table in tables}
        ids = [row[0] for row in connection.execute('SELECT job_id FROM job_record ORDER BY job_id')]
        prefs = [list(row) for row in connection.execute('SELECT key,value_json FROM prefs ORDER BY key')]
    return {'counts': counts, 'job_ids': ids, 'preferences': prefs}

backup.mkdir(parents=True, exist_ok=True)
before = state_manifest(active / 'state.sqlite')
if resume:
    assert json.loads((backup / 'data_before.json').read_text()) == before, 'Active data changed since backup'
else:
    with sqlite3.connect(active / 'state.sqlite') as source, sqlite3.connect(backup / 'state.sqlite') as dest:
        source.backup(dest)
    (backup / 'data_before.json').write_text(json.dumps(before, indent=2))
    shutil.copy2(active / 'FC26_Companion.exe', backup / 'FC26_Companion.exe')
    for name in ('README.txt', 'ASSET_TOOLS.md', 'asset_release_report.json'):
        if (active / name).is_file():
            shutil.copy2(active / name, backup / name)
worker = Path(r'C:\FC 26 Live Editor\lua\scripts\le_companion')
autorun = Path(r'C:\FC 26 Live Editor\lua\autorun\01_le_companion_v2_autorun.lua')
if not resume:
    shutil.copytree(worker, backup / 'worker')
    shutil.copy2(autorun, backup / autorun.name)
pins = json.loads((workspace / 'backups/worker_before_relocate_2_10_48/official_le_hashes.json').read_text())
assert all(digest(active.parent / name) == expected for name, expected in pins.items()), 'Official LE file changed before deployment'
runtime = (active / '_internal_v2').resolve()
assert runtime.parent == active
old_runtime = (backup / '_internal_v2').resolve()
assert old_runtime.parent == backup
shutil.move(str(runtime), str(old_runtime))
try:
    shutil.copytree(bundle / '_internal_v2', runtime)
    shutil.copy2(bundle / 'FC26_Companion.exe', active / 'FC26_Companion.exe')
    report = le_install.install(queue_dir=active / 'queue', resources=bundle / '_internal_v2',
        expected_dll_sha256='0af955fcf7a34ec4055a11e18d3c621b4fa877cd6013776cfaa78a279b9308d9')
    if not report['ok']:
        if not any('Close FC 26 and Live Editor' in error for error in report.get('errors') or ()):
            raise RuntimeError(str(report.get('errors')))
except BaseException:
    failed = (backup / 'failed_runtime').resolve()
    assert failed.parent == backup
    if runtime.exists():
        shutil.move(str(runtime), str(failed))
    shutil.move(str(old_runtime), str(runtime))
    shutil.copy2(backup / 'FC26_Companion.exe', active / 'FC26_Companion.exe')
    # Restore only Companion's worker files if installation was interrupted.
    shutil.copytree(backup / 'worker', worker, dirs_exist_ok=True)
    shutil.copy2(backup / autorun.name, autorun)
    raise

after = state_manifest(active / 'state.sqlite')
assert before == after, 'Active user data changed during runtime deployment'
assert all(digest(active.parent / name) == expected for name, expected in pins.items()), 'Official LE file changed during deployment'
shutil.copy2(workspace / 'docs/SIGNED_PORTRAITS_INJURIES_2_10_50.md', active / 'SIGNED_PORTRAITS.md')
(active / 'installation_report_2_10_50.json').write_text(json.dumps(report, indent=2))
release = {'app': '2.10.50', 'core': '2.6.31', 'worker_update_installed': report['ok'], 'backup': str(backup), 'data_preserved': True,
    'data_counts': after['counts'], 'executable_sha256': digest(active / 'FC26_Companion.exe'),
    'native_asset_export_verified': True, 'new_injury_profile_native_evidence_verified': True,
    'new_injury_profile_live_scan_verified': False, 'resident_worker_restart_required': True,
    'portrait_installation_tested_with_isolated_mods': True, 'portrait_overrides_installed_automatically': False,
    'official_le_files_verified_unchanged': len(pins), 'full_suite_passed': 1097,
    'full_suite_skipped': 30, 'live_ai_retry_passed': True, 'followup_tests_passed': 54,
    'full_suite_initial_live_ai_failure': 'Mismatched player/base revision; passed on focused retry',
    'final_targeted_tests_passed': 100,
    'hidden_club_portrait_ui_check_passed': True, 'packaged_dds_check_passed': True}
(active / 'identity_injury_release_report.json').write_text(json.dumps(release, indent=2))
(active / 'README.txt').write_text('''FC26 Companion 2.10.50 - Live Editor v26.3.6

Save your Career and restart FC26 with Live Editor to load core 2.6.31.
Then open FC26_Companion.exe in this folder and refresh your squad.

Injuries: Scan squad, then heal one player or the listed injured players.
The previous failure came from a worker restricted to LE v26.3.5.
The v26.3.6 layout is verified and retains all memory validation guards.

Signed identities: Club and Player show the requested name and portrait on
the actual Career ID. Available portraits load after a safe Career event.
If necessary, select the player in Club and click Load signed portrait.

Optional: Install portrait in game writes a backed-up LE portrait override
for the actual Career ID. Restore previous portrait reverses it. Overrides
affect this ID across saves while LE runs. They change menu images, not 3D
faces. Reopen squad menus; restart FC with LE if an image remains cached.
No portrait overrides are installed automatically.

Activity > Assets provides raw game file export/check tools. The game database
export is not a Career save backup. Native DB/portrait exports were verified.
SIGNED_PORTRAITS.md contains release details. Your existing data is preserved.
''', encoding='utf-8')
print(json.dumps(release, indent=2))
