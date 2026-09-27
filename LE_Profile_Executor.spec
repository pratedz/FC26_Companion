# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('profiles.json', '.'), ('bridge', 'bridge'), ('web', 'web')]
binaries = []
hiddenimports = ['customtkinter', 'src.web_api', 'src.web_server', 'src.health_check', 'src.editor_presets', 'src.profiles', 'src.target_players', 'src.player_schema', 'src.player_apply', 'src.import_player', 'src.grok_client', 'src.ui_theme', 'src.ui.widgets', 'src.ui.player_edit', 'src.ui.apply_chrome', 'src.ui.apply_flow', 'src.ui.chrome', 'src.ui.handoffs', 'src.ui.badge', 'src.ui.skeleton', 'src.ui.stepper', 'src.ui.empty_state', 'src.ui.collapsible', 'src.ui.tabs.home', 'src.ui.tabs.cards', 'src.ui.tabs.add_team', 'src.ui.tabs.boost', 'src.ui.tabs.squad', 'src.ui.tabs.editor', 'src.ui.tabs.catalog', 'src.ui.tabs.about', 'src.icons', 'src.card_index', 'src.card_types', 'src.card_catalog', 'src.card_to_lua', 'src.card_enrich', 'src.card_compare', 'src.favorites', 'src.product', 'src.boost_queue', 'src.add_player', 'src.card_match', 'src.snapshot_store', 'src.protocol', 'src.companion_config', 'src.inject_side', 'src.apply_service', 'src.le_apply', 'src.undo_apply', 'src.job_history', 'src.futbin_client', 'src.futbin_models', 'src.futbin_parse', 'src.futbin_http', 'src.futgg_client', 'PIL', 'PIL._tkinter_finder', 'src', 'src.actions', 'src.add_team_lua', 'src.base_players', 'src.career_ops', 'src.fc26_data', 'src.field_map', 'src.futbin_cli', 'src.generated', 'src.generated.fc26_tables', 'src.growth_xp', 'src.gui', 'src.lua_resolve', 'src.ovr_formula', 'src.paths', 'src.snippets', 'src.squad_export', 'src.squad_snapshot', 'src.teams_directory', 'src.ui', 'src.ui.tabs', 'src.ui_perf', 'src.universe', 'src.v2', 'src.v2.outcomes', 'src.v2.protocol', 'src.v2.services', 'src.v2.state', 'src.v2.ui', 'src.v2.ui.components', 'src.v2.ui.theme']
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:\\Users\\prated\\Desktop\\FC 26 LE v26.3.5\\LE_Profile_Executor\\app_entry.py'],
    pathex=['C:\\Users\\prated\\Desktop\\FC 26 LE v26.3.5\\LE_Profile_Executor'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LE_Profile_Executor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LE_Profile_Executor',
)
