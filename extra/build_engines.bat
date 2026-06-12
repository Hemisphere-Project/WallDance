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

rem Prevent ultralytics from auto-installing packages into the venv
set "YOLO_AUTOINSTALL=0"

rem ── Harvest weights already in application\ into models\ ───────────
rem (downloaded earlier) so they are not re-downloaded
rem and so the model manager — which reads from models\ — can find them.
rem yolo11 family only: Phase 2b benchmark removed yolo26 (ROADMAP 4.2 2b).
for %%N in (
    yolo11n-pose yolo11s-pose yolo11m-pose yolo11l-pose yolo11x-pose
) do (
    if not exist "%MODELS_DIR%\%%N.pt" if exist "%%N.pt" (
        echo === Found %%N.pt in application\, moving to models\ ===
        move /Y "%%N.pt" "%MODELS_DIR%\%%N.pt" >nul
    )
)

rem ── Offer to download missing pose models ──────────────────────────
set "MISSING_LIST="
set "MISSING_COUNT=0"
set "TOTAL_MODELS=5"

for %%N in (
    yolo11n-pose yolo11s-pose yolo11m-pose yolo11l-pose yolo11x-pose
) do (
    if not exist "%MODELS_DIR%\%%N.pt" (
        set "MISSING_LIST=!MISSING_LIST! %%N"
        set /a MISSING_COUNT+=1
    )
)

if !MISSING_COUNT! GTR 0 (
    echo === Missing pose models ^(!MISSING_COUNT!/!TOTAL_MODELS!^): ===
    for %%N in (!MISSING_LIST!) do echo   - %%N.pt
    echo.
    set /p "DL_ANSWER=Download missing models before building engines? [Y/n] "
    if "!DL_ANSWER!"=="" set "DL_ANSWER=Y"
    if /i "!DL_ANSWER!"=="Y" (
        for %%N in (!MISSING_LIST!) do (
            echo === Downloading %%N.pt ===
            uv run --no-sync python -c "import shutil,os;from ultralytics import YOLO;YOLO('%%N.pt');s='%%N.pt';d=os.path.join(r'%MODELS_DIR%',s);(os.path.isfile(s) and not os.path.abspath(s)==os.path.abspath(d)) and shutil.move(s,d)"
            if errorlevel 1 (
                echo === Warning: failed to download %%N.pt ===
            )
        )
        echo === Downloads complete ===
    ) else (
        echo Skipping downloads.
    )
    echo.
)

set "FOUND_PT=0"
set "TOTAL_VARIANTS=0"
set "BUILT_VARIANTS=0"
set "SKIPPED_VARIANTS=0"
set "WARN_VARIANTS=0"

rem Engines for m/l/x only (Phase 2b: n/s never the right auto pick);
rem n/s weights stay as last-resort insurance, engine built on demand.
for %%M in ("%MODELS_DIR%\yolo11m-pose.pt" "%MODELS_DIR%\yolo11l-pose.pt" "%MODELS_DIR%\yolo11x-pose.pt") do (
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
