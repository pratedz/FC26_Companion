"""Run tools/check_layout.py and fail on any Player finding except the known drawer one."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
run = subprocess.run([sys.executable, "tools/check_layout.py"], cwd=ROOT,
                     capture_output=True, text=True)
out = run.stdout + "\n" + run.stderr
findings = [line for line in out.splitlines() if re.match(r"^\d+x\d+ ", line)]
player = [line for line in findings if " player" in line and "'Game log'" not in line]
for line in player:
    print(line)
if not any("'Game log'" in line for line in findings):
    # Positive control: the known drawer finding proves the checker really ran.
    print("check_layout did not report the known drawer finding; it may not have run")
    sys.exit(1)
if player:
    sys.exit(1)
print(f"PLAYER LAYOUT CLEAN ({len(findings)} other findings)")
