# Gates: FC26 Companion 2.11 UI redesign

OWNS: companion/ui/**, tools/capture_ui.py, tools/check_layout.py, tools/redesign_checks.py, tools/hidden_windows_plugin.py, assets/icons/**, docs/redesign/**, PRODUCT.md, DESIGN.md, tests/v2/**, companion/__init__.py, README.md

Scope: redesign every Companion surface on one token system, fix the audited usability defects, and prove nothing regressed.

- [x] G1: no colour literal appears outside companion/ui/theme.py
  CHECK: python tools/redesign_checks.py hex
  EXPECT: hex-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=aba3a3b1a6e9a256d05684f411201c1ed358c238a5ee408e5011a785a5677b7d; exit=0; EXPECT=matched; output-sha256=70ff9b9d4245032ca7dc02834ffff44624e0d3135f156fc7f3ad9036f06fe23a; output-bytes=8; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G2: every text, chip, OVR tier and focus pairing meets WCAG 2.2 AA
  CHECK: python tools/redesign_checks.py contrast
  EXPECT: contrast-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=f62c01df5c69976b27bdc5b83ef21a16077d3acdfb934ef207008f027418d10c; exit=0; EXPECT=matched; output-sha256=976b428f8387cece35f62e715cbf27516f2af63c8d19c8edad45b89024d75144; output-bytes=24; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G3: no entry hides its placeholder behind a textvariable
  CHECK: python tools/redesign_checks.py placeholders
  EXPECT: placeholders-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=008ead257c97750be6bb719409485a6ebee72e5c9473f09b9f1f50c158a0287c; exit=0; EXPECT=matched; output-sha256=f9be6dd42e8a8d7f8f19d46800f24a199edaa984c5e6a973f4b809e289746378; output-bytes=17; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G4: navigation and chrome draw icons from the icon pack, not Unicode glyphs
  CHECK: python tools/redesign_checks.py glyphs
  EXPECT: glyphs-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=d82132a55ff9bad094db8655088a7fdc160f3c93be67d98feed859460b9addaf; exit=0; EXPECT=matched; output-sha256=2f441fc8d32257218e02bbd74e3ff7d5fa999368f799e0aafe1a67dea47f3884; output-bytes=11; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G5: no kicker or all-caps micro label sits above a heading
  CHECK: python tools/redesign_checks.py kickers
  EXPECT: kickers-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=2a357202c82cf935c4534b63dd64edbd45d436a5d9f7222ce451b2fd5d24b775; exit=0; EXPECT=matched; output-sha256=0c668d54a5aeeb16af57049c5a00214e40f491ab3c8fef1e9d3089feb72aca85; output-bytes=12; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G6: every font size in the UI comes from the theme type scale
  CHECK: python tools/redesign_checks.py fonts
  EXPECT: fonts-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=fe440ca2e9894fb6335cb1175ff7f4884c4d4be0556731671775c10614eb7302; exit=0; EXPECT=matched; output-sha256=cd159f879cc922469476d0503a84457d8f6da72a0e3102821fa4763b88aad82d; output-bytes=51; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G7: at 1040x720, 1280x820 and 1600x960, with and without the Activity drawer, every grid column and every mapped button sits inside its container and each surface shows at most one solid primary
  CHECK: python tools/check_layout.py
  EXPECT: layout-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=d97bf256c3a6a16d575a9b993288d7759cb78cd70ee3b880923f29082f0b140f; exit=0; EXPECT=matched; output-sha256=99c08b088fae72f6ed6fa3ef71650f24778f57104f20a5224a04335dd92a5f53; output-bytes=1142; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G8: the full pytest suite has no failure outside the recorded baseline
  CHECK: python tools/redesign_checks.py tests
  EXPECT: tests-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=97f0fad6fa8b2bd097c2cf030fe3cc37537234c4a0ea13b63c866035fb8ffb0d; exit=0; EXPECT=matched; output-sha256=fb7f11e80728ca7be04abb0836a8cb33dcc6706afe5e09e271bbbca912920534; output-bytes=87; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G9: PRODUCT.md and DESIGN.md exist with their required sections
  CHECK: python tools/redesign_checks.py docs
  EXPECT: docs-ok
  CWD: ../..
  EVIDENCE: automatic-evidence=v1; definition-sha256=7625db6185566a8f9ae0fc7e5d6235fc3cc39e0d887546ec7380336a2619a037; exit=0; EXPECT=matched; output-sha256=397d5f68102738c18dccea5b926d97abcc8f25f240838f28429b315d44b49375; output-bytes=9; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=7d1499bf2eca/36 entries

- [x] G10: before and after captures exist for every surface and scenario, and were inspected
  EVIDENCE: 2026-10-14 docs/redesign/screens/before (21) and after (28 incl. deployed exe); inspected in two rounds plus a fresh-eyes reviewer (round 1 fix x10, round 2: 5 resolved, 3 partial, 2 unresolved, listed in REPORT.md section 9)

- [x] G11: the exe builds from source and the install runs the new build
  EVIDENCE: 2026-10-14 01:38 build_exe_v2.py exit 0; deployed to Desktop/Fc26 LE/FC26_Companion with backup backups/companion_before_2.11.0; relaunched 01:39, window 'FC26 Companion' responding, 0 ERROR records in logs/companion.jsonl, screenshot after/deployed_exe_first_launch.png

- [x] G12: security review of the diff finds no secret exposure, new network path, or weakened confirmation
  EVIDENCE: 2026-10-14 diff scan: no new subprocess/network/eval in companion/; API key field still masked and never written to prefs; release confirmation and mandatory Review unchanged; tools/ not bundled in the exe; capture tools open live data read-only (sqlite mode=ro + backup, junction to card_db)
