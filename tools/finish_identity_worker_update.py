"""Finish the prepared worker update only after both hosts have exited."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion.platform import le_install

active = Path(r'C:\Users\prated\Desktop\Fc26 LE\FC26_Companion')
report = le_install.install(queue_dir=active/'queue', resources=active/'_internal_v2',
    expected_dll_sha256='0af955fcf7a34ec4055a11e18d3c621b4fa877cd6013776cfaa78a279b9308d9')
if not report['ok']:
    print(json.dumps(report, indent=2))
    raise SystemExit(1)
release_path = active/'identity_injury_release_report.json'
release = json.loads(release_path.read_text())
release['worker_update_installed'] = True
release_path.write_text(json.dumps(release, indent=2))
(active/'installation_report_2_10_50.json').write_text(json.dumps(report, indent=2))
print(json.dumps({'worker_installed':True,'core':report['core_version'],'queue_dir':report['queue_dir']}, indent=2))
