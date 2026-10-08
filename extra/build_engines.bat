@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Build TensorRT engines: the app's DEFAULT engine first (core\config.py
rem YOLO_MODEL @ YOLO_IMGSZ -- yolo11x-pose @ 1280 since D33), then the m/l/x grid.
rem
rem   extra\build_engines.bat                  default engine, then the whole grid
rem   extra\build_engines.bat --default-only   only the default engine (a few minutes)
set "DEFAULT_ONLY=0"
if /i "%~1"=="--default-only" set "DEFAULT_ONLY=1"

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

rem ── The model/imgsz a new project runs (D33) ───────────────────────
rem Read from config.py so the script cannot drift from the app; the
rem fallback only covers a broken venv.
set "DEF_BASE=yolo11x-pose"
set "DEF_IMGSZ=1280"
for /f "usebackq tokens=1,2" %%A in (`uv run --no-sync python -c "import sys; sys.path.insert(0, 'src'); from core.config import YOLO_MODEL, YOLO_IMGSZ; print(YOLO_MODEL.replace('.pt', ''), YOLO_IMGSZ)" 2^>nul`) do (
    set "DEF_BASE=%%A"
    set "DEF_IMGSZ=%%B"
)
echo === Default engine: !DEF_BASE! @ !DEF_IMGSZ! ===

rem The default model is not optional: fetch it without asking.
if not exist "%MODELS_DIR%\!DEF_BASE!.pt" call :download_model !DEF_BASE!

rem ── Offer to download missing pose models ──────────────────────────
set "MISSING_LIST="
set "MISSING_COUNT=0"
set "TOTAL_MODELS=5"

if "%DEFAULT_ONLY%"=="0" (
    for %%N in (
        yolo11n-pose yolo11s-pose yolo11m-pose yolo11l-pose yolo11x-pose
    ) do (
        if not exist "%MODELS_DIR%\%%N.pt" (
            set "MISSING_LIST=!MISSING_LIST! %%N"
            set /a MISSING_COUNT+=1
        )
    )
)

if !MISSING_COUNT! GTR 0 (
    echo === Missing pose models ^(!MISSING_COUNT!/!TOTAL_MODELS!^): ===
    for %%N in (!MISSING_LIST!) do echo   - %%N.pt
    echo.
    set /p "DL_ANSWER=Download missing models before building engines? [Y/n] "
    if "!DL_ANSWER!"=="" set "DL_ANSWER=Y"
    if /i "!DL_ANSWER!"=="Y" (
        for %%N in (!MISSING_LIST!) do call :download_model %%N
        echo === Downloads complete ===
    ) else (
        echo Skipping downloads.
    )
    echo.
)

set "TOTAL_VARIANTS=0"
set "BUILT_VARIANTS=0"
set "SKIPPED_VARIANTS=0"
set "WARN_VARIANTS=0"

rem ── The default engine FIRST (D33) ─────────────────────────────────
rem What a new project runs.  Without it the app runs PyTorch (3-7x slower)
rem and says so (banner + readiness FAIL).  Built before the grid, so an
rem interrupted or failed grid still leaves a show-ready laptop.
call :build_one !DEF_BASE! !DEF_IMGSZ!
if errorlevel 1 (
    echo ================================================================
    echo ERROR: the DEFAULT engine !DEF_BASE!_!DEF_IMGSZ!.engine was NOT built.
    echo The app would run PyTorch ^(3-7x slower^). Fix the error above and re-run.
    echo ================================================================
    exit /b 1
)
if "%DEFAULT_ONLY%"=="1" (
    echo === Default engine ready: %MODELS_DIR%\!DEF_BASE!_!DEF_IMGSZ!.engine ===
    exit /b 0
)

set "FOUND_PT=0"

rem Engines for m/l/x only (Phase 2b: n/s never the right auto pick);
rem n/s weights stay as last-resort insurance, engine built on demand.
for %%N in (yolo11m-pose yolo11l-pose yolo11x-pose) do (
    if exist "%MODELS_DIR%\%%N.pt" (
        set "FOUND_PT=1"
        for %%S in (640 800 960 1280 1536 1920) do (
            call :build_one %%N %%S
            rem 1 = the export failed: stop (as before); 2 = a warning: go on
            if !errorlevel! EQU 1 goto :summary_fail
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

:summary_fail
echo === Engine build summary ^(stopped at the first failure^) ===
echo Variants processed: !TOTAL_VARIANTS!
echo Built: !BUILT_VARIANTS!
echo Skipped: !SKIPPED_VARIANTS!
echo Warnings: !WARN_VARIANTS!
echo The default engine !DEF_BASE!_!DEF_IMGSZ!.engine is in place.
exit /b 1

rem ── Subroutines ─────────────────────────────────────────────────────
:download_model
rem %~1 = model base name (e.g. yolo11x-pose)
echo === Downloading %~1.pt ===
uv run --no-sync python -c "import shutil,os;from ultralytics import YOLO;YOLO('%~1.pt');s='%~1.pt';d=os.path.join(r'%MODELS_DIR%',s);(os.path.isfile(s) and not os.path.abspath(s)==os.path.abspath(d)) and shutil.move(s,d)"
if errorlevel 1 echo === Warning: failed to download %~1.pt ===
exit /b 0

:build_one
rem %~1 = model base name, %~2 = image size.  errorlevel 0 = the engine exists,
rem 1 = no weights / the export failed, 2 = exported but the file was not found.
set "B1_PT=%MODELS_DIR%\%~1.pt"
set "B1_ENGINE=%MODELS_DIR%\%~1_%~2.engine"
set "B1_EXPORTED=%MODELS_DIR%\%~1.engine"
set /a TOTAL_VARIANTS+=1
if exist "!B1_ENGINE!" (
    echo === Skipping !B1_ENGINE! - already exists ===
    set /a SKIPPED_VARIANTS+=1
    exit /b 0
)
if not exist "!B1_PT!" (
    echo === Error: !B1_PT! not found ===
    exit /b 1
)
echo === Building !B1_ENGINE! ===
uv run --no-sync python -c "from ultralytics import YOLO; m=YOLO(r'!B1_PT!'); m.export(format='engine', imgsz=%~2, half=True, device=0)"
if errorlevel 1 (
    echo === Error: export failed for %~1.pt at size %~2 ===
    exit /b 1
)
if exist "!B1_EXPORTED!" (
    move /Y "!B1_EXPORTED!" "!B1_ENGINE!" >nul
    echo === Created !B1_ENGINE! ===
    set /a BUILT_VARIANTS+=1
    exit /b 0
)
echo === Warning: !B1_EXPORTED! not found after export ===
set /a WARN_VARIANTS+=1
exit /b 2
