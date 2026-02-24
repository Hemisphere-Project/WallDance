@echo off
setlocal EnableExtensions

set "FORCE_CPU="
if /I "%~1"=="--cpu" (
    set "FORCE_CPU=1"
    shift
)

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%application" || (
    echo ERROR: Could not open application directory.
    echo Hint: run run.bat from the WallDance repository root.
    exit /b 1
)

call :detect_uv

if not defined UV_CMD (
    echo ERROR: uv is missing or not callable.
    echo Hint: run install.bat first.
    exit /b 1
)

if defined FORCE_CPU (
    echo [WallDance] CPU mode enabled ^(--cpu^).
    set "CUDA_VISIBLE_DEVICES=-1"
)

%UV_CMD% run --no-sync python src/main.py %*
exit /b %errorlevel%

:detect_uv
set "UV_CMD="
where uv >nul 2>nul
if not errorlevel 1 (
    set "UV_CMD=uv"
    exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m uv --version >nul 2>nul
    if not errorlevel 1 (
        set "UV_CMD=py -3 -m uv"
        exit /b 0
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    python -m uv --version >nul 2>nul
    if not errorlevel 1 set "UV_CMD=python -m uv"
)
exit /b 0
