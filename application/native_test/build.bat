@echo off
setlocal enabledelayedexpansion
REM ================================================================
REM  Build script for IDS Camera USB3 Stall Test (C++)
REM  Handles: installing Build Tools + CMake if needed, then builds.
REM ================================================================

echo.
echo ====================================================================
echo   IDS Camera Stall Test - C++ Build Script
echo ====================================================================
echo.

REM ── Check for CMake ────────────────────────────────────────────
set "CMAKE_CMD="
where cmake >nul 2>&1
if %errorlevel% equ 0 (
    set "CMAKE_CMD=cmake"
    goto :cmake_found
)

REM CMake not on PATH – look in standard install locations
if exist "C:\Program Files\CMake\bin\cmake.exe" (
    set "CMAKE_CMD=C:\Program Files\CMake\bin\cmake.exe"
    goto :cmake_found
)
if exist "C:\Program Files (x86)\CMake\bin\cmake.exe" (
    set "CMAKE_CMD=C:\Program Files (x86)\CMake\bin\cmake.exe"
    goto :cmake_found
)

REM Look inside Visual Studio installations
for %%E in (Community Professional Enterprise BuildTools) do (
    if exist "C:\Program Files\Microsoft Visual Studio\2022\%%E\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" (
        set "CMAKE_CMD=C:\Program Files\Microsoft Visual Studio\2022\%%E\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    )
    if exist "C:\Program Files (x86)\Microsoft Visual Studio\2022\%%E\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" (
        set "CMAKE_CMD=C:\Program Files (x86)\Microsoft Visual Studio\2022\%%E\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    )
)

if "!CMAKE_CMD!" neq "" goto :cmake_found

REM Still not found – try winget install
echo [!] CMake not found. Installing via winget...
winget install --id Kitware.CMake --accept-source-agreements --accept-package-agreements
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install CMake. Please install manually.
    pause
    exit /b 1
)
REM After install, check if it appeared in the standard location
if exist "C:\Program Files\CMake\bin\cmake.exe" (
    set "CMAKE_CMD=C:\Program Files\CMake\bin\cmake.exe"
    goto :cmake_found
)
echo [OK] CMake installed. You may need to restart this terminal.
echo     Close and reopen your terminal, then run this script again.
pause
exit /b 0

:cmake_found
echo [OK] CMake found: !CMAKE_CMD!

REM ── Check for MSVC (cl.exe) ───────────────────────────────────
REM Try to find vcvarsall.bat for VS 2022 (Community, Professional, Enterprise, BuildTools)
set "VCVARS="
for %%E in (Community Professional Enterprise BuildTools) do (
    if exist "C:\Program Files\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvarsall.bat" (
        set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvarsall.bat"
    )
    if exist "C:\Program Files (x86)\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvarsall.bat" (
        set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvarsall.bat"
    )
)

if "!VCVARS!" == "" (
    echo.
    echo [!] Visual Studio / Build Tools 2022 not found.
    echo     Installing "Visual Studio Build Tools 2022" with C++ workload...
    echo     This may take 5-15 minutes.
    echo.
    winget install --id Microsoft.VisualStudio.2022.BuildTools ^
        --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" ^
        --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] Failed to install Build Tools. Please install manually:
        echo   https://visualstudio.microsoft.com/visual-cpp-build-tools/
        echo   Select "Desktop development with C++"
        pause
        exit /b 1
    )
    echo.
    echo [OK] Build Tools installed. Please RESTART this terminal and run again.
    pause
    exit /b 0
)

echo [OK] MSVC found: !VCVARS!

REM ── Set up MSVC environment ────────────────────────────────────
echo [..] Setting up x64 build environment...
call "!VCVARS!" x64 >nul 2>&1

REM ── Verify cl.exe is now available ─────────────────────────────
where cl >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] cl.exe not found after vcvarsall. Build Tools may be incomplete.
    pause
    exit /b 1
)
echo [OK] cl.exe available.

REM ── Build with CMake ───────────────────────────────────────────
cd /d "%~dp0"

echo.
echo [..] Configuring with CMake...
if not exist build mkdir build
cd build

"!CMAKE_CMD!" .. -G "Visual Studio 17 2022" -A x64
if %errorlevel% neq 0 (
    echo.
    echo [!] CMake configure with VS 2022 generator failed.
    echo     Trying Ninja generator...
    cd ..
    rmdir /s /q build 2>nul
    mkdir build
    cd build
    "!CMAKE_CMD!" .. -G "Ninja" -DCMAKE_BUILD_TYPE=Release
    if %errorlevel% neq 0 (
        echo [ERROR] CMake configure failed.
        pause
        exit /b 1
    )
)

echo.
echo [..] Building...
"!CMAKE_CMD!" --build . --config Release
if %errorlevel% neq 0 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo ====================================================================
echo   BUILD SUCCESSFUL
echo ====================================================================
echo.

REM ── Find the executable ────────────────────────────────────────
if exist "Release\ids_stall_test.exe" (
    echo   Executable: %cd%\Release\ids_stall_test.exe
    echo.
    echo   Run with:
    echo     cd /d "%cd%\Release"
    echo     ids_stall_test.exe 120 16
    echo.
    echo   ^(120 = seconds, 16 = buffer count^)
) else if exist "ids_stall_test.exe" (
    echo   Executable: %cd%\ids_stall_test.exe
    echo.
    echo   Run with:
    echo     cd /d "%cd%"
    echo     ids_stall_test.exe 120 16
) else (
    echo   [WARN] Could not find executable. Check build output above.
)

echo.
pause
