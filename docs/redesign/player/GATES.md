# Gates: Player page redesign + AI requests in Player

OWNS: companion/ui/surfaces/player/*.py, companion/ui/surfaces/planner_controller.py, companion/ui/surfaces/planner.py, companion/domain/ai_requests.py, companion/__init__.py (app version line only), tests/v2/test_player_ai_requests.py, tools/capture_ui.py, docs/redesign/player/*

Scope: Redesign the Edit Player page (identity header, mode row, Card Library, AI) inside the existing broadcast-desk design system, and give Player's AI mode the same request presets (and saved requests) as Club's "Plan these players".

- [x] G1: Request library is shared: Club presets unchanged, Player presets derived, saved requests readable/writable from both surfaces
  CHECK: python docs/redesign/player/run_tests.py tests/v2/test_player_ai_requests.py tests/v2/test_planner_controller.py
  EXPECT: PLAYER TESTS PASSED
  CWD: ../../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=670ebadbf540456f2c648da33ae2cbb11bb0a71872a31983bcba3abef268e86e; exit=0; EXPECT=matched; output-sha256=1010fbeab8b15d710fbf9783af33ddbad92a598b336481b81ccdfd6b218a7121; output-bytes=123; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=4f0dc1bdc3d6/37 entries

- [x] G2: Existing Player, card-import and planner tests still pass (GUI tests on, windows hidden)
  CHECK: python docs/redesign/player/run_tests.py tests/v2/test_card_import.py tests/v2/test_player_flow.py tests/v2/test_player_grok_feedback.py tests/v2/test_player_layout_visible.py tests/v2/test_tier_a_polish.py tests/v2/test_squad_command.py tests/v2/test_planner_controller.py
  EXPECT: PLAYER TESTS PASSED
  CWD: ../../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=584d36d18cc4b02c10cbddd95d92a40f77f5ec770f3972b3757ab485ad9f24bd; exit=0; EXPECT=matched; output-sha256=76af8f28d9380467bc9409423d6961e0974a7829396ff4f6609894eacb709cce; output-bytes=204; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=4f0dc1bdc3d6/37 entries

- [x] G3: Design-system static checks stay green (hex, contrast, placeholders, glyphs, kickers, fonts)
  CHECK: python tools/redesign_checks.py hex contrast placeholders glyphs kickers fonts
  EXPECT: /hex-ok[\s\S]*contrast-ok[\s\S]*placeholders-ok[\s\S]*glyphs-ok[\s\S]*kickers-ok[\s\S]*fonts-ok/
  CWD: ../../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=9cbf6440cbd1bcacd0b293fb7e7732ee81873db107265c2799291d3dcc0bd608; exit=0; EXPECT=matched; output-sha256=e8e54ac2675266046949137090ab4941d4663a1fbec6ac54ae9621d2708377cf; output-bytes=123; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=4f0dc1bdc3d6/37 entries

- [x] G4: No layout finding on the Player surface other than the pre-existing drawer 'Game log' one
  CHECK: python docs/redesign/player/layout_gate.py
  EXPECT: PLAYER LAYOUT CLEAN
  CWD: ../../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=5962243e9fde249129b49330aae6519a4990d3b26b0d1ef4497838881c432428; exit=0; EXPECT=matched; output-sha256=a17115dba84801c77187b400996ecff7d21afba8ea09833dd9e19e9378e921cb; output-bytes=41; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=4f0dc1bdc3d6/37 entries

- [x] G5: Before/after screenshots at 1040x720 and 1280x820 for player_open, player_edited, player_ai reviewed; no clipping, truncation or dead zones (manual)
  EVIDENCE: 2026-10-17 22:12 capture_ui scenarios player_open, player_edited, player_ai, player_card at 1040x720 and 1280x820 (final round in %TEMP%/plshots/final) reviewed: results/preview/requests fill the panel, Stage card and Propose always visible, ratings beside the name, rules render, no clipped controls; request captions truncate to two lines by design with the full text in a tooltip.

- [x] G6: Built with build_exe_v2, install backed up, deployed, README version bumped, app relaunched (manual)
  EVIDENCE: 2026-10-17 22:14 build_exe_v2 built dist; 22:15 install exe + _internal_v2 moved to backups/companion_before_2.13.3_20261017_221514; deployed exe sha256 30f40803... equals dist; _internal_v2 identical to dist (diff -rq clean, 3172 files); bundled core equals C:/FC 26 Live Editor core (only config.json extra); README line 1 reads 2.13.3; FC26_Companion.spec restored; app relaunched 22:15:49 PID 4228 responding.
