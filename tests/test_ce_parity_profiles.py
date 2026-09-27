"""CE LE-safe parity — profiles/packs inventory for ported features."""

from __future__ import annotations

from pathlib import Path

from src import product
from src import profiles as profiles_mod


# Profile ids that must exist for CE user-script / career parity (LE-safe).
REQUIRED_PROFILE_IDS = {
    "full_fitness",
    "full_sharpness",
    "full_form",
    "full_morale",
    "extend_contracts",
    "extend_cpu_contracts",
    "never_retire",
    "mass_edit_age",
    "mass_edit_squadrole",
    "fix_heads",
    "export_season_stats",
    "export_transfer_history",
    "pot_99_all",
    "untuck_shirts",
    "tight_shirts",
    "randomize_shoe_models",
    "set_generic_heads",
    "set_generic_heads_alt",
    "players_list_retiring",
    "custom_headasset_to_manager",
    "modifier_zero",
    "unlock_boots",
    "export_user_squad",
    "matchday_pack",
    "squad_boost_pack",
}

REQUIRED_PACK_IDS = {
    "matchday",
    "squad_boost",
    "contracts",
    "career_cleanup",
    "mass_visual",
    "export_season",
    "max_growth",
}


def test_required_ce_parity_profiles_load():
    loaded = {p.id: p for p in profiles_mod.load_profiles()}
    missing = REQUIRED_PROFILE_IDS - set(loaded)
    assert not missing, f"missing profiles: {sorted(missing)}"


def test_required_packs_present():
    packs = {p["id"] for p in product.list_packs()}
    missing = REQUIRED_PACK_IDS - packs
    assert not missing, f"missing packs: {sorted(missing)}"


def test_stock_script_paths_exist_for_parity_profiles():
    """Every stock_script profile must resolve to a real file under LE root or app."""
    from src import paths as paths_mod

    le_root = Path(paths_mod.le_root())
    app_root = Path(paths_mod.app_root())
    for p in profiles_mod.load_profiles():
        if p.kind != "stock_script":
            continue
        if p.id not in REQUIRED_PROFILE_IDS and p.category not in (
            "mass_edit",
            "visual",
            "export",
            "contracts",
            "career_cleanup",
        ):
            continue
        rel = p.script_path or ""
        candidates = [le_root / rel, app_root / rel, Path(rel)]
        # also sibling of workspace LE install
        workspace = Path(__file__).resolve().parents[2]
        candidates.append(workspace / rel)
        ok = any(c.is_file() for c in candidates)
        assert ok, f"profile {p.id}: script not found for {rel} (tried {candidates[:3]})"


def test_no_ce_aob_patterns_in_new_modules():
    """Static: career_ops / ovr_formula must not embed CE memory/AOB patches."""
    src = Path(__file__).resolve().parents[1] / "src"
    for name in ("career_ops.py", "ovr_formula.py"):
        text = (src / name).read_text(encoding="utf-8")
        for bad in ("AOB_PATTERNS", "readPointer", "writeBytes", "FakeEAAC", "Cheat Engine"):
            assert bad not in text, f"{name} contains forbidden {bad!r}"
