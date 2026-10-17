"""Run pytest the way the Companion's UI tests need: tools/ on the path, GUI on, windows hidden."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
env = dict(os.environ, PYTHONPATH=str(ROOT / "tools"), COMPANION_ALLOW_GUI_TESTS="1")
code = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "hidden_windows_plugin", *sys.argv[1:]],
                       cwd=ROOT, env=env)
# The suite rewrites this bridge file; put it back so the tree stays clean.
subprocess.call(["git", "checkout", "--", "bridge/RUN_BRIDGE_ONCE.lua"], cwd=ROOT,
                stderr=subprocess.DEVNULL)
if code == 0:
    print("PLAYER TESTS PASSED")
sys.exit(code)
