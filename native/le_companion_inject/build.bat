@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set OUT_DIR=%~dp0..\..\dist_native
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

set SRC=le_companion_inject.c protocol_core.c
set OUT_DLL=%OUT_DIR%\LECompanionInject.dll
set OUT_LIB=%OUT_DIR%\LECompanionInject.lib

echo [LECompanionInject] Building inject-side DLL...
echo   Out: %OUT_DLL%

where cl >nul 2>&1
if %ERRORLEVEL%==0 (
  echo [toolchain] MSVC cl.exe
  cl /nologo /O2 /LD /DLE_COMPANION_INJECT_EXPORTS /W3 %SRC% /Fe:"%OUT_DLL%" /link /DLL /OUT:"%OUT_DLL%" /IMPLIB:"%OUT_LIB%"
  if errorlevel 1 (
    echo BUILD FAILED: MSVC
    exit /b 1
  )
  del /q *.obj 2>nul
  echo BUILD OK: %OUT_DLL%
  exit /b 0
)

where gcc >nul 2>&1
if %ERRORLEVEL%==0 (
  echo [toolchain] MinGW gcc
  gcc -shared -O2 -DLE_COMPANION_INJECT_EXPORTS -o "%OUT_DLL%" %SRC% -Wl,--out-implib,"%OUT_LIB%"
  if errorlevel 1 (
    echo BUILD FAILED: gcc
    exit /b 1
  )
  echo BUILD OK: %OUT_DLL%
  exit /b 0
)

where clang >nul 2>&1
if %ERRORLEVEL%==0 (
  echo [toolchain] clang
  clang -shared -O2 -DLE_COMPANION_INJECT_EXPORTS -o "%OUT_DLL%" %SRC%
  if errorlevel 1 (
    echo BUILD FAILED: clang
    exit /b 1
  )
  echo BUILD OK: %OUT_DLL%
  exit /b 0
)

REM Optional: zig cc (pip install ziglang)
for /f "delims=" %%Z in ('python -c "import ziglang,os; print(os.path.join(os.path.dirname(ziglang.__file__),'zig.exe'))" 2^>nul') do set ZIG=%%Z
if defined ZIG if exist "%ZIG%" (
  echo [toolchain] zig cc
  "%ZIG%" cc -shared -O2 -DLE_COMPANION_INJECT_EXPORTS -target x86_64-windows-gnu -o "%OUT_DLL%" %SRC%
  if errorlevel 1 (
    echo BUILD FAILED: zig
    exit /b 1
  )
  echo BUILD OK: %OUT_DLL%
  exit /b 0
)

echo.
echo BUILD FAILED: no C toolchain found (cl.exe / gcc / clang / zig).
echo Install Visual Studio Build Tools, MinGW, or: python -m pip install ziglang
echo Source is complete under native\le_companion_inject\ — not an empty stub.
exit /b 2
