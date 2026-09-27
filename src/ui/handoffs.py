"""Cross-tab handoffs — shared card/target context for Career Studio synergy.

Keeps Cards ↔ Add team ↔ Editor ↔ Squad selection flowing without re-searching.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import target_players


def free_agent_pool_count(squad: Optional[Dict[str, Any]] = None) -> int:
    """How many free-agent overwrite candidates the last export harvested."""
    try:
        return int(target_players.free_agent_count(squad))
    except Exception:
        sq = squad if squad is not None else target_players.load_squad()
        pool = sq.get("free_agents") or sq.get("dummy_pool") or []
        n = 0
        for p in pool:
            if isinstance(p, dict) and (p.get("playerid") or p.get("id")):
                n += 1
            elif isinstance(p, (int, str)) and str(p).isdigit() and int(p) > 0:
                n += 1
        return n


def free_agent_status_line(squad: Optional[Dict[str, Any]] = None) -> str:
    n = free_agent_pool_count(squad)
    if n <= 0:
        return "Free agents · 0 — Export squad again (needs FA pool for safe Add team)"
    return f"Free agents · {n} ready for safe Add team"


def get_cards_selected_card(app: Any) -> Optional[Dict[str, Any]]:
    """Best-effort selected SOURCE card from Cards tab (browse or edit)."""
    try:
        from .tabs import cards as cards_tab

        card = cards_tab._selected_import_card(app)
        if card:
            return dict(card)
    except Exception:
        pass
    hits = getattr(app, "_hits", None) or []
    idx = int(getattr(app, "_selected_idx", -1) or -1)
    if hits and 0 <= idx < len(hits):
        return dict(hits[idx])
    return None


def get_editor_card(app: Any) -> Optional[Dict[str, Any]]:
    try:
        app._ensure_tab("  Editor  ")
    except Exception:
        pass
    try:
        if hasattr(app, "_editor_collect_card"):
            card = app._editor_collect_card()
            if card and (str(card.get("name") or "")).strip():
                return dict(card)
    except Exception:
        pass
    return None


def card_short_label(card: Optional[Dict[str, Any]]) -> str:
    if not card:
        return "none"
    name = str(card.get("name") or "?").strip() or "?"
    ovr = card.get("overallrating")
    pos = card.get("position") or card.get("preferredposition1") or ""
    bits = [name]
    if ovr not in (None, ""):
        bits.append(f"OVR {ovr}")
    if pos not in (None, ""):
        bits.append(str(pos))
    return " · ".join(bits)


def seed_add_team_catalog(app: Any, card: Dict[str, Any]) -> None:
    """Put one card into Add team catalog list as the selected row."""
    from .tabs import add_team as add_team_tab

    c = dict(card)
    app._ensure_tab("  Add team  ")
    app._add_team_hits = [c]
    app._add_team_selected_idx = 0
    # Align year filter with card if present
    try:
        y = str(c.get("year") or c.get("game") or "").strip()
        if y and hasattr(app, "add_team_year_var"):
            # normalize e.g. 2026 → 26
            if y.isdigit() and len(y) == 4:
                y = y[-2:]
            if y in (
                "",
                "local",
                "18",
                "19",
                "20",
                "21",
                "22",
                "23",
                "24",
                "25",
                "26",
                "futbin",
            ):
                app.add_team_year_var.set(y)
    except Exception:
        pass
    try:
        q = str(c.get("name") or "").strip()
        if q and hasattr(app, "add_team_q_var"):
            app.add_team_q_var.set(q)
    except Exception:
        pass

    add_team_tab.set_source(app, add_team_tab._SOURCE_CATALOG)
    lb = getattr(app, "add_team_list", None)
    if lb is not None:
        try:
            lb.delete(0, "end")
            from .. import card_catalog

            line = card_catalog.format_card_line(c, 0)
            lb.insert("end", line)
            lb.selection_clear(0, "end")
            lb.selection_set(0)
            lb.activate(0)
        except Exception:
            try:
                lb.delete(0, "end")
                lb.insert("end", f"  {card_short_label(c)}")
                lb.selection_set(0)
            except Exception:
                pass
    try:
        app.add_team_selected_label.configure(
            text=f"Selected · {card_short_label(c)}  ·  from Cards handoff"
        )
    except Exception:
        pass
    add_team_tab.update_preview(app)
    add_team_tab.update_gate(app)
    add_team_tab.refresh_readiness(app)


def send_card_to_add_team(app: Any, card: Optional[Dict[str, Any]] = None) -> bool:
    """Handoff Cards/Editor selection → Add team preview (safe free-agent path)."""
    c = card or get_cards_selected_card(app) or get_editor_card(app)
    if not c:
        try:
            app.status.set("Pick a card first · search + select a variant")
        except Exception:
            pass
        return False
    seed_add_team_catalog(app, c)
    try:
        app._goto("  Add team  ")
    except Exception:
        pass
    try:
        app.status.set(f"Add team ready · {card_short_label(c)} · safe free-agent path")
    except Exception:
        pass
    return True


def send_card_to_editor(app: Any, card: Optional[Dict[str, Any]] = None) -> bool:
    """Load card into Editor form for AI/manual polish, then open Editor."""
    c = card or get_cards_selected_card(app)
    if not c:
        try:
            app.status.set("Pick a card first to open in Editor")
        except Exception:
            pass
        return False
    try:
        app._ensure_tab("  Editor  ")
    except Exception:
        pass
    try:
        if hasattr(app, "_editor_fill_form"):
            app._editor_fill_form(c)
        elif hasattr(app, "_load_card_into_editor_boxes"):
            app._load_card_into_editor_boxes(c)
    except Exception as e:
        try:
            app.status.set(f"Editor load failed · {e}")
        except Exception:
            pass
        return False
    try:
        app._goto("  Editor  ")
    except Exception:
        pass
    try:
        app.status.set(f"Editor loaded · {card_short_label(c)}")
    except Exception:
        pass
    return True


def lock_squad_target(app: Any, player: Dict[str, Any]) -> None:
    """Lock Target ID/name from a squad row (shared by Squad + Cards)."""
    pid = player.get("playerid") or player.get("id") or 0
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        pid_i = 0
    name = str(player.get("name") or player.get("playername") or "").strip()
    if pid_i > 0 and hasattr(app, "target_var"):
        try:
            app.target_var.set(str(pid_i))
        except Exception:
            pass
    if name and hasattr(app, "target_name_var"):
        try:
            app.target_name_var.set(name)
        except Exception:
            pass
    # Career ops synergy
    if pid_i > 0 and hasattr(app, "career_pid_var"):
        try:
            app.career_pid_var.set(str(pid_i))
        except Exception:
            pass
    squad = target_players.load_squad()
    tid = int(squad.get("teamid") or 0)
    if tid > 0 and hasattr(app, "career_team_var"):
        try:
            app.career_team_var.set(str(tid))
        except Exception:
            pass


def send_squad_player_to_cards(app: Any, player: Dict[str, Any]) -> None:
    lock_squad_target(app, player)
    name = str(player.get("name") or "").strip()
    if name and hasattr(app, "q_var"):
        try:
            app.q_var.set(name)
        except Exception:
            pass
    try:
        if hasattr(app, "import_mode_var"):
            app.import_mode_var.set("apply")
    except Exception:
        pass
    try:
        app._goto("  Cards  ")
    except Exception:
        pass
    try:
        from .tabs import cards as cards_tab

        cards_tab.update_cards_pipeline(app)
    except Exception:
        pass
    try:
        app.status.set(
            f"Target locked · {name or player.get('playerid')} — search card to overwrite"
        )
    except Exception:
        pass


def queue_workflow_pack(app: Any, pack_id: str) -> int:
    """Stack a synergy pack into boost queue (non-blocking). Returns job count."""
    from .. import boost_queue as boost_q

    mgr = boost_q.get_boost_queue()
    jobs = mgr.enqueue_pack(pack_id)
    n = len(jobs)
    try:
        app.status.set(f"Queued {n} job(s) · pack {pack_id}")
    except Exception:
        pass
    try:
        # Prefer Boost tab visibility of queue
        if hasattr(app, "_boost_refresh_queue_ui"):
            app._boost_refresh_queue_ui()
    except Exception:
        pass
    return n


def queue_signing_settle(app: Any) -> int:
    """After successful Add team — fitness/form so new signing is match-ready."""
    return queue_workflow_pack(app, "signing_settle")


def remember_last_add(app: Any, playerid: Optional[int], name: str = "") -> None:
    app._last_add_playerid = int(playerid) if playerid else None
    app._last_add_name = name or ""
    if playerid and hasattr(app, "career_pid_var"):
        try:
            app.career_pid_var.set(str(int(playerid)))
        except Exception:
            pass
