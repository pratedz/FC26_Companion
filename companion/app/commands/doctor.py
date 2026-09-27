"""The health report, as data. Rendered by the CLI and the Activity drawer alike.

Replaces v1's ``src/health_check.py``. Every check returns a verdict plus a
*named next action*, because a health screen that only says "broken" is a dead
end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..presenters import liveness_view
from ..services import Services


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    action: str = ""       # what the user can do about it
    severity: str = "info"  # info | warn | error


@dataclass(frozen=True, slots=True)
class Report:
    checks: tuple[Check, ...] = ()
    bridge: dict[str, Any] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return all(c.ok for c in self.checks if c.severity == "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "bridge": self.bridge,
            "checks": [
                {
                    "name": c.name,
                    "ok": c.ok,
                    "detail": c.detail,
                    "action": c.action,
                    "severity": c.severity,
                }
                for c in self.checks
            ],
        }


def run(svc: Services) -> Report:
    checks: list[Check] = []
    p = svc.paths

    checks.append(
        Check(
            "Queue directory",
            p.queue.is_dir(),
            str(p.queue),
            "" if p.queue.is_dir() else "Restart the app to create it.",
            "error",
        )
    )

    cat = p.universe_db
    if cat.is_file():
        mb = round(cat.stat().st_size / 1e6, 1)
        checks.append(Check("Card catalog", True, f"{mb} MB at {cat}", "", "error"))
    else:
        checks.append(
            Check(
                "Card catalog",
                False,
                f"missing at {cat}",
                "Library ▸ Sources ▸ Download catalog",
                "error",
            )
        )

    checks.append(
        Check(
            "Profiles",
            p.profiles_file.is_file(),
            str(p.profiles_file),
            "" if p.profiles_file.is_file() else "Reinstall — profiles.json ships with the app.",
            "warn",
        )
    )

    # R5: a synced or network queue breaks the atomic-rename claim.
    checks.append(_check_queue_is_local(p.queue))

    try:
        from ...platform import le_install  # noqa: PLC0415

        worker = le_install.status()
        installed = bool(worker.get("installed"))
        config = worker.get("config") if isinstance(worker.get("config"), dict) else {}
        configured_queue = str(config.get("queue_dir") or "").replace("\\", "/")
        expected_queue = str(p.queue.resolve()).replace("\\", "/")
        queue_matches = installed and configured_queue.rstrip("/") == expected_queue.rstrip("/")
        detail_bits = [
            f"core={'valid' if worker.get('core_valid') else 'missing/invalid'}",
            f"autorun={'valid' if worker.get('autorun_valid') else 'missing/invalid'}",
            f"config={'valid' if worker.get('config_valid') else 'missing/invalid'}",
        ]
        checks.append(
            Check(
                "Worker installation",
                installed and queue_matches,
                ", ".join(detail_bits)
                + ("" if not installed or queue_matches else ", queue points elsewhere"),
                ""
                if installed and queue_matches
                else "Club > Install worker (this does not replace v1).",
                "error",
            )
        )
    except Exception as e:  # noqa: BLE001
        checks.append(
            Check(
                "Worker installation",
                False,
                f"could not inspect installation: {e}",
                "Club > Install worker.",
                "error",
            )
        )

    lv = svc.transport.liveness()
    worker_installed = next(
        (check.ok for check in checks if check.name == "Worker installation"),
        False,
    )
    checks.append(
        Check(
            "In-game worker",
            lv.armed,
            lv.message,
            (
                ""
                if lv.armed
                else (
                    "Restart Live Editor and FC 26 so the installed worker loads."
                    if worker_installed
                    else "Club > Install worker, then restart LE and FC 26."
                )
            ),
            "error" if not lv.armed else "info",
        )
    )

    pending = len(svc.transport.pending_ids())
    claimed = len(svc.transport.claimed_ids())
    checks.append(
        Check(
            "Queue depth",
            True,
            f"{pending} pending, {claimed} claimed",
            (
                (
                    "Use Force Drain if these are not moving."
                    if lv.armed
                    else "Restart Live Editor; pending jobs are preserved."
                )
                if pending
                else ""
            ),
            "info",
        )
    )

    try:
        from ...platform import registry  # noqa: PLC0415

        data_dir = registry.le_data_dir()
        checks.append(
            Check("LE data dir", data_dir is not None, str(data_dir), "", "warn")
        )
    except Exception as e:  # noqa: BLE001
        checks.append(
            Check("LE data dir", False, f"not resolved: {e}",
                  "Install the worker to detect it.", "warn")
        )

    return Report(checks=tuple(checks), bridge=liveness_view(lv))


def _check_queue_is_local(queue: Any) -> Check:
    """Claim-by-rename needs local NTFS. OneDrive/network paths break it (R5)."""
    text = str(queue).lower()
    suspicious = ("onedrive", "dropbox", "google drive", "\\\\")
    hit = next((s for s in suspicious if s in text), "")
    if hit:
        return Check(
            "Queue on local disk",
            False,
            f"queue path looks synced or remote ({hit})",
            "Move the app to a local folder — file locking is unreliable there.",
            "error",
        )
    return Check("Queue on local disk", True, "local path", "", "info")
