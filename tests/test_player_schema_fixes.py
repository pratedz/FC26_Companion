"""Regression tests for confirmed correctness bugs in the FC 26 player schema.

Each class below pins one bug that was verified against ground truth:

* ``lua/libs/v2/imports/other/playstyles_enum.lua`` — the authoritative
  trait1/trait2 bit layout.
* ``lua/scripts/pap_all_playstyles.lua`` — writes trait1=icontrait1=1073741823
  and trait2=icontrait2=1535, so those two constants must round-trip exactly.
* ``player_presets/base_players.csv`` — 22,348 real rows, the authority on the
  legal range of every players-table column.

The two LE data files live outside the app directory, so the cross-checks that
read them skip cleanly when the tests run against a bare checkout.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import field_map  # noqa: E402
from src import futgg_client  # noqa: E402
from src import player_schema  # noqa: E402

LE_ROOT = APP_ROOT.parent
LUA_ENUM = LE_ROOT / "lua" / "libs" / "v2" / "imports" / "other" / "playstyles_enum.lua"
BASE_CSV = LE_ROOT / "player_presets" / "base_players.csv"

# What lua/scripts/pap_all_playstyles.lua actually writes.
PAP_TRAIT1 = 1073741823
PAP_TRAIT2 = 1535


def _lua_enum_values() -> Dict[str, List[int]]:
    """Ordered ENUM_PLAYSTYLE1_*/2_* values as declared in playstyles_enum.lua."""
    out: Dict[str, List[int]] = {"1": [], "2": []}
    text = LUA_ENUM.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = re.match(r"\s*ENUM_PLAYSTYLE([12])_\w+\s*=\s*(\d+)", line)
        if m:
            out[m.group(1)].append(int(m.group(2)))
    return out


def _csv_column(name: str) -> List[int]:
    """Every non-blank int in one base_players.csv column."""
    with BASE_CSV.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        idx = header.index(name)
        vals: List[int] = []
        for row in reader:
            if idx >= len(row):
                continue
            raw = row[idx]
            if raw == "":
                continue
            try:
                vals.append(int(raw))
            except ValueError:
                continue
    return vals


def _csv_header() -> List[str]:
    with BASE_CSV.open(newline="", encoding="utf-8", errors="replace") as fh:
        return next(csv.reader(fh))


# ── Bug 2: PLAYSTYLE1 is 30 dense bits ───────────────────────────────

PS1_EXPECTED = [
    "Finesse Shot", "Chip Shot", "Power Shot", "Dead Ball", "Precision Header",
    "Acrobatic", "Low Driven Shot", "Game Changer", "Incisive Pass",
    "Pinged Pass", "Long Ball Pass", "Tiki Taka", "Whipped Pass", "Inventive",
    "Jockey", "Block", "Intercept", "Anticipate", "Slide Tackle",
    "Aerial Fortress", "Technical", "Rapid", "First Touch", "Trickster",
    "Press Proven", "Quick Step", "Relentless", "Long Throw", "Bruiser",
    "Enforcer",
]


class TestPlaystyle1:
    def test_names_and_order(self) -> None:
        assert [n for n, _, _ in player_schema.PLAYSTYLE1] == PS1_EXPECTED

    def test_dense_30_bits(self) -> None:
        assert len(player_schema.PLAYSTYLE1) == 30
        assert [b for _, b, _ in player_schema.PLAYSTYLE1] == [
            1 << i for i in range(30)
        ]
        assert all(bank == "ps1" for _, _, bank in player_schema.PLAYSTYLE1)

    def test_all_mask_is_the_pap_script_constant(self) -> None:
        # pap_all_playstyles.lua: local playstyles1 = 1073741823
        assert player_schema.PLAYSTYLE1_ALL_MASK == PAP_TRAIT1
        assert player_schema.PLAYSTYLE1_ALL_MASK == (1 << 30) - 1


# ── Bug 1: PLAYSTYLE2 must be DENSE 14 bits, not sparse ──────────────

PS2_EXPECTED = [
    ("GK Far Throw", 1),
    ("GK Footwork", 2),
    ("GK Cross Claimer", 4),
    ("GK Rush Out", 8),
    ("GK Far Reach", 16),
    ("GK Deflector", 32),
    ("CPU AI Long Shot Taker", 64),
    ("CPU AI Early Crosser", 128),
    ("Solid Player", 256),
    ("Team Player", 512),
    ("One Club Player", 1024),
    ("Injury Prone", 2048),
    ("Leadership", 4096),
    ("Super Sub", 8192),
]


class TestPlaystyle2Dense:
    def test_exact_layout(self) -> None:
        assert [(n, b) for n, b, _ in player_schema.PLAYSTYLE2] == PS2_EXPECTED

    def test_no_gaps(self) -> None:
        """The old table skipped bits 6/7, jumping 32 -> 256."""
        assert len(player_schema.PLAYSTYLE2) == 14
        assert [b for _, b, _ in player_schema.PLAYSTYLE2] == [
            1 << i for i in range(14)
        ]

    def test_bits_6_and_7_are_the_cpu_ai_traits(self) -> None:
        by_bit = {b: n for n, b, _ in player_schema.PLAYSTYLE2}
        assert by_bit[64] == "CPU AI Long Shot Taker"
        assert by_bit[128] == "CPU AI Early Crosser"
        # The sparse table put these two names on the wrong bits.
        assert by_bit[256] == "Solid Player"
        assert by_bit[1024] == "One Club Player"

    def test_all_mask(self) -> None:
        assert player_schema.PLAYSTYLE2_ALL_MASK == (1 << 14) - 1 == 16383

    @pytest.mark.skipif(not LUA_ENUM.exists(), reason="LE lua enum not present")
    def test_matches_playstyles_enum_lua(self) -> None:
        lua = _lua_enum_values()
        assert lua["1"] == [b for _, b, _ in player_schema.PLAYSTYLE1]
        assert lua["2"] == [b for _, b, _ in player_schema.PLAYSTYLE2]


class TestPapAllPlaystylesRoundTrip:
    """1073741823 / 1535 must survive mask -> names -> mask unchanged."""

    def test_trait1_round_trip(self) -> None:
        names = player_schema.masks_to_playstyle_names(PAP_TRAIT1, 0)
        assert len(names) == 30
        assert player_schema.playstyle_names_to_masks(names) == (PAP_TRAIT1, 0)

    def test_trait2_round_trip(self) -> None:
        names = player_schema.masks_to_playstyle_names(0, PAP_TRAIT2)
        t1, t2 = player_schema.playstyle_names_to_masks(names)
        assert t1 == 0
        assert t2 == PAP_TRAIT2
        # 1535 = bits 0-8 and bit 10 = ten playstyles.
        assert len(names) == 10

    def test_trait2_decodes_the_cpu_ai_bits(self) -> None:
        names = player_schema.masks_to_playstyle_names(0, PAP_TRAIT2)
        assert "CPU AI Long Shot Taker" in names
        assert "CPU AI Early Crosser" in names
        # 1535 has bit 9 (512) clear, so Team Player must NOT appear.
        assert "Team Player" not in names
        assert "One Club Player" in names

    def test_sparse_table_regression_value_is_gone(self) -> None:
        """The old sparse table dropped bits 64/128 and re-encoded 1535 as 1343."""
        names = player_schema.masks_to_playstyle_names(0, PAP_TRAIT2)
        _, t2 = player_schema.playstyle_names_to_masks(names)
        assert t2 != 1343

    def test_both_banks_together(self) -> None:
        names = player_schema.masks_to_playstyle_names(PAP_TRAIT1, PAP_TRAIT2)
        assert player_schema.playstyle_names_to_masks(names) == (
            PAP_TRAIT1,
            PAP_TRAIT2,
        )

    def test_every_mask_bit_round_trips(self) -> None:
        for _, bit, _ in player_schema.PLAYSTYLE1:
            assert player_schema.playstyle_names_to_masks(
                player_schema.masks_to_playstyle_names(bit, 0)
            ) == (bit, 0)
        for _, bit, _ in player_schema.PLAYSTYLE2:
            assert player_schema.playstyle_names_to_masks(
                player_schema.masks_to_playstyle_names(0, bit)
            ) == (0, bit)


class TestFutggPlaystyleRoundTrip:
    """FUT.GG uses sequential indices; id >= 30 maps to trait2 bit 1 << (id-30)."""

    def test_id_to_name_covers_all_44(self) -> None:
        combined = PS1_EXPECTED + [n for n, _ in PS2_EXPECTED]
        assert len(combined) == 44
        for i, expected in enumerate(combined):
            assert player_schema.futgg_playstyle_id_to_name(i) == expected
        assert player_schema.futgg_playstyle_id_to_name(44) is None

    def test_shifted_ids_now_resolve_correctly(self) -> None:
        # Under the sparse table id 36 answered "Solid Player" (off by two).
        assert player_schema.futgg_playstyle_id_to_name(36) == "CPU AI Long Shot Taker"
        assert player_schema.futgg_playstyle_id_to_name(37) == "CPU AI Early Crosser"
        assert player_schema.futgg_playstyle_id_to_name(38) == "Solid Player"
        assert player_schema.futgg_playstyle_id_to_name(43) == "Super Sub"

    def test_bank2_bit_formula(self) -> None:
        for i in range(30, 44):
            t1, t2 = futgg_client.futgg_playstyle_ids_to_masks([i])
            assert t1 == 0
            assert t2 == 1 << (i - 30)

    def test_masks_decode_back_to_the_same_name(self) -> None:
        for i in range(44):
            t1, t2 = futgg_client.futgg_playstyle_ids_to_masks([i])
            assert player_schema.masks_to_playstyle_names(t1, t2) == [
                player_schema.futgg_playstyle_id_to_name(i)
            ]

    def test_all_ids_at_once(self) -> None:
        t1, t2 = futgg_client.futgg_playstyle_ids_to_masks(list(range(44)))
        assert t1 == player_schema.PLAYSTYLE1_ALL_MASK
        assert t2 == player_schema.PLAYSTYLE2_ALL_MASK


# ── Bug 3: skillmoves 0-indexed vs weakfoot 1-indexed ────────────────


class TestSkillMovesIndexing:
    def test_verified_ranges(self) -> None:
        assert (player_schema.SKILLMOVES_MIN, player_schema.SKILLMOVES_MAX) == (0, 4)
        assert (player_schema.WEAKFOOT_MIN, player_schema.WEAKFOOT_MAX) == (1, 5)

    def test_clamp_skillmoves_tops_out_at_4(self) -> None:
        # Was: max(0, min(5, v)) -> 5 leaked through as an illegal column value.
        assert player_schema.clamp_field("skillmoves", 5) == 4
        assert player_schema.clamp_field("skillmoves", 99) == 4
        assert player_schema.clamp_field("skillmoves", 0) == 0
        assert player_schema.clamp_field("skillmoves", -3) == 0
        for v in range(5):
            assert player_schema.clamp_field("skillmoves", v) == v

    def test_clamp_weakfoot_starts_at_1(self) -> None:
        # Was: max(0, min(5, v)) -> 0 leaked through as an illegal column value.
        assert player_schema.clamp_field("weakfootabilitytypecode", 0) == 1
        assert player_schema.clamp_field("weakfootabilitytypecode", -1) == 1
        assert player_schema.clamp_field("weakfootabilitytypecode", 6) == 5
        for v in range(1, 6):
            assert player_schema.clamp_field("weakfootabilitytypecode", v) == v

    def test_the_two_fields_do_not_share_a_range(self) -> None:
        assert player_schema.clamp_field("skillmoves", 5) != player_schema.clamp_field(
            "weakfootabilitytypecode", 5
        )
        assert player_schema.clamp_field("skillmoves", 0) != player_schema.clamp_field(
            "weakfootabilitytypecode", 0
        )

    def test_empty_card_defaults_are_both_four_stars(self) -> None:
        card = player_schema.empty_player_card()
        # Was: both 4, so a "4 star" default silently produced 5-star skills.
        assert card["skillmoves"] == 3
        assert card["weakfootabilitytypecode"] == 4
        assert card["skillmoves"] != card["weakfootabilitytypecode"]
        assert player_schema.skillmoves_to_stars(card["skillmoves"]) == 4
        assert card["weakfootabilitytypecode"] == 4  # already in stars

    def test_default_is_legal_for_the_column(self) -> None:
        card = player_schema.empty_player_card()
        assert (
            player_schema.SKILLMOVES_MIN
            <= card["skillmoves"]
            <= player_schema.SKILLMOVES_MAX
        )
        assert (
            player_schema.WEAKFOOT_MIN
            <= card["weakfootabilitytypecode"]
            <= player_schema.WEAKFOOT_MAX
        )

    def test_star_helpers_round_trip(self) -> None:
        for stars in range(1, 6):
            raw = player_schema.stars_to_skillmoves(stars)
            assert raw == stars - 1
            assert player_schema.skillmoves_to_stars(raw) == stars

    def test_star_helpers_edges(self) -> None:
        assert player_schema.stars_to_skillmoves(5) == 4
        assert player_schema.stars_to_skillmoves(1) == 0
        assert player_schema.skillmoves_to_stars(0) == 1
        assert player_schema.skillmoves_to_stars(4) == 5

    def test_star_helpers_clamp_garbage(self) -> None:
        assert player_schema.stars_to_skillmoves(0) == 0
        assert player_schema.stars_to_skillmoves(9) == 4
        assert player_schema.stars_to_skillmoves("bad") == 0
        assert player_schema.stars_to_skillmoves(None) == 0
        assert player_schema.skillmoves_to_stars(99) == 5
        assert player_schema.skillmoves_to_stars(-4) == 1
        assert player_schema.skillmoves_to_stars("bad") == 1

    def test_normalize_clamps_incoming_five(self) -> None:
        card = player_schema.normalize_player_card(
            {"name": "X", "overallrating": 90, "skillmoves": 5}
        )
        assert card["skillmoves"] == 4

    def test_field_updates_never_emit_illegal_values(self) -> None:
        card = player_schema.empty_player_card()
        card["skillmoves"] = 5
        card["weakfootabilitytypecode"] = 0
        updates = dict(
            player_schema.card_to_field_updates(card, enabled_categories=["skills"])
        )
        assert updates["skillmoves"] == 4
        assert updates["weakfootabilitytypecode"] == 1

    def test_summary_renders_stars_not_raw(self) -> None:
        card = player_schema.empty_player_card()
        # raw 3 must display as 4 stars
        assert "4★SM" in player_schema.format_card_summary(card)
        card["skillmoves"] = 0
        # raw 0 is legal (1★) and must not render as "?"
        assert "1★SM" in player_schema.format_card_summary(card)

    def test_field_map_clamps_to_db_range(self) -> None:
        got = dict(field_map.extract_attr_updates({"skill_moves": 5, "weak_foot": 5}))
        assert got["skillmoves"] == 4
        assert got["weakfootabilitytypecode"] == 5
        got = dict(field_map.extract_attr_updates({"skill_moves": 0, "weak_foot": 0}))
        assert got["skillmoves"] == 0
        assert got["weakfootabilitytypecode"] == 1

    @pytest.mark.skipif(not BASE_CSV.exists(), reason="base_players.csv not present")
    def test_ranges_match_real_data(self) -> None:
        sm = _csv_column("skillmoves")
        wf = _csv_column("weakfootabilitytypecode")
        assert (min(sm), max(sm)) == (
            player_schema.SKILLMOVES_MIN,
            player_schema.SKILLMOVES_MAX,
        )
        assert (min(wf), max(wf)) == (
            player_schema.WEAKFOOT_MIN,
            player_schema.WEAKFOOT_MAX,
        )


# ── Bug 4: internationalrep is [1..5] ────────────────────────────────


class TestInternationalRep:
    def test_clamp_floor_is_one(self) -> None:
        # Was: max(0, min(5, v)) -> 0 is not a legal reputation.
        assert player_schema.clamp_field("internationalrep", 0) == 1
        assert player_schema.clamp_field("internationalrep", -7) == 1

    def test_clamp_ceiling_is_five(self) -> None:
        assert player_schema.clamp_field("internationalrep", 6) == 5
        assert player_schema.clamp_field("internationalrep", 99) == 5

    def test_legal_values_pass_through(self) -> None:
        for v in range(1, 6):
            assert player_schema.clamp_field("internationalrep", v) == v

    @pytest.mark.skipif(not BASE_CSV.exists(), reason="base_players.csv not present")
    def test_range_matches_real_data(self) -> None:
        vals = _csv_column("internationalrep")
        assert (min(vals), max(vals)) == (
            player_schema.INTERNATIONALREP_MIN,
            player_schema.INTERNATIONALREP_MAX,
        )


# ── Bug 5: the shohan composite ──────────────────────────────────────

SIX_COMPOSITES = ["pacdiv", "shohan", "paskic", "driref", "defspe", "phypos"]


class TestShohanComposite:
    def test_both_tables_list_all_six(self) -> None:
        assert [f for f, _ in player_schema.COMPOSITE_FIELDS] == SIX_COMPOSITES
        assert [f for f, _ in player_schema.COMPOSITE_DB_FIELDS] == SIX_COMPOSITES

    def test_phantom_sho_column_is_gone(self) -> None:
        assert "sho" not in [f for f, _ in player_schema.COMPOSITE_FIELDS]
        assert "sho" not in player_schema.ALL_EDIT_FIELDS

    def test_shohan_is_an_editable_composite_field(self) -> None:
        assert "shohan" in player_schema.ALL_EDIT_FIELDS
        assert player_schema.FIELD_TO_CATEGORY["shohan"] == "composites"

    def test_composites_category_exposes_six_fields(self) -> None:
        cat = player_schema.CATEGORY_BY_ID["composites"]
        assert [f for f, _, _ in cat["fields"]] == SIX_COMPOSITES

    def test_sho_round_trips_through_a_card(self) -> None:
        card = player_schema.normalize_player_card(
            {"name": "Composite Test", "shohan": 88, "pacdiv": 91}
        )
        assert card["shohan"] == 88
        updates = dict(
            player_schema.card_to_field_updates(card, enabled_categories=["composites"])
        )
        assert updates["shohan"] == 88
        assert updates["pacdiv"] == 91

    def test_composites_are_opt_in(self) -> None:
        assert "composites" not in player_schema.DEFAULT_CATEGORY_IDS

    @pytest.mark.skipif(not BASE_CSV.exists(), reason="base_players.csv not present")
    def test_all_six_are_real_db_columns(self) -> None:
        header = _csv_header()
        for col in SIX_COMPOSITES:
            assert col in header, f"{col} missing from base_players.csv"
        assert "sho" not in header


# ── Bug 6: bodytypecode clamp width ──────────────────────────────────


class TestBodyTypeCode:
    def test_real_high_codes_survive(self) -> None:
        # Was: no dedicated branch, and add_player still clamps 1..20, which
        # rewrote 437 (a real physique) down to 20.
        assert player_schema.clamp_field("bodytypecode", 437) == 437
        assert player_schema.clamp_field("bodytypecode", 200) == 200
        assert player_schema.clamp_field("bodytypecode", 21) == 21

    def test_bounds(self) -> None:
        assert (
            player_schema.BODYTYPECODE_MIN,
            player_schema.BODYTYPECODE_MAX,
        ) == (1, 437)
        assert player_schema.clamp_field("bodytypecode", 0) == 1
        assert player_schema.clamp_field("bodytypecode", -5) == 1
        assert player_schema.clamp_field("bodytypecode", 9999) == 437

    def test_common_codes_pass_through(self) -> None:
        for v in range(1, 10):
            assert player_schema.clamp_field("bodytypecode", v) == v

    @pytest.mark.skipif(not BASE_CSV.exists(), reason="base_players.csv not present")
    def test_clamp_covers_every_observed_value(self) -> None:
        vals = _csv_column("bodytypecode")
        assert min(vals) >= player_schema.BODYTYPECODE_MIN
        assert max(vals) == player_schema.BODYTYPECODE_MAX == 437
        # No real value may be altered by the clamp.
        for v in set(vals):
            assert player_schema.clamp_field("bodytypecode", v) == v

    @pytest.mark.skipif(not BASE_CSV.exists(), reason="base_players.csv not present")
    def test_old_narrow_clamp_would_have_mangled_data(self) -> None:
        vals = set(_csv_column("bodytypecode"))
        mangled: List[Tuple[int, int]] = [(v, 20) for v in vals if v > 20]
        assert mangled, "expected real codes above the old 1..20 ceiling"
