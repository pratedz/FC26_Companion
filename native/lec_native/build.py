"""Build lec_native.dll (release + test) with Zig and run the harness.

    python build.py            # build, test, copy release DLL into ingame/
    python build.py --no-copy  # build and test only

Zig 0.16 comes from `pip install --user ziglang`. Call this with plain
`python` (not -I) or set ZIG to zig.exe so the user site is visible.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
INGAME = HERE.parents[1] / "ingame" / "le_companion" / "native"
LE_DLL = Path(os.environ.get("LE_DLL", r"C:\Users\prated\Desktop\Fc26 LE\FCLiveEditor.DLL"))
COMMON = ["-target", "x86_64-windows-gnu", "-O2", "-Wall", "-Wextra", "-Werror",
          "-Wno-unused-parameter", "-fno-sanitize=all"]
LIBS = ["-luser32", "-lkernel32"]


def zig() -> list[str]:
    exe = os.environ.get("ZIG")
    if exe:
        return [exe]
    try:
        import ziglang  # noqa: F401
    except ImportError:
        sys.exit("ziglang not found: pip install --user ziglang, or set ZIG")
    return [sys.executable, "-m", "ziglang"]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=HERE)


def main(argv: list[str]) -> int:
    OUT.mkdir(exist_ok=True)
    if LE_DLL.is_file():
        run([sys.executable, "gen_le_sig.py", "--check", str(LE_DLL)])
    z = zig()
    run(z + ["cc", *COMMON, "-shared", "-o", str(OUT / "lec_native.dll"), "lec_native.c", *LIBS])
    run(z + ["cc", *COMMON, "-DLEC_TEST", "-shared", "-o", str(OUT / "lec_native_test.dll"),
             "lec_native.c", *LIBS])
    run(z + ["cc", *COMMON, "-o", str(OUT / "harness.exe"), "harness.c", *LIBS])
    run([str(OUT / "harness.exe"), str(OUT / "lec_native_test.dll")])
    if "--no-copy" not in argv:
        INGAME.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OUT / "lec_native.dll", INGAME / "lec_native.dll")
        print(f"copied release DLL -> {INGAME / 'lec_native.dll'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
