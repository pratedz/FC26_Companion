"""Tests for the widget kit's *pure* half: diff engine, table model, OVR bands.

These are the tests that matter, because these three models decide what the user
is told. Every assertion below names the v1 defect it pins shut:

  - the diff engine is what makes P4 real (v1 wrote ~40 untouched defaults);
  - ``before is None`` renders an em dash and is called out (P3/H2);
  - the table model owns selection identity (v1's five parallel result lists);
  - the OVR delta colours each side by its own band (§6.3).

No Tk is imported here. Real-widget construction is covered in
``test_ui_shell.py``, which skips cleanly with no display.
"""

from __future__ import annotations

import pytest

from companion.ui import theme
from companion.ui.widgets import diff as D
from companion.ui.widgets.ovr import OvrDelta, ovr_delta, ovr_text
from companion.ui.widgets.primitives import (
    EM_DASH,
    debounce,
    display_value,
    elevation,
    provenance_color,
    shade,
    truncate,
)
from companion.ui.widgets.states import DeadEndError, ErrorCopy, require_action
from companion.ui.widgets.table import (
    Column,
    TableModel,
    configure_body_height,
    shown_pool_for_height,
    ROW_HEIGHT,
)

# ---------------------------------------------------------------------------
# primitives — pure helpers
# ---------------------------------------------------------------------------


def test_themed_scroll_paints_a_token_canvas_instead_of_tk_black():
    import inspect

    from companion.ui.widgets import primitives

    source = inspect.getsource(primitives.themed_scroll)
    assert "CTkCanvas" in source
    assert "highlightbackground" in source
    assert "highlightcolor" in source
    assert "theme.CARD" in source
    assert "CTkScrollableFrame" not in source
    assert "tk.Canvas" not in source
    assert "pack_propagate(False)" in source
    assert "bind_pointer_wheel" in source
    assert "register_wheel_region" in source
    assert "sync_scroll" in source
    assert "<Button-4>" in primitives.WHEEL_SEQUENCES
    assert "winfo_width" in source
    assert "after_idle" in source
    assert "fit_inner" in source
    assert "yview_scroll" in source
    rungs = [elevation(i) for i in range(4)]
    assert rungs == [theme.BG, theme.PANEL, theme.CARD, theme.CARD_HOVER]
    assert len(set(rungs)) == 4          # a nested panel is never invisible
    assert elevation(99) == theme.CARD_HOVER
    assert elevation(-3) == theme.BG


def test_pointer_wheel_targets_include_the_text_on_a_cell():
    """OVR/name text is a tk label on top of the CTk canvas, and that is the hit target."""
    from companion.ui.widgets.primitives import pointer_wheel_targets

    canvas = object()
    text = object()
    label = type("Label", (), {"_canvas": canvas, "_label": text})()
    assert pointer_wheel_targets(label) == [canvas, text]
    frame = type("Frame", (), {"_canvas": canvas})()
    assert pointer_wheel_targets(frame) == [canvas]
    plain = object()
    assert pointer_wheel_targets(plain) == [plain]


def test_wheel_zone_scrolls_the_row_and_leaves_the_scrollbar():
    from companion.ui.widgets.primitives import wheel_zone

    host = object()
    bar = object()
    row = type("Row", (), {"master": host})()
    cell = type("Cell", (), {"master": row})()
    body_ids = {id(host)}
    bar_ids = {id(bar)}
    assert wheel_zone(cell, body_ids=body_ids, bar_ids=bar_ids) == ("body", 2)
    assert wheel_zone(bar, body_ids=body_ids, bar_ids=bar_ids) == ("bar", 0)
    knob = type("Knob", (), {"master": bar})()
    assert wheel_zone(knob, body_ids=body_ids, bar_ids=bar_ids)[0] == "bar"
    outside = type("Out", (), {"master": None})()
    assert wheel_zone(outside, body_ids=body_ids, bar_ids=bar_ids) is None


def test_wheel_dispatch_scrolls_only_the_list_under_the_pointer(monkeypatch):
    from companion.ui.widgets import primitives as primitives

    outer = object()
    host = type("Host", (), {"master": outer})()
    row = type("Row", (), {"master": host})()
    cell = type("Cell", (), {"master": row})()
    calls: list[str] = []
    inner = primitives._WheelRegion(
        {id(host)}, set(), lambda _event: calls.append("inner") or "break"
    )
    outer_region = primitives._WheelRegion(
        {id(outer)}, set(), lambda _event: calls.append("outer") or "break"
    )
    bar = object()
    knob = type("Knob", (), {"master": bar})()
    barred = primitives._WheelRegion(
        {id(host)}, {id(bar)}, lambda _event: calls.append("bar") or "break"
    )
    monkeypatch.setattr(primitives, "_WHEEL_REGIONS", [outer_region, inner])
    event = type("Event", (), {"widget": cell, "x_root": 0, "y_root": 0})()
    assert primitives._dispatch_wheel(event) == "break"
    assert calls == ["inner"]

    calls.clear()
    monkeypatch.setattr(primitives, "_WHEEL_REGIONS", [barred])
    bar_event = type("Event", (), {"widget": knob, "x_root": 0, "y_root": 0})()
    assert primitives._dispatch_wheel(bar_event) is None
    assert calls == []


def test_chrome_tokens_are_distinct_from_panels():
    assert theme.CHROME != theme.PANEL
    assert theme.GRID_HEADER != theme.PANEL
    assert theme.CHROME_LINE != theme.BORDER


def test_monogram_uses_initials():
    from companion.ui.surfaces._common import monogram

    assert monogram("Manchester City") == "MC"
    assert monogram("A B C D") == "ABC"
    assert monogram("") == "FC"
    assert monogram("", fallback="P") == "P"


def test_muted_label_allows_explicit_colour_override(monkeypatch):
    from companion.ui.widgets import primitives

    captured = {}

    def fake_text_label(parent, text, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(primitives, "text_label", fake_text_label)
    primitives.muted_label(object(), "working", color=theme.ACCENT)
    assert captured["color"] == theme.ACCENT


def test_shade_derives_colours_without_new_hex_literals():
    assert shade(theme.ACCENT, 1.0) == theme.ACCENT.lower()
    darker = shade(theme.ACCENT, 0.25)
    assert darker.startswith("#") and darker != theme.ACCENT
    assert shade(theme.SUCCESS, 99) == "#ffffff"   # clamps, never overflows
    assert shade("not-a-colour", 1.2) == "not-a-colour"


def test_provenance_defaults_to_unknown():
    """An unlabelled value must not render as trustworthy — that IS the P3 bug."""
    assert provenance_color("read") == theme.PROV_READ
    assert provenance_color("edited") == theme.PROV_EDITED
    assert provenance_color("unknown") == theme.PROV_UNKNOWN
    assert provenance_color("") == theme.PROV_UNKNOWN
    assert provenance_color("garbage") == theme.PROV_UNKNOWN


def test_display_value_never_launders_none():
    assert display_value(None) == EM_DASH
    assert display_value(0) == "0"        # 0 is a real value, not "unknown"
    assert display_value("") == ""
    assert display_value(True) == "on"


def test_truncate():
    assert truncate("Aarón Anselmino", 8) == "Aarón A…"
    assert truncate("Palmer", 20) == "Palmer"


def test_debounce_cancels_prior_after_and_reschedules():
    calls: list[str] = []
    cancelled: list[object] = []
    scheduled: list[object] = []

    class FakeWidget:
        def after(self, _delay_ms, fn):
            handle = object()
            scheduled.append((handle, fn))
            return handle

        def after_cancel(self, handle):
            cancelled.append(handle)

    widget = FakeWidget()
    wrapped = debounce(widget, 120, lambda: calls.append("run"))
    wrapped()
    first = scheduled[-1][0]
    wrapped()
    assert cancelled == [first]
    assert len(scheduled) == 2
    scheduled[-1][1]()
    assert calls == ["run"]


# ---------------------------------------------------------------------------
# OVR — the delta keeps two independent bands
# ---------------------------------------------------------------------------


def test_ovr_text_unknown_is_a_dash_but_zero_is_zero():
    assert ovr_text(None) == EM_DASH
    assert ovr_text(0) == "0"
    assert ovr_text("91") == "91"
    assert ovr_text("nope") == EM_DASH


def test_delta_colours_each_side_by_its_own_band():
    """§6.3: an upgrade must read as a colour jump, not two identical chips."""
    d = ovr_delta(86, 91)
    assert theme.ovr_colors(d.before) != theme.ovr_colors(d.after)
    assert d.band_before == "Gold high" and d.band_after == "Elite"
    assert d.band_changed is True


def test_delta_within_a_band_is_not_a_colour_jump():
    d = ovr_delta(75, 79)
    assert d.changed is True
    assert d.band_changed is False       # both "Gold low"
    assert theme.ovr_colors(75) == theme.ovr_colors(79)


def test_delta_direction_and_signed_form():
    assert ovr_delta(86, 91).direction == "up"
    assert ovr_delta(91, 86).direction == "down"
    assert ovr_delta(86, 86).direction == "same"
    assert ovr_delta(86, 91).signed == "+5"
    assert ovr_delta(91, 86).signed == "-5"
    assert ovr_delta(86, 86).signed == ""


def test_delta_with_unread_before_refuses_to_guess():
    """P3: we never read it, so we cannot claim an improvement."""
    d = ovr_delta(None, 91)
    assert d.known is False
    assert d.direction == "unknown"
    assert d.amount is None
    assert d.band_changed is False
    assert d.text == f"{EM_DASH} → 91"


def test_ovr_delta_is_frozen_value_object():
    with pytest.raises(Exception):
        OvrDelta(1, 2).before = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The diff engine — §5.2 copy rules
# ---------------------------------------------------------------------------


def _palmer() -> D.DiffModel:
    return D.build_diff(
        {
            "overallrating": (86, 91),
            "potential": (89, 94),
            "playstyles": (["Rapid"], ["Rapid", "Finesse Shot", "Trickster"]),
            "preferredposition1": ("ST", "CF"),
        },
        subject="Cole Palmer",
        subject_id=257534,
        undo_available=True,
    )


def test_diff_speaks_plain_english_not_a_dict_dump():
    model = _palmer()
    text = {line.field: line.text for line in model.lines}
    assert text["overallrating"] == "86 → 91"
    assert text["preferredposition1"] == "ST → CF"
    assert text["playstyles"].startswith("2 added — Finesse Shot, Trickster")


def test_ovr_delta_is_hoisted_as_the_headline():
    model = _palmer()
    assert model.headline is not None
    assert model.headline.field == "overallrating"
    assert model.headline_delta is not None and model.headline_delta.band_changed


def test_summary_leads_with_ovr_and_caps_overflow():
    """The Changes bar line must fit a 1040 px window (A7)."""
    summary = _palmer().summary(limit=2)
    assert summary.startswith("Cole Palmer · Overall 86 → 91")
    assert summary.endswith("more")
    assert "+2 PlayStyles" in _palmer().summary(limit=9)


def test_apply_button_carries_the_count_not_ok():
    model = _palmer()
    assert model.apply_label == "Apply 4"
    assert model.title == "Review 4 changes before applying"
    assert D.build_diff({"potential": (89, 90)}).apply_label == "Apply 1"


def test_reassurance_lines_say_what_did_not_change():
    """§5.2: silence about name/age/nationality/appearance caused the v1 distrust."""
    model = _palmer()
    untouched = {line.field for line in model.lines if line.kind == "untouched"}
    assert untouched == set(D.DEFAULT_REASSURE)
    # ...and they are NOT counted as changes.
    assert model.count == 4


def test_a_field_present_in_the_change_set_is_not_also_reassured():
    model = D.build_diff({"age": (22, 23)})
    ages = [line for line in model.lines if line.field == "age"]
    assert len(ages) == 1 and ages[0].kind == "numeric"


def test_unknown_before_is_an_em_dash_and_is_called_out():
    """P3 + H6, the highest-severity honesty rule in the product."""
    model = D.build_diff({"stamina": (None, 88)})
    line = next(ln for ln in model.lines if ln.field == "stamina")
    assert line.before_known is False
    assert line.text == f"{EM_DASH} → 88"
    assert line.note == "not read from the game"
    assert model.unread == ("Stamina",)
    assert "never read from the game" in model.unread_note
    assert "Stamina" in model.unread_note


def test_known_before_produces_no_unread_callout():
    assert D.build_diff({"stamina": (80, 88)}).unread_note == ""


def test_zero_before_is_a_real_value_not_unknown():
    line = next(ln for ln in D.build_diff({"stamina": (0, 88)}).lines
                if ln.field == "stamina")
    assert line.before_known is True
    assert line.text == "0 → 88"


def test_counts_rather_than_enumerating_past_six():
    before: list[str] = []
    after = [f"Style{i}" for i in range(9)]
    line = next(ln for ln in D.build_diff({"playstyles": (before, after)}).lines
                if ln.field == "playstyles")
    assert line.text.startswith("9 added — ")
    assert line.text.endswith("+3 more")
    assert line.short == "+9 PlayStyles"


def test_collection_removals_are_reported_too():
    line = next(
        ln for ln in D.build_diff({"playstyles": (["a", "b"], ["a"])}).lines
        if ln.field == "playstyles"
    )
    assert "1 removed — b" in line.text
    assert line.short == "-1 PlayStyles"


def test_no_change_reads_as_unchanged_and_blocks_apply():
    """P3's acceptance test: load from target, apply nothing."""
    model = D.build_diff({"overallrating": (86, 86)})
    assert model.count == 0
    assert model.has_changes is False
    assert model.title == "No changes staged."
    assert model.summary() == "No changes staged."
    assert model.headline is None


def test_undo_availability_is_stated_before_committing():
    assert "undo snapshot will be saved" in D.build_diff({}, undo_available=True).undo_note
    assert D.build_diff({}, undo_available=False).undo_note == (
        "This change cannot be undone automatically."
    )
    assert D.build_diff({}).undo_note == ""     # unknown -> say nothing, not "yes"


def test_blast_radius_survives_onto_the_model():
    model = D.build_diff({}, blast_radius="45 players in Chelsea. Nothing else is touched.")
    assert "45 players" in model.blast_radius


def test_pairs_from_marks_unseen_base_fields_as_unread():
    pairs = D.pairs_from({"overallrating": 86}, {"overallrating": 91, "stamina": 88})
    assert pairs["overallrating"] == (86, 91)
    assert pairs["stamina"] == (None, 88)       # never read -> honest None


def test_custom_labels_and_groups_are_respected():
    model = D.build_diff(
        {"finishing": (89, 92)},
        labels={"finishing": "Finishing"},
        groups={"finishing": "Shooting"},
        reassure=(),
    )
    assert model.groups[0].name == "Shooting"
    assert model.groups[0].lines[0].label == "Finishing"
    assert model.groups[0].change_count == 1


def test_unknown_field_names_are_humanised_not_dumped():
    assert D.humanise("skill_moves") == "Skill moves"
    assert D.humanise("skillMoves") == "Skill moves"
    assert D.humanise("position1") == "Position 1"
    line = D.build_diff({"weight_kg": (78, 80)}, reassure=()).lines[0]
    assert line.label == "Weight kg"


def test_summarise_shortcut_matches_the_model():
    changes = {"overallrating": (86, 91)}
    assert D.summarise(changes) == D.build_diff(changes).summary()


# ---------------------------------------------------------------------------
# States — H5: a terminal state must name its exit
# ---------------------------------------------------------------------------


def test_require_action_rejects_a_dead_end():
    with pytest.raises(DeadEndError):
        require_action("", None)
    with pytest.raises(DeadEndError):
        require_action("Retry", None)
    with pytest.raises(DeadEndError):
        require_action("   ", lambda: None)
    assert require_action(" Read my squad ", print)[0] == "Read my squad"


def test_error_copy_keeps_the_raw_cause_separate():
    copy = ErrorCopy(message="Couldn't read your squad.", cause="lua:63: nil value")
    assert copy.has_cause
    assert not ErrorCopy(message="x").has_cause


# ---------------------------------------------------------------------------
# TableModel — sorting, filtering, selection identity
# ---------------------------------------------------------------------------

COLUMNS = (
    Column("name", "NAME", width=200, stretch=True),
    Column("pos", "POS", width=50),
    Column("ovr", "OVR", width=46, numeric=True),
    Column("age", "AGE", width=46, numeric=True),
)

SQUAD = (
    {"playerid": 257534, "name": "Cole Palmer", "pos": "CAM", "ovr": 86, "age": 22},
    {"playerid": 247635, "name": "Enzo Fernández", "pos": "CM", "ovr": 84, "age": 24},
    {"playerid": 76687, "name": "Didier Drogba", "pos": "ST", "ovr": 89, "age": 34},
    {"playerid": 278455, "name": "Aarón Anselmino", "pos": "CB", "ovr": None, "age": 19},
)


def model(**kw) -> TableModel:
    return TableModel(COLUMNS, SQUAD, key_field="playerid", **kw)


def test_shown_pool_for_height_fills_a_windowed_panel():
    assert shown_pool_for_height(0) == 6
    assert shown_pool_for_height(10) == 6
    assert shown_pool_for_height(ROW_HEIGHT * 10) == 10
    assert shown_pool_for_height(ROW_HEIGHT * 40) == 24


def test_configure_body_height_accepts_the_inner_canvas():
    """CTk delivers the resize on the frame canvas, which used to be ignored."""
    canvas = object()

    class Host:
        _canvas = canvas

        def winfo_height(self) -> int:
            return 1

    host = Host()
    assert configure_body_height(
        canvas, host, event_height=ROW_HEIGHT * 12, host_height=1
    ) == ROW_HEIGHT * 12
    assert configure_body_height(
        canvas, host, event_height=ROW_HEIGHT * 40, host_height=ROW_HEIGHT * 12
    ) == ROW_HEIGHT * 12
    assert configure_body_height(
        host, host, event_height=0, host_height=ROW_HEIGHT * 14
    ) == ROW_HEIGHT * 14
    assert configure_body_height(object(), host, event_height=400, host_height=400) is None


def test_cell_text_renders_unknowns_as_an_em_dash():
    m = model()
    assert COLUMNS[2].text(SQUAD[3]) == EM_DASH
    assert COLUMNS[2].text(SQUAD[0]) == "86"


def test_numeric_sort_puts_unknowns_last_in_both_directions():
    """An unread OVR is not a zero — it must never top a 'best first' sort."""
    m = model()
    m.sort_by("ovr")                       # numeric columns start descending
    assert m.sort_desc is True
    assert [r["ovr"] for r in m.visible_rows()] == [89, 86, 84, None]
    m.sort_by("ovr")                       # toggle
    assert [r["ovr"] for r in m.visible_rows()] == [84, 86, 89, None]


def test_text_sort_starts_ascending_and_is_case_insensitive():
    m = model()
    m.sort_by("name")
    assert m.sort_desc is False
    assert m.visible_rows()[0]["name"] == "Aarón Anselmino"


def test_sort_indicator_is_a_glyph_not_only_a_colour():
    m = model()
    m.sort_by("ovr")
    assert m.header_caption("ovr") == "OVR ▼"
    m.sort_by("ovr")
    assert m.header_caption("ovr") == "OVR ▲"
    assert m.header_caption("name") == "NAME"


def test_unsortable_column_ignores_clicks():
    m = TableModel((Column("name", "NAME", sortable=False),), SQUAD)
    m.sort_by("name")
    assert m.sort_key == ""


def test_filter_matches_any_rendered_column():
    m = model()
    m.set_filter("palmer")
    assert m.visible_count == 1
    m.set_filter("CB")
    assert m.visible_count == 1
    m.set_filter("")
    assert m.visible_count == 4


def test_facet_filter_composes_with_the_text_box():
    m = model()
    m.set_filter_fn(lambda r: (r.get("age") or 0) < 25)
    assert m.visible_count == 3
    m.set_filter("enzo")
    assert m.visible_count == 1


def test_selection_is_keyed_by_row_identity_not_position():
    """v1's parallel result lists re-pointed selections at the wrong player."""
    m = model()
    m.click(0)
    assert m.selected_rows()[0]["playerid"] == 257534
    m.sort_by("ovr")                       # order changes underneath
    assert m.selected_rows()[0]["playerid"] == 257534
    m.set_filter("palmer")
    assert m.selected_count == 1


def test_shift_click_selects_a_range_from_the_anchor():
    m = model()
    m.click(0)
    m.click(2, shift=True)
    assert m.selected_count == 3


def _roster(n: int = 10) -> TableModel:
    rows = [{"playerid": i, "name": f"P{i:02d}", "pos": "CM", "ovr": 70 + (i % 5), "age": 20 + i} for i in range(1, n + 1)]
    return TableModel(COLUMNS, rows, key_field="playerid")


def _ids(m: TableModel) -> list[int]:
    return [row["playerid"] for row in m.selected_rows()]


def test_shift_click_selects_forward_and_reverse_ranges():
    m = _roster()
    m.click(0)
    m.click(9, shift=True)
    assert _ids(m) == list(range(1, 11))
    m.click(2)
    m.click(0, shift=True)
    assert _ids(m) == [1, 2, 3]


def test_shift_tick_uses_the_same_visible_range():
    m = _roster()
    m.toggle(1)
    m.toggle(4, shift=True)
    assert _ids(m) == [2, 3, 4, 5]


def test_shift_range_ignores_filtered_out_rows():
    m = _roster()
    m.click(0)
    m.set_filter_fn(lambda row: row["playerid"] % 2 == 1)
    m.click(0)
    m.click(m.visible_count - 1, shift=True)
    assert _ids(m) == [1, 3, 5, 7, 9]
    assert all(pid % 2 == 1 for pid in _ids(m))


def test_shift_range_follows_the_sorted_order():
    m = _roster()
    m.sort_by("name")
    m.sort_by("name")  # descending
    visible = [row["playerid"] for row in m.visible_rows()]
    m.click(0)
    m.click(3, shift=True)
    assert _ids(m) == visible[:4]


def test_normal_click_moves_the_shift_anchor():
    m = _roster()
    m.click(0)
    m.click(5, shift=True)
    assert _ids(m) == [1, 2, 3, 4, 5, 6]
    m.click(7)
    m.click(9, shift=True)
    assert _ids(m) == [8, 9, 10]


def test_ctrl_click_toggles_without_clearing():
    m = model()
    m.click(0)
    m.click(2, ctrl=True)
    assert m.selected_count == 2
    m.click(2, ctrl=True)
    assert m.selected_count == 1


def test_plain_click_replaces_the_selection():
    m = model()
    m.click(0)
    m.click(2, ctrl=True)
    m.click(1)
    assert m.selected_count == 1


def test_select_all_means_all_VISIBLE_only():
    """Bulk-editing rows a filter is hiding is how a mass edit surprises someone."""
    m = model()
    m.set_filter("a")
    visible = m.visible_count
    assert 0 < visible < 4
    m.select_all_visible()
    assert m.selected_count == visible


def test_single_select_model_never_accumulates():
    m = model(multi_select=False)
    m.click(0)
    m.click(2, ctrl=True)
    assert m.selected_count == 1


def test_set_rows_keeps_live_selections_and_drops_dead_ones():
    m = model()
    m.click(0)
    m.click(2, ctrl=True)
    m.set_rows(SQUAD[:1])                  # Drogba is gone from the save
    assert m.selected_count == 1
    assert m.selected_rows()[0]["playerid"] == 257534


def test_cursor_clamps_and_never_wraps():
    m = model()
    assert m.move_cursor(-5) == 0
    assert m.move_cursor(99) == 3
    assert m.move_cursor(1) == 3
    assert m.cursor_row()["playerid"] == 278455


def test_cursor_on_an_empty_model_is_negative_not_an_index_error():
    m = TableModel(COLUMNS, ())
    assert m.move_cursor(1) == -1
    assert m.cursor_row() is None
    assert m.row_at(0) is None


def test_toggle_and_clear():
    m = model()
    m.toggle(0)
    m.toggle(1)
    assert m.selected_count == 2
    m.toggle(1)
    assert m.selected_count == 1
    m.clear_selection()
    assert m.selected_count == 0


def test_format_callback_receives_the_whole_row():
    col = Column("contract", "CONTR", format=lambda r: f"{r['name'][:1]}·2029")
    assert col.text(SQUAD[0]) == "C·2029"


def test_format_failure_degrades_to_a_dash_not_a_crash():
    col = Column("x", "X", format=lambda r: r["missing"])
    assert col.text(SQUAD[0]) == EM_DASH
