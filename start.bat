@echo off
REM Neptune one-click launcher (Windows). Double-click this file, or run `.\start.bat`.
REM Uses the project's own .venv directly, so you NEVER have to "activate" anything
REM (which sidesteps PowerShell's execution-policy prompt). First run creates the venv
REM and installs dependencies; later runs just start the app.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] creating .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [setup] ERROR: could not create the venv. Is Python on PATH? Try `py -3.12 -m venv .venv`.
        pause
        exit /b 1
    )
)

REM Install deps only if a core import is missing (fast no-op on subsequent runs).
".venv\Scripts\python.exe" -c "import fastapi, sqlalchemy, uvicorn, numpy, cvxpy" 1>nul 2>nul
if errorlevel 1 (
    echo [setup] installing dependencies ^(first run^) ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo [run] Neptune  -  UI http://localhost:5176   API http://localhost:8000
".venv\Scripts\python.exe" run.py
