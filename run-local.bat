@echo off
rem ============================================================================
rem  Pashu Shield - one-click local run (Windows)
rem
rem  Creates .venv if missing, installs backend + ml-backend requirements,
rem  then starts BOTH servers in their own windows:
rem    * ML API (FastAPI/uvicorn)  -> http://127.0.0.1:8000
rem    * Backend + frontend (Flask) -> http://localhost:5001
rem  Finally opens http://localhost:5001 in the default browser.
rem
rem  Server windows use "cmd /k" so they stay open if a process crashes,
rem  and every path is quoted so folders with spaces work.
rem ============================================================================
setlocal EnableExtensions

rem --- resolve the folder this script lives in (handles spaces) -------------
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo.
echo ============================================================
echo  Pashu Shield - local development startup
echo  Project folder: %ROOT%
echo ============================================================
echo.

rem --- find a Python interpreter --------------------------------------------
set "PYCMD="
where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
    where py >nul 2>nul && set "PYCMD=py -3"
)
if not defined PYCMD (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         and re-run this script.
    echo.
    pause
    exit /b 1
)

rem --- 1. create .venv if absent ---------------------------------------------
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment .venv ...
    %PYCMD% -m venv "%ROOT%\.venv"
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv - see message above.
        echo.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Virtual environment .venv already exists.
)
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

rem --- 2. install dependencies ------------------------------------------------
echo [2/4] Installing dependencies (backend + ml-backend requirements) ...
"%VENV_PY%" -m pip install --quiet --upgrade pip >nul 2>nul
"%VENV_PY%" -m pip install -r "%ROOT%\backend\requirements.txt" -r "%ROOT%\ml-backend\requirements.txt"
if errorlevel 1 (
    echo [ERROR] pip install failed - see message above.
    echo         Fix the error and run run-local.bat again.
    echo.
    pause
    exit /b 1
)

rem --- 3. start the ML API (uvicorn on 127.0.0.1:8000) ------------------------
echo [3/4] Starting ML API (uvicorn main:app on http://127.0.0.1:8000) ...
start "Pashu Shield ML API - uvicorn :8000" /D "%ROOT%\ml-backend" cmd /k ""%VENV_PY%" -m uvicorn main:app --host 127.0.0.1 --port 8000"

rem --- 4. start the backend + frontend (python app.py on :5001) ---------------
echo [4/4] Starting backend + frontend (python app.py on http://localhost:5001) ...
start "Pashu Shield Backend - Flask :5001" /D "%ROOT%\backend" cmd /k "set PORT=5001&& set SIH_ML_BACKEND=http://127.0.0.1:8000&& "%VENV_PY%" app.py"

rem --- open the app once the backend has had a moment to boot -----------------
echo.
echo Waiting for the backend to come up ...
timeout /t 5 /nobreak >nul
start "" "http://localhost:5001"

echo.
echo ============================================================
echo  Pashu Shield is starting:
echo    App + API  :  http://localhost:5001
echo    ML API     :  http://127.0.0.1:8000  (docs at /docs)
echo.
echo  The two server windows stay open even if a process crashes;
echo  close them (or Ctrl+C inside) to stop the app.
echo ============================================================
echo.
pause
exit /b 0
