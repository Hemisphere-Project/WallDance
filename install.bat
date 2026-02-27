@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%application" || (
    echo ERROR: Could not open application directory.
    echo Hint: run install.bat from the WallDance repository root.
    exit /b 1
)

call :detect_python
if errorlevel 1 (
    echo ERROR: Python 3 is missing or not callable.
    echo Hint: install Python 3.10-3.12 from https://www.python.org/downloads/windows/
    echo       or run: winget install Python.Python.3.12
    exit /b 1
)

call :detect_uv
if not defined UV_CMD (
    echo uv was not found. Attempting to install uv with pip...
    %PY_CMD% -m pip install -U uv
    if errorlevel 1 (
        echo ERROR: Failed to install uv.
        echo Hint: install uv from https://docs.astral.sh/uv/getting-started/installation/
        echo       or run: winget install astral-sh.uv
        exit /b 1
    )
    call :detect_uv
)

if not defined UV_CMD (
    echo ERROR: uv is missing or not callable.
    echo Hint: install uv from https://docs.astral.sh/uv/getting-started/installation/
    echo       or run: winget install astral-sh.uv
    exit /b 1
)

rem ── Detect NVIDIA GPU ──────────────────────────────────────────────────────
set "HAS_GPU=0"
where nvidia-smi >nul 2>nul
if not errorlevel 1 (
    nvidia-smi >nul 2>nul
    if not errorlevel 1 set "HAS_GPU=1"
)

rem Allow manual override: install.bat --cpu  or  install.bat --gpu
for %%A in (%*) do (
    if /I "%%A"=="--cpu" set "HAS_GPU=0"
    if /I "%%A"=="--gpu" set "HAS_GPU=1"
)

if "%HAS_GPU%"=="1" (
    echo [WallDance] NVIDIA GPU detected - installing with CUDA support.
) else (
    echo [WallDance] No NVIDIA GPU detected - installing CPU-only ^(lower FPS, but works for dev/test^).
)

rem ── Generate uv.toml – override the "pytorch" named index URL ──────────────
rem pyproject.toml declares a named index "pytorch" (explicit = true) so only
rem torch and torchvision are fetched from it; everything else uses PyPI.
if "%HAS_GPU%"=="1" (
    set "PYTORCH_INDEX=https://download.pytorch.org/whl/cu130"
) else (
    set "PYTORCH_INDEX=https://download.pytorch.org/whl/cpu"
)

(
    echo index-strategy = "unsafe-best-match"
    echo.
    echo [[index]]
    echo name = "pytorch"
    echo url = "!PYTORCH_INDEX!"
    echo explicit = true
) > uv.toml

rem ── Remove stale lock file (index URLs may have changed) ──────────────────
if exist "uv.lock" del /q uv.lock

rem ── Sync dependencies ─────────────────────────────────────────────────────
set "UV_EXTRAS="
if "%HAS_GPU%"=="1" set "UV_EXTRAS=--extra gpu"

echo [WallDance] Resolving and installing dependencies (this may take a few minutes)...

%UV_CMD% sync %UV_EXTRAS% --extra ids
if errorlevel 1 (
    echo [WallDance] IDS camera SDK not available - installing without IDS support.
    echo [WallDance] ^(This is normal on laptops / dev machines.^)
    %UV_CMD% sync %UV_EXTRAS%
)

if errorlevel 1 (
    echo ERROR: Dependency installation failed.
    exit /b 1
)

echo [WallDance] Checking PyTorch/CUDA compatibility...
call :check_torch_cuda

echo.
echo Installation complete!
echo Run run.bat to start WallDance pose detection.
exit /b 0

:detect_python
set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -V >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3"
)
if defined PY_CMD exit /b 0

where python >nul 2>nul
if errorlevel 1 exit /b 1
python -V >nul 2>nul
if errorlevel 1 exit /b 1
set "PY_CMD=python"
exit /b 0

:detect_uv
set "UV_CMD="
where uv >nul 2>nul
if not errorlevel 1 (
    set "UV_CMD=uv"
    exit /b 0
)

if defined PY_CMD (
    %PY_CMD% -m uv --version >nul 2>nul
    if not errorlevel 1 set "UV_CMD=%PY_CMD% -m uv"
)
exit /b 0

:check_torch_cuda
%UV_CMD% run --no-sync python -c "import torch" >nul 2>nul
if errorlevel 1 (
    echo WARNING: PyTorch import failed. Runtime will likely use CPU fallback.
    echo Hint: run install.bat again or check Python environment consistency.
    exit /b 0
)

if "%HAS_GPU%"=="1" (
    set "CUDA_OK="
    for /f "usebackq delims=" %%A in (`%UV_CMD% run --no-sync python -c "import torch; print('1' if torch.cuda.is_available() else '0')" 2^>nul`) do set "CUDA_OK=%%A"

    if not "!CUDA_OK!"=="1" (
        echo WARNING: CUDA not available to PyTorch despite NVIDIA GPU being present.
        echo [WallDance] This likely means the PyTorch index did not have a CUDA build for the required version.
        echo [WallDance] Attempting automatic CUDA PyTorch upgrade...
        call :auto_fix_torch sm_auto
        rem Re-check after fix attempt
        set "CUDA_OK2="
        for /f "usebackq delims=" %%A in (`%UV_CMD% run --no-sync python -c "import torch; print('1' if torch.cuda.is_available() else '0')" 2^>nul`) do set "CUDA_OK2=%%A"
        if not "!CUDA_OK2!"=="1" (
            echo WARNING: CUDA still not available after auto-fix. Continuing in CPU mode.
            echo Fix: ensure CUDA drivers are installed, then run install.bat again.
        ) else (
            echo OK: CUDA is now available after PyTorch upgrade.
        )
        exit /b 0
    )

    set "CUDA_PROBE="
    for /f "usebackq tokens=1,2,3,* delims=|" %%A in (`%UV_CMD% run --no-sync python -c "import torch; n=torch.cuda.get_device_name(0); cc=torch.cuda.get_device_capability(0); need=f'sm_{cc[0]}{cc[1]}'; arch=torch.cuda.get_arch_list() if hasattr(torch.cuda,'get_arch_list') else []; ok=(not arch) or (need in arch); print(('OK' if ok else 'MISMATCH') + '|' + n + '|' + need + '|' + ','.join(arch))" 2^>nul`) do (
        set "CUDA_PROBE=1"
        if /I "%%A"=="MISMATCH" (
            echo WARNING: GPU architecture mismatch: %%B requires %%C.
            echo Current PyTorch build supports: %%D
            echo WallDance will fall back to CPU ^(low FPS^).
            echo Fix: install a newer PyTorch build that supports your GPU architecture.
            if defined WALLDANCE_SKIP_TORCH_AUTOFIX (
                echo Auto-fix skipped due to WALLDANCE_SKIP_TORCH_AUTOFIX.
            ) else (
                call :auto_fix_torch %%C
            )
        ) else (
            echo OK: PyTorch CUDA is available on %%B ^(%%C^).
        )
    )

    if not defined CUDA_PROBE (
        echo WARNING: Could not fully validate CUDA architecture support.
        echo If run.bat falls back to CPU, update PyTorch/CUDA for your GPU.
    )
) else (
    echo OK: PyTorch ^(CPU^) is ready.
    echo Tip: use a smaller model for better CPU performance:
    echo      In config.py set YOLO_MODEL = "yolo11n-pose.pt" and YOLO_IMGSZ = 640
)
exit /b 0

:auto_fix_torch
set "REQ_SM=%~1"
if not defined REQ_SM set "REQ_SM=sm_auto"

echo [WallDance] Attempting automatic PyTorch upgrade for %REQ_SM%...
echo [WallDance] Trying PyTorch CUDA wheels in order: cu130, cu129, cu128, cu126, cu124

for %%I in (cu130 cu129 cu128 cu126 cu124) do (
    echo [WallDance] Trying %%I...
    %UV_CMD% pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/%%I
    if not errorlevel 1 (
        set "AUTO_OK="
        if "%REQ_SM%"=="sm_auto" (
            rem Just check if CUDA is generally available
            for /f "usebackq delims=" %%Z in (`%UV_CMD% run --no-sync python -c "import torch; print('1' if torch.cuda.is_available() else '0')" 2^>nul`) do set "AUTO_OK=%%Z"
        ) else (
            for /f "usebackq delims=" %%Z in (`%UV_CMD% run --no-sync python -c "import torch; need=r'%REQ_SM%'; arch=torch.cuda.get_arch_list() if hasattr(torch.cuda,'get_arch_list') else []; ok=torch.cuda.is_available() and ((not arch) or (need in arch)); print('1' if ok else '0')" 2^>nul`) do set "AUTO_OK=%%Z"
        )
        if "!AUTO_OK!"=="1" (
            echo OK: Automatic PyTorch upgrade succeeded with %%I.
            exit /b 0
        ) else (
            echo [WallDance] %%I installed but CUDA still unavailable.
        )
    ) else (
        echo [WallDance] Install attempt with %%I failed.
    )
)

echo WARNING: Automatic PyTorch upgrade did not resolve GPU architecture support.
echo Action: use the latest stable/nightly command from https://pytorch.org/get-started/locally/
echo         then re-run install.bat.
exit /b 0
