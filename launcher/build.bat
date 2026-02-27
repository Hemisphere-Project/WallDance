@echo off
echo Building WallDance Launcher...

:: Ensure we are in the launcher directory
cd /d "%~dp0"

:: Check for virtual environment and create if missing
if not exist ".venv\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv .venv
)

:: Activate virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat

:: Install requirements
echo Installing requirements...
pip install -r requirements.txt

:: Get CustomTkinter path dynamically
for /f "delims=" %%i in ('python -c "import customtkinter; import os; print(os.path.dirname(customtkinter.__file__))"') do set CTK_PATH=%%i

echo CustomTkinter path: %CTK_PATH%

:: Run PyInstaller
echo Running PyInstaller...
if exist "dist\WallDanceLauncher.exe" del /q "dist\WallDanceLauncher.exe"
pyinstaller --noconfirm --onefile --windowed --name "WallDanceLauncher" --add-data "%CTK_PATH%;customtkinter/" --icon "icon.ico" --hidden-import win32timezone --clean main.py

:: Flush Windows icon cache so explorer picks up the new icon
ie4uinit.exe -show

echo Build complete. The executable is in the "dist" folder.
pause
