"""Read-only signing inspection and verified export recovery; no game writes."""
from pathlib import Path
import json
import logging
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion.bootstrap import build_services
from companion.app.commands import signed_portraits as portraits
from companion.app.commands import legacy_assets
from companion.app.commands.apply import _persist_result

root = Path(r'C:\Users\prated\Desktop\Fc26 LE\FC26_Companion')
svc = build_services(root, inline=True, logger=logging.getLogger('portrait-live'))
try:
    live = svc.transport.liveness()
    values = portraits.identities(svc, live.save_uid)
    print(json.dumps({pid: {'name': i['name'], 'source': i['source_playerid']} for pid, i in values.items()}, indent=2))
    # The previous native export job already completed; consume its exact receipt.
    reports = legacy_assets.recent_reports(svc)
    for report in reports:
        if report.get('job_id') == '01M3YBT7PP0W3T9SJ9DWAGD4T4':
            result = svc.transport.result(report['job_id'])
            if result and result.outcome.is_terminal:
                _persist_result(svc, result)
                portraits._cache_locked(svc, 190045, report)
                (root / 'live_asset_export.json').write_text(json.dumps(report, indent=2))
                print(json.dumps({'native_export_verified': report['ok'], 'assets': [
                    {k:r.get(k) for k in ('path','bytes','exported','dimensions')} for r in report['assets']]}, indent=2))
finally:
    svc.executor.shutdown() if hasattr(svc.executor, 'shutdown') else None
    svc.db.close()
