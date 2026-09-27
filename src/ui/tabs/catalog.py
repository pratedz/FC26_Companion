"""Catalog / Futbin-FUT.GG import tab."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
except ImportError:  # pragma: no cover
    ctk = None  # type: ignore
    filedialog = None  # type: ignore
    messagebox = None  # type: ignore

from ... import card_catalog
from ... import futgg_client
from ... import paths
from ...futbin_client import import_from_html, import_from_json, probe_futbin
from ...ui_theme import BG, BORDER, FONT_MONO, LIST_BG, MUTED, RADIUS_SM, TEXT
from .. import widgets as w

def _build_catalog_tab(app: Any) -> None:
    wrap = ctk.CTkFrame(app.tab_catalog, fg_color=BG)
    wrap.pack(fill="both", expand=True, padx=4, pady=4)

    # Primary actions card
    primary = w.panel(wrap)
    primary.pack(fill="x", padx=4, pady=(4, 8))
    w.label(primary, "FUT catalog", bold=True, size=16).pack(anchor="w", padx=16, pady=(16, 4))
    w.label(
        primary,
        "Download cards from FUT.GG (specials, Icons, Heroes). Futbin is often Cloudflare-blocked.",
        muted=True,
        size=12,
    ).pack(anchor="w", padx=16, pady=(0, 8))

    # Optional last-sync from futgg folder mtime
    sync_txt = "Last sync · never"
    try:
        futgg_dir = paths.card_db_dir() / "futgg"
        if futgg_dir.is_dir():
            mtimes = [p.stat().st_mtime for p in futgg_dir.rglob("*") if p.is_file()]
            if mtimes:
                from datetime import datetime

                dt = datetime.fromtimestamp(max(mtimes))
                sync_txt = f"Last sync · {dt.strftime('%Y-%m-%d %H:%M')}"
    except Exception:
        pass
    w.label(primary, sync_txt, muted=True, size=11).pack(anchor="w", padx=16, pady=(0, 8))

    row = ctk.CTkFrame(primary, fg_color="transparent")
    row.pack(fill="x", padx=16, pady=(0, 16))
    w.btn(
        row,
        "Download FC26 catalog",
        lambda: app._sync_futgg(["26"]),
        kind="primary",
        width=210,
        icon="download",
    ).pack(side="left", padx=4)
    w.btn(
        row,
        "Years 23–26",
        lambda: app._sync_futgg(["23", "24", "25", "26", "27"]),
        kind="accent",
        width=140,
        icon="folder",
    ).pack(side="left", padx=4)
    w.btn(
        row,
        "Sync player…",
        app._sync_player_dialog,
        kind="secondary",
        width=130,
        icon="refresh",
    ).pack(side="left", padx=4)

    # Advanced ghost row
    adv = ctk.CTkFrame(wrap, fg_color="transparent")
    adv.pack(fill="x", padx=8, pady=(0, 8))
    w.label(adv, "Advanced", muted=True, size=11).pack(side="left", padx=(8, 8))
    w.btn(adv, "Probe Futbin", app._probe, kind="ghost", width=120).pack(side="left", padx=4)
    w.btn(adv, "Import HTML…", app._imp_html, kind="ghost", width=120).pack(side="left", padx=4)
    w.btn(adv, "Import JSON…", app._imp_json, kind="ghost", width=120).pack(side="left", padx=4)

    # Activity log
    log_panel = w.panel(wrap)
    log_panel.pack(fill="both", expand=True, padx=4, pady=(0, 4))
    w.label(
        log_panel,
        "Activity log — progress and results appear here after a download.",
        muted=True,
        size=11,
    ).pack(anchor="w", padx=16, pady=(12, 4))
    app.fut_out = ctk.CTkTextbox(
        log_panel,
        font=ctk.CTkFont(family=FONT_MONO, size=12),
        fg_color=LIST_BG,
        text_color=TEXT,
        corner_radius=RADIUS_SM,
        border_width=1,
        border_color=BORDER,
    )
    app.fut_out.pack(fill="both", expand=True, padx=16, pady=(0, 16))
    app.fut_out.insert(
        "1.0",
        "No downloads yet.\n\n"
        "• Click Download FC26 catalog for specials / Icons\n"
        "• Or Sync player… for one name\n"
        "• Then open Cards → year 26 → Search\n"
        "Progress lines appear here during downloads.\n",
    )



def _probe(app: Any) -> None:
    app.status.set("Probing Futbin…")

    def work() -> None:
        data = probe_futbin("26")
        app.after(0, lambda: app._show_json(data, "Futbin probe done"))

    threading.Thread(target=work, daemon=True).start()



def _sync_futgg(app: Any, years: list) -> None:
    if app._busy:
        return
    app._busy = True
    app.status.set(f"Downloading FUT catalog {years}…")
    app.fut_out.delete("1.0", "end")
    app.fut_out.insert("1.0", f"Starting FUT.GG sync for {years}…\n")

    def log(msg: str) -> None:
        app.after(0, lambda m=msg: app._append_fut(m))

    def work() -> None:
        try:
            results = futgg_client.sync_years(years, progress=log)
            try:
                card_catalog.clear_catalog_cache(drop_index=True)
            except Exception:
                pass
            app.after(0, lambda: app._show_json(results, f"Catalog ready · {years}"))
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)
            app.after(0, lambda: messagebox.showerror("Download failed", err_msg))
            app.after(0, lambda: app.status.set(f"Catalog failed · {err_msg}"))
        finally:
            app._busy = False

    threading.Thread(target=work, daemon=True).start()



def _append_fut(app: Any, msg: str) -> None:
    app.fut_out.insert("end", msg + "\n")
    app.fut_out.see("end")
    app.status.set(msg[:80])



def _sync_player_dialog(app: Any) -> None:
    dialog = ctk.CTkInputDialog(text="Player name (e.g. Neymar)", title="Sync FUT versions")
    name = dialog.get_input()
    if not name:
        return
    if app._busy:
        return
    app._busy = True
    app.status.set(f"Syncing {name}…")

    def log(msg: str) -> None:
        app.after(0, lambda m=msg: app._append_fut(m))

    def work() -> None:
        try:
            rows = futgg_client.sync_player_all_versions(name, progress=log)
            summary = [
                {
                    "year": r.get("year"),
                    "ovr": r.get("overallrating"),
                    "rev": r.get("revision"),
                    "name": r.get("name"),
                }
                for r in rows[:40]
            ]
            app.after(
                0,
                lambda: app._show_json(
                    {"count": len(rows), "sample": summary},
                    f"Synced {len(rows)} versions · {name}",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)
            app.after(0, lambda: messagebox.showerror("Sync failed", err_msg))
        finally:
            app._busy = False

    threading.Thread(target=work, daemon=True).start()



def _show_json(app: Any, data: Any, status: str) -> None:
    app.fut_out.delete("1.0", "end")
    app.fut_out.insert("1.0", json.dumps(data, indent=2)[:12000])
    app.status.set(status)



def _imp_html(app: Any) -> None:
    path = filedialog.askopenfilename(filetypes=[("HTML", "*.html;*.htm"), ("All", "*.*")])
    if not path:
        return
    try:
        html = Path(path).read_text(encoding="utf-8", errors="replace")
        card = import_from_html(html, year="26", save=True)
        app._show_json(card, f"Imported · {card.get('name')}")
        messagebox.showinfo("Imported", f"Cached {card.get('name')}. Search year=futbin on Cards.")
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("Import failed", str(e))



def _imp_json(app: Any) -> None:
    path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All", "*.*")])
    if not path:
        return
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
        cards = import_from_json(obj, year="26", save=True)
        app._show_json({"imported": len(cards)}, f"Imported {len(cards)} card(s)")
        messagebox.showinfo("Imported", f"{len(cards)} card(s) cached.")
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("Import failed", str(e))

# ── about ────────────────────────────────────────────────────────

