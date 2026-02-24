@echo off
setlocal EnableExtensions

set "POWER_LIMIT=%~1"
if "%POWER_LIMIT%"=="" set "POWER_LIMIT=280"

echo === GPU Power Limiter ===
echo.

net session >nul 2>nul
if not %errorlevel%==0 (
    echo ERROR: This script requires Administrator privileges.
    echo Hint: Right-click Command Prompt and choose "Run as administrator".
    exit /b 1
)

where nvidia-smi >nul 2>nul
if not %errorlevel%==0 (
    echo ERROR: nvidia-smi was not found.
    echo Hint: install/update NVIDIA drivers and ensure nvidia-smi is in PATH.
    exit /b 1
)

echo Current GPU Power Settings:
nvidia-smi -q -d POWER | findstr /R /C:"Power Draw" /C:"Power Limit"
echo.

set "CURRENT_LIMIT="
set "MAX_LIMIT="
set "MIN_LIMIT="

for /f "usebackq tokens=1 delims=." %%A in (`nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits ^| more`) do (
    if not defined CURRENT_LIMIT set "CURRENT_LIMIT=%%A"
)
for /f "usebackq tokens=1 delims=." %%A in (`nvidia-smi --query-gpu=power.max_limit --format=csv,noheader,nounits ^| more`) do (
    if not defined MAX_LIMIT set "MAX_LIMIT=%%A"
)
for /f "usebackq tokens=1 delims=." %%A in (`nvidia-smi --query-gpu=power.min_limit --format=csv,noheader,nounits ^| more`) do (
    if not defined MIN_LIMIT set "MIN_LIMIT=%%A"
)

if "%CURRENT_LIMIT%"=="" (
    echo ERROR: Could not read current power limit.
    exit /b 1
)
if "%MAX_LIMIT%"=="" (
    echo ERROR: Could not read max power limit.
    exit /b 1
)
if "%MIN_LIMIT%"=="" (
    echo ERROR: Could not read min power limit.
    exit /b 1
)

echo Current limit: %CURRENT_LIMIT%W
echo Valid range: %MIN_LIMIT%W - %MAX_LIMIT%W
echo Requested: %POWER_LIMIT%W
echo.

set /a TEST_LIMIT=%POWER_LIMIT% >nul 2>nul
if errorlevel 1 (
    echo ERROR: Power limit must be an integer value in watts.
    exit /b 1
)

if %POWER_LIMIT% LSS %MIN_LIMIT% (
    echo ERROR: Power limit must be between %MIN_LIMIT%W and %MAX_LIMIT%W
    exit /b 1
)
if %POWER_LIMIT% GTR %MAX_LIMIT% (
    echo ERROR: Power limit must be between %MIN_LIMIT%W and %MAX_LIMIT%W
    exit /b 1
)

echo Applying power limit...
nvidia-smi -pl %POWER_LIMIT%
if not %errorlevel%==0 (
    echo.
    echo X Failed to set power limit
    exit /b 1
)

echo.
echo Power limit set to %POWER_LIMIT%W
echo.
echo New GPU Power Settings:
nvidia-smi --query-gpu=power.limit,power.draw --format=csv
echo.
echo Note: This setting resets on reboot.
exit /b 0
