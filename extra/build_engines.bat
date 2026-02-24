@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%\application" || (
    echo ERROR: Could not open application directory.
    exit /b 1
)

set "MODELS_DIR=%ROOT_DIR%\models"
if not exist "%MODELS_DIR%" (
    echo ERROR: Models directory not found: %MODELS_DIR%
    exit /b 1
)

where uv >nul 2>nul
if not %errorlevel%==0 (
    echo ERROR: uv is missing or not in PATH.
    echo Hint: run install.bat first or install uv.
    exit /b 1
)

set "FOUND_PT=0"
set "TOTAL_VARIANTS=0"
set "BUILT_VARIANTS=0"
set "SKIPPED_VARIANTS=0"
set "WARN_VARIANTS=0"

for %%M in ("%MODELS_DIR%\*.pt") do (
    if exist "%%~fM" (
        set "FOUND_PT=1"
        set "BASE=%%~nM"

        for %%S in (640 800 960 1280 1536 1920) do (
            set "IMG=%%S"
            set "ENGINE=%MODELS_DIR%\!BASE!_!IMG!.engine"
            set /a TOTAL_VARIANTS+=1

            if exist "!ENGINE!" (
                echo === Skipping !ENGINE! - already exists ===
                set /a SKIPPED_VARIANTS+=1
            )

            if not exist "!ENGINE!" (
                echo === Building !ENGINE! ===
                uv run --no-sync python -c "from ultralytics import YOLO; m=YOLO(r'%%~fM'); m.export(format='engine', imgsz=!IMG!, half=True, device=0)"

                if errorlevel 1 (
                    echo === Error: export failed for %%~nxM at size !IMG! ===
                    exit /b 1
                )

                set "DEFAULT_ENGINE=%MODELS_DIR%\!BASE!.engine"
                if exist "!DEFAULT_ENGINE!" (
                    move /Y "!DEFAULT_ENGINE!" "!ENGINE!" >nul
                    echo === Created !ENGINE! ===
                    set /a BUILT_VARIANTS+=1
                )

                if not exist "!DEFAULT_ENGINE!" (
                    echo === Warning: !DEFAULT_ENGINE! not found after export ===
                    set /a WARN_VARIANTS+=1
                )
            )
        )
    )
)

if "%FOUND_PT%"=="0" (
    echo ERROR: No .pt model files found in %MODELS_DIR%
    exit /b 1
)

echo === Engine build summary ===
echo Variants processed: !TOTAL_VARIANTS!
echo Built: !BUILT_VARIANTS!
echo Skipped: !SKIPPED_VARIANTS!
echo Warnings: !WARN_VARIANTS!
echo === Done ===
exit /b 0
