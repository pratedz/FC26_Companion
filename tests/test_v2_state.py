"""Tests for the v2 typed state model and design tokens.

These pin the two rules that v1 violated most damagingly: never write a field
you never read, and never show more than one primary action per screen.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.v2.state import (  # noqa: E402
    ActivityEntry,
    AppState,
    Change,
    ChangeSet,
    ConnectionState,
    FieldValue,
    Provenance,
    Surface,
)
from src.v2.ui import theme  # noqa: E402


# ── provenance: the silent-partial-write fix ─────────────────────────


def test_unknown_field_is_not_writable() -> None:
    f = FieldValue("acceleration")
    assert f.provenance is Provenance.UNKNOWN
    assert not f.is_writable
    assert f.display() == "—"


def test_read_field_is_shown_but_not_written() -> None:
    """Reading a value must not by itself schedule a write."""
    f = FieldValue("acceleration", 78, Provenance.READ)
    assert f.display() == "78"
    assert not f.is_writable


def test_edited_field_is_written_and_keeps_original() -> None:
    f = FieldValue("acceleration", 78, Provenance.READ)
    f.edit(95)
    assert f.is_writable
    assert f.changed
    assert f.original == 78
    assert f.value == 95


def test_editing_an_unknown_field_makes_it_writable() -> None:
    f = FieldValue("finishing")
    f.edit(90)
    assert f.is_writable
    assert f.value == 90


# ── change set ───────────────────────────────────────────────────────


def test_change_set_dedupes_by_field_keeping_first_before() -> None:
    """Repeated edits must not make the diff lie about the starting value."""
    cs = ChangeSet(target_id=1, target_label="Test")
    cs.add(Change("overallrating", 82, 88))
    cs.add(Change("overallrating", 88, 91))
    assert len(cs) == 1
    assert cs.changes[0].before == 82
    assert cs.changes[0].after == 91


def test_change_set_summary_and_diff_lines() -> None:
    cs = ChangeSet(target_id=1, target_label="L. Messi")
    assert cs.is_empty
    assert "No changes" in cs.summary()
    cs.add(Change("overallrating", 90, 99, "OVR 90 → 99"))
    assert "L. Messi" in cs.summary()
    assert cs.diff_lines() == ["OVR 90 → 99"]


# ── connection honesty ───────────────────────────────────────────────


def test_connection_states_are_distinct() -> None:
    """v1 collapsed armed / running / draining into one heartbeat."""
    assert ConnectionState().state == "setup"
    assert ConnectionState(worker_installed=True).state == "waiting"
    assert ConnectionState(worker_installed=True, armed=True).state == "armed"
    assert (
        ConnectionState(worker_installed=True, armed=True, career_loaded=True).state
        == "live"
    )


def test_armed_message_names_the_next_action() -> None:
    msg = ConnectionState(worker_installed=True, armed=True).human()
    assert "Career Mode event" in msg
    assert "advance a day" in msg


def test_armed_is_not_reported_as_off() -> None:
    """A healthy armed worker must never read as OFF just because it is idle."""
    state = ConnectionState(worker_installed=True, armed=True, last_drain_age=999.0)
    assert state.state in ("armed", "live")
    assert "not installed" not in state.human()


# ── app state ────────────────────────────────────────────────────────


def test_selecting_a_new_player_resets_staged_changes() -> None:
    """Staged edits belong to the player they were made against."""
    st = AppState()
    st.select_player(1, "A")
    st.stage(Change("overallrating", 80, 90))
    assert len(st.change_set) == 1
    st.select_player(2, "B")
    assert st.change_set.is_empty
    assert st.change_set.target_id == 2


def test_listeners_fire_and_a_broken_one_does_not_break_others() -> None:
    st = AppState()
    seen = []
    st.subscribe(lambda topic: (_ for _ in ()).throw(RuntimeError("boom")))
    st.subscribe(seen.append)
    st.go(Surface.PLAYER)
    assert "surface" in seen


def test_unsubscribe_stops_delivery() -> None:
    st = AppState()
    seen = []
    off = st.subscribe(seen.append)
    st.go(Surface.PLAYER)
    off()
    st.go(Surface.LIBRARY)
    assert seen == ["surface"]


def test_activity_log_is_capped_and_newest_first() -> None:
    st = AppState()
    for i in range(250):
        st.log_activity(ActivityEntry(ts=float(i), kind="apply", detail=str(i)))
    assert len(st.activity) == 200
    assert st.activity[0].detail == "249"


def test_busy_tracking_is_per_key() -> None:
    """v1 used one global _busy flag for five unrelated features."""
    st = AppState()
    st.set_busy("search", True)
    assert st.is_busy("search")
    assert not st.is_busy("catalog_sync")
    st.set_busy("search", False)
    assert not st.is_busy()


# ── theme ────────────────────────────────────────────────────────────


def test_only_one_primary_tone_is_filled_green() -> None:
    """The one-primary-per-screen rule needs exactly one green fill tone."""
    greens = [
        name
        for name, spec in theme.BUTTON_TONES.items()
        if spec["fg"] == theme.SUCCESS
    ]
    assert greens == ["primary"]


def test_ovr_bands_cover_the_range_and_change_colour() -> None:
    assert theme.ovr_fill(50) != theme.ovr_fill(99)
    assert theme.ovr_band(91)[0] == "Elite"
    assert theme.ovr_band(65)[0] == "Bronze"
    for v in range(1, 100):
        assert theme.ovr_fill(v)


def test_ovr_handles_garbage_without_raising() -> None:
    assert theme.ovr_band(None)[0] == "Unknown"
    assert theme.ovr_band("not a number")[0] == "Unknown"


def test_position_ids_and_names_both_resolve() -> None:
    assert theme.position_name(0) == "GK"
    assert theme.position_name(25) == "ST"
    assert theme.position_name("cb") == "CB"
    assert theme.position_group(0) == "GK"
    assert theme.position_group(25) == "ATT"
    assert theme.position_group(5) == "DEF"
    fill, text = theme.position_colours(0)
    assert fill and text


def test_position_handles_out_of_range() -> None:
    assert theme.position_name(999) == "?"
    assert theme.position_group(999) == "MID"


def test_every_connection_state_has_colour_label_and_hint() -> None:
    for state in ("live", "armed", "waiting", "setup", "error"):
        assert theme.connection_colour(state)
        assert theme.connection_label(state)
        assert theme.connection_hint(state)
