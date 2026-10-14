# Gates: FC27 card collections

OWNS: src/futgg_client.py, tools/sync_futgg_database.py, tools/build_universe.py, tests/test_futgg_sync.py, card_db/futgg/futgg_27.jsonl, card_db/futgg/fc27_collections.json, docs/fc27-card-coverage.md, .gitignore, GATES.md

Scope: Download all published FC27 FUT.GG variants including regular/pristine holographics, verify the released collections, update the installed catalogs without removing historical data, and commit the dataset and supporting changes.

- [x] G1: Sync handles capped listings and fails without replacing good data when pages or definitions are incomplete
  CHECK: python tools/sync_futgg_database.py --verify-sync
  EXPECT: SYNC CHECKS VERIFIED
  EVIDENCE: automatic-evidence=v1; definition-sha256=d61e3ca834f63debc5fdc3770844c338a47cc5d86c732073964287df851388d1; exit=0; EXPECT=matched; output-sha256=c117b19279c97d51b461bd514ddf05b5f517e0269e8658a7a0ffb99084f699dd; output-bytes=123; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=830de1fa3d3b/38 entries

- [x] G2: Download contains complete definitions and every card from the verified released squads
  CHECK: python tools/sync_futgg_database.py --verify-data
  EXPECT: DATA VERIFIED
  EVIDENCE: automatic-evidence=v1; definition-sha256=b1590e147cefd53d24ed2780278496523c37c8845c08de114004484b1bcdd65d; exit=0; EXPECT=matched; output-sha256=b8307e1c8fd02a348e41bc41a2b725a9dcdc34f2f763b2fa8dd9ba7a35a72fe1; output-bytes=912; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=830de1fa3d3b/38 entries

- [x] G3: Installed and source catalogs contain the downloaded FC27 cards and preserve historical observations
  CHECK: python tools/sync_futgg_database.py --verify-install "C:/Users/prated/Desktop/Fc26 LE/FC26_Companion"
  EXPECT: INSTALL VERIFIED
  EVIDENCE: automatic-evidence=v1; definition-sha256=82b1b245b67a2755580c0fb6ef0bc02ad034a77002fd25241726f21552cc0f40; exit=0; EXPECT=matched; output-sha256=5d9098bb33e2e9fffe90f7648648a93dab17c5df2f4521bcf351936f445a0067; output-bytes=992; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\prated\Desktop\FC26_Companion_src; path=830de1fa3d3b/38 entries

- [x] G4: The dataset, audit, downloader, installer and tests are committed together
  EVIDENCE: commit faf30f7 contains the verified dataset, audit manifest, sync/installation tooling, tests and coverage report.
