"""Activity's game asset tools. Manual exports, clear evidence, DDS previews."""
from __future__ import annotations

from typing import Any

import customtkinter as ctk

from ...app.commands import legacy_assets as commands
from ...domain.legacy_assets import DATABASE_ASSET, portrait_paths
from .. import theme
from ..widgets.primitives import button, muted_label, panel, text_label
from ._common import set_status


def _wrapped(parent: Any, text: str, *, size: int = 11, muted: bool = True, **kwargs: Any) -> Any:
    factory = muted_label if muted else text_label
    # Activity's drawer is 360 logical pixels wide. A fixed wrap below its
    # inner width avoids recursive Configure/redraw cycles in CTk's labels.
    return factory(parent, text, size=size, wraplength=220, justify="left", anchor="w", **kwargs)


def build(host: Any, svc: Any) -> Any:
    root = ctk.CTkScrollableFrame(host, fg_color="transparent")
    root.pack(fill="both", expand=True)
    text_label(root, "Game assets", size=theme.T_HEADING, bold=True).pack(anchor="w", pady=(theme.SP2, theme.SP1))
    _wrapped(root, "Check and export files from the running game. Exports stay inside Companion.").pack(fill="x", pady=(0, theme.SP2))
    readiness = _wrapped(root, "")
    readiness.pack(fill="x", pady=(0, theme.SP2))
    actions = []
    busy = {"job_id": ""}
    result_host = ctk.CTkFrame(root, fg_color="transparent")

    def submit(paths: Any, *, export: bool = False, required: bool = False) -> None:
        try:
            values = paths() if callable(paths) else paths
            job_id = commands.request(svc, values, export=export, required=required)
            busy["job_id"] = job_id
            set_status(svc, "Asset request queued. Exports run on the next safe Career event; follow progress below.")
            refresh()
        except (ValueError, OSError, RuntimeError) as exc:
            set_status(svc, f"Asset request unavailable: {exc}")

    def action(parent: Any, label: str, paths: Any, *, export: bool = False, required: bool = False) -> None:
        widget = button(parent, label, lambda: submit(paths, export=export, required=required),
                        kind="secondary", height=theme.BTN_SM)
        widget.pack(fill="x", pady=theme.SP1)
        actions.append((widget, export))

    database = panel(root, level=1)
    database.pack(fill="x", pady=(0, theme.SP2))
    text_label(database, "Game database", size=13, bold=True).pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, 0))
    _wrapped(database, "Export the raw game database with its file size and SHA-256 fingerprint.\n"
                "This is not a Career save backup and does not replace your card catalog.",
                ).pack(fill="x", padx=theme.SP3)
    db_row = ctk.CTkFrame(database, fg_color="transparent")
    db_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    action(db_row, "Check database", [DATABASE_ASSET])
    action(db_row, "Export database", [DATABASE_ASSET], export=True, required=True)

    portraits = panel(root, level=1)
    portraits.pack(fill="x", pady=(0, theme.SP2))
    text_label(portraits, "Player portrait", size=13, bold=True).pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, 0))
    _wrapped(portraits, "Enter the game's player ID. Checks normal and youth portraits; exports DDS and previews PNG.\n"
                "A reused free-agent ID may still have that free agent's picture. The ID identifies the asset.",
                ).pack(fill="x", padx=theme.SP3)
    portrait_row = ctk.CTkFrame(portraits, fg_color="transparent")
    portrait_row.pack(fill="x", padx=theme.SP3, pady=(theme.SP1, theme.SP2))
    pid = ctk.CTkEntry(portrait_row, placeholder_text="Player ID", width=115)
    pid.pack(fill="x", pady=(0, theme.SP1))

    def portrait_request() -> tuple[str, str]:
        try:
            return portrait_paths(int(pid.get().strip()))
        except ValueError as exc:
            raise ValueError("Enter a player ID between 1 and 999999.") from exc

    action(portrait_row, "Check portrait", portrait_request)
    action(portrait_row, "Export & preview", portrait_request, export=True)

    custom = panel(root, level=1)
    custom.pack(fill="x", pady=(0, theme.SP2))
    text_label(custom, "Other game asset", size=13, bold=True).pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    custom_row = ctk.CTkFrame(custom, fg_color="transparent")
    custom_row.pack(fill="x", padx=theme.SP3, pady=(0, theme.SP2))
    path = ctk.CTkEntry(custom_row, placeholder_text="data/... game file path", width=320)
    path.pack(fill="x", pady=(0, theme.SP1))
    action(custom_row, "Check", lambda: [path.get().strip()])
    action(custom_row, "Export", lambda: [path.get().strip()], export=True, required=True)
    result_host.pack(fill="both", expand=True)
    last_signature = [None]

    def refresh() -> None:
        if not root.winfo_exists():
            return
        reports = commands.recent_reports(svc)
        current = next((r for r in reports if r["job_id"] == busy["job_id"]), None)
        if current and current.get("outcome") not in {"queued", "running", "deferred", "inspecting"}:
            busy["job_id"] = ""
        check_reason = commands.availability(svc)
        export_reason = commands.availability(svc, export=True)
        readiness.configure(text=check_reason or "Asset API connected. Open Career Mode and keep Live Editor running.")
        for widget, export in actions:
            reason = (export_reason if export else check_reason) or busy["job_id"]
            widget.configure(state="disabled" if reason else "normal")
        signature = repr(reports)
        if signature != last_signature[0]:
            last_signature[0] = signature
            for child in result_host.winfo_children():
                child.destroy()
            text_label(result_host, "Recent asset results", size=13, bold=True).pack(anchor="w", pady=theme.SP1)
            if not reports:
                muted_label(result_host, "Run a check or export to see its result here.", size=11).pack(anchor="w")
            for report in reports:
                _render_report(result_host, svc, report)

    def tick() -> None:
        if root.winfo_exists():
            refresh()
            root.after(1500, tick)

    tick()
    return root


def _render_report(parent: Any, svc: Any, report: dict[str, Any]) -> None:
    card = panel(parent, level=1)
    card.pack(fill="x", pady=(0, theme.SP2))
    outcome = str(report.get("outcome") or "waiting")
    if report.get("ok") is False:
        outcome = "Needs attention"
    text_label(card, f"{str(report.get('action') or 'Asset').title()} · {outcome}",
               size=12, bold=True).pack(anchor="w", padx=theme.SP3, pady=(theme.SP2, theme.SP1))
    _wrapped(card, f"{report['job_id']} · LE {report.get('le_version') or '—'}", size=10).pack(fill="x", padx=theme.SP3)
    if report.get("error"):
        _wrapped(card, report["error"]).pack(fill="x", padx=theme.SP3)
    for row in report.get("assets") or ():
        state = "Found" if row.get("exists") is True else "Missing" if row.get("exists") is False else "Not checked"
        if row.get("exported"):
            state = f"Exported · {row['bytes']:,} bytes"
        _wrapped(card, f"{state} · {row['path']}", muted=False).pack(fill="x", padx=theme.SP3, pady=theme.SP1)
        if row.get("error"):
            _wrapped(card, row["error"]).pack(fill="x", padx=theme.SP3)
        if row.get("exported"):
            _wrapped(card, "SHA-256: " + row["sha256"], size=10).pack(fill="x", padx=theme.SP3)
            buttons = ctk.CTkFrame(card, fg_color="transparent")
            buttons.pack(fill="x", padx=theme.SP3)
            button(buttons, "Open export folder", lambda r=row, j=report["job_id"]: _open(svc, j, r["output"]),
                   kind="ghost", height=theme.BTN_SM).pack(side="left")
        if row.get("preview") and row.get("exported"):
            try:
                from PIL import Image
                preview = commands.report_file(svc, report["job_id"], row["preview"])
                with Image.open(preview) as image:
                    if image.width * image.height > 180 * 180:
                        raise ValueError("Invalid cached preview dimensions")
                    photo = ctk.CTkImage(light_image=image.copy(), dark_image=image.copy(), size=(96, 96))
                label = ctk.CTkLabel(card, text="", image=photo)
                label.pack(anchor="w", padx=theme.SP3, pady=theme.SP2)
            except (OSError, ValueError):
                _wrapped(card, "Preview file unavailable. Export again to regenerate it.").pack(fill="x", padx=theme.SP3)
    _wrapped(card, report.get("finished_at") or "Waiting for the next safe Career event.", size=10).pack(
        fill="x", padx=theme.SP3, pady=(theme.SP1, theme.SP2))


def _open(svc: Any, job_id: str, value: str) -> None:
    try:
        import os
        os.startfile(commands.report_file(svc, job_id, value).parent)
    except (OSError, ValueError, AttributeError) as exc:
        set_status(svc, f"Could not open export: {exc}")
