@echo off
:: ─── OSC Replay Launcher ───────────────────────────────────────────
:: Tries Python 3 first, falls back to PowerShell if not found.
:: Usage: replay.bat [recording.osc] [extra args]
:: ────────────────────────────────────────────────────────────────────

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "PYTHON="

:: --- Try python3 first, then python, then py launcher ---
where python3 >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%v in ('python3 -c "import sys; print(sys.version_info.major)" 2^>nul') do (
        if "%%v"=="3" set "PYTHON=python3"
    )
)

if not defined PYTHON (
    where python >nul 2>&1
    if !errorlevel!==0 (
        for /f "tokens=*" %%v in ('python -c "import sys; print(sys.version_info.major)" 2^>nul') do (
            if "%%v"=="3" set "PYTHON=python"
        )
    )
)

if not defined PYTHON (
    where py >nul 2>&1
    if !errorlevel!==0 (
        for /f "tokens=*" %%v in ('py -3 -c "import sys; print(sys.version_info.major)" 2^>nul') do (
            if "%%v"=="3" set "PYTHON=py -3"
        )
    )
)

:: Default to recording.osc if no file argument provided
set "FILE=%~1"
if "%FILE%"=="" set "FILE=recording.osc"

:: --- If Python found, use it ---
if defined PYTHON (
    echo [Launcher] Using %PYTHON%
    %PYTHON% "%SCRIPT_DIR%osc_replay.py" --loop "%FILE%" %2 %3 %4 %5 %6 %7 %8 %9
    pause
    exit /b 0
)

:: --- Fallback to PowerShell ---
where powershell >nul 2>&1
if %errorlevel%==0 (
    echo [Launcher] Python not found, using PowerShell
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%replay.ps1" -File "%FILE%" %2 %3 %4 %5 %6 %7 %8 %9
    pause
    exit /b 0
)

echo.
echo   Python 3 and PowerShell both not found.
echo   Please install Python from https://www.python.org/downloads/
echo.
pause
exit /b 1
