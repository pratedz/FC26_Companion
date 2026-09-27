@echo off
setlocal
cd /d "%~dp0"
echo === Building via build_exe.py (canonical) ===
python build_exe.py
if errorlevel 1 (
  echo BUILD FAILED
  exit /b 1
)
echo DONE
endlocal
