# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('ingame', 'ingame'), ('profiles.json', '.'), ('docs/schema', 'docs/schema'), ('C:/Users/prated/AppData/Local/Programs/Python/Python313/tcl/tcl8.6', '_tcl_data'), ('C:/Users/prated/AppData/Local/Programs/Python/Python313/tcl/tk8.6', '_tk_data'), ('C:/Users/prated/AppData/Local/Programs/Python/Python313/Lib/tkinter', 'tkinter'), ('assets', 'assets'), ('web', 'web')]
binaries = [('C:/Users/prated/AppData/Local/Programs/Python/Python313/DLLs/tcl86t.dll', '.'), ('C:/Users/prated/AppData/Local/Programs/Python/Python313/DLLs/tk86t.dll', '.'), ('C:/Users/prated/AppData/Local/Programs/Python/Python313/DLLs/_tkinter.pyd', '.')]
hiddenimports = ['customtkinter', 'darkdetect', 'tkinter', 'tkinter.constants', 'tkinter.filedialog', 'tkinter.font', 'tkinter.messagebox', 'tkinter.simpledialog', 'tkinter.ttk', 'PIL', 'PIL._tkinter_finder', 'openai', 'companion', 'companion.app', 'companion.app.commands', 'companion.app.commands.apply', 'companion.app.commands.builds', 'companion.app.commands.card_import', 'companion.app.commands.doctor', 'companion.app.commands.injury', 'companion.app.commands.liveness_hold', 'companion.app.commands.migrate', 'companion.app.commands.player', 'companion.app.commands.queue', 'companion.app.commands.release', 'companion.app.commands.squad', 'companion.app.commands.squad_plan', 'companion.app.commands.squad_session', 'companion.app.commands.squad_sync_memory', 'companion.app.commands.sync_trace', 'companion.app.commands.team', 'companion.app.events', 'companion.app.presenters', 'companion.app.reducers', 'companion.app.services', 'companion.app.state', 'companion.app.store', 'companion.app.ui_actions', 'companion.bootstrap', 'companion.cli', 'companion.core', 'companion.core.clock', 'companion.core.db', 'companion.core.executor', 'companion.core.log', 'companion.core.offline_core', 'companion.core.paths', 'companion.core.ports', 'companion.core.transport', 'companion.core.transport.fake', 'companion.core.transport.jobfile', 'companion.core.transport.v3', 'companion.domain', 'companion.domain.add_player', 'companion.domain.automation_guide', 'companion.domain.builds', 'companion.domain.card_import', 'companion.domain.catalog', 'companion.domain.ids', 'companion.domain.job', 'companion.domain.outcome', 'companion.domain.pack_library', 'companion.domain.player', 'companion.domain.playstyles', 'companion.domain.profile_library', 'companion.domain.safe_dummy_candidates', 'companion.domain.squad_plan', 'companion.domain.squad_session', 'companion.domain.teams', 'companion.integrations', 'companion.integrations.ai_provider', 'companion.integrations.grok', 'companion.platform', 'companion.platform.base_players', 'companion.platform.fswatch', 'companion.platform.le_install', 'companion.platform.procs', 'companion.platform.registry', 'companion.platform.secrets', 'companion.ui', 'companion.ui.icons', 'companion.ui.nav', 'companion.ui.palette', 'companion.ui.placement', 'companion.ui.shell', 'companion.ui.surfaces', 'companion.ui.surfaces._common', 'companion.ui.surfaces.activity', 'companion.ui.surfaces.add_player', 'companion.ui.surfaces.automations', 'companion.ui.surfaces.club', 'companion.ui.surfaces.injury', 'companion.ui.surfaces.library', 'companion.ui.surfaces.planner', 'companion.ui.surfaces.player', 'companion.ui.surfaces.player.card_picker', 'companion.ui.surfaces.player.manual', 'companion.ui.surfaces.player.presets', 'companion.ui.surfaces.player.target_picker', 'companion.ui.surfaces.player.workstation', 'companion.ui.surfaces.sign', 'companion.ui.surfaces.sign.assistant', 'companion.ui.surfaces.sign.basket', 'companion.ui.surfaces.sign.details', 'companion.ui.surfaces.sign.discovery', 'companion.ui.surfaces.sign.readiness', 'companion.ui.surfaces.sign.result_row', 'companion.ui.surfaces.sign.results', 'companion.ui.surfaces.sign.review', 'companion.ui.surfaces.sign.search', 'companion.ui.theme', 'companion.ui.wallpaper', 'companion.ui.widgets', 'companion.ui.widgets.ai_bar', 'companion.ui.widgets.diff', 'companion.ui.widgets.ovr', 'companion.ui.widgets.primitives', 'companion.ui.widgets.states', 'companion.ui.widgets.table', 'companion.ui.widgets.toast']
hiddenimports += collect_submodules('openai')
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('darkdetect')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:/Users/prated/Desktop/FC 26 LE v26.3.5/LE_Profile_Executor/app_entry_v2.py'],
    pathex=['C:/Users/prated/Desktop/FC 26 LE v26.3.5/LE_Profile_Executor'],
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
    name='FC26_Companion',
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
    contents_directory='_internal_v2',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FC26_Companion',
)
