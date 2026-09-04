@echo off
REM =============================================================================
REM Creasy - start server (API + dashboard). Same pattern as OSM start-backend.bat.
REM IMPORTANT: never use unescaped "->" in echo lines (cmd redirect).
REM =============================================================================

setlocal EnableDelayedExpansion

set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
if exist "%HERE%\pyproject.toml" (
    set "ROOT=%HERE%"
) else if exist "%HERE%\..\pyproject.toml" (
    for %%I in ("%HERE%\..") do set "ROOT=%%~fI"
) else (
    echo [ERROR] Cannot find repo root ^(pyproject.toml^).
    call :maybe_pause
    exit /b 1
)
cd /d "%ROOT%"

set "DASH_PORT=8000"
if defined PORT set "DASH_PORT=%PORT%"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
set "CREASY_PY="
if exist "%VENV_PY%" set "CREASY_PY=%VENV_PY%"

set "GIT_TERMINAL_PROMPT=0"
set "PYTHONUNBUFFERED=1"
if exist "%USERPROFILE%\.opencode\bin" set "PATH=%USERPROFILE%\.opencode\bin;%PATH%"
if exist "%ROOT%\vendor\bin" set "PATH=%ROOT%\vendor\bin;%PATH%"

echo ========================================
echo   Creasy
echo ========================================
echo Project : %ROOT%
echo Server  : http://0.0.0.0:%DASH_PORT%/  ^(open http://127.0.0.1:%DASH_PORT%/jobs ^)
echo.

if not defined CREASY_PY (
    echo [ERROR] .venv is missing.
    echo Run scripts\install.bat first. It creates .venv from vendor\python\windows\python.exe.
    call :maybe_pause
    exit /b 1
)
echo Python  : %CREASY_PY%

where git >nul 2>&1
if errorlevel 1 (
    echo [WARNING] git is not on PATH. Reviews cannot clone.
) else (
    echo [OK] git found
)

where opencode >nul 2>&1
if errorlevel 1 (
    echo [WARNING] opencode is not on PATH. Jobs will fail until OpenCode is installed.
    echo           Run scripts\install-opencode.bat ^(keeps existing home, copies opencode-configs^).
) else (
    echo [OK] opencode on PATH
)

if exist "%ROOT%\.env.example" if not exist "%ROOT%\.env" (
    copy /y "%ROOT%\.env.example" "%ROOT%\.env" >nul
    echo [WARNING] Wrote .env from .env.example. Set GITLAB_TOKEN and WEBHOOK_SECRET.
)

if not exist "%ROOT%\web\dist\index.html" (
    echo [WARNING] web\dist\index.html missing. /jobs will 404.
    echo           Use the CI zip or run python packaging\build_dist.py --in-place.
)

if not exist "%ROOT%\scripts\run-server.bat" (
    echo [ERROR] scripts\run-server.bat not found.
    call :maybe_pause
    exit /b 1
)

echo Starting Creasy in window "Creasy"...
start "Creasy" /D "%ROOT%" "%ROOT%\scripts\run-server.bat"

echo Waiting for API http://127.0.0.1:%DASH_PORT%/health ...
set /a TRIES=0
:wait_backend
set /a TRIES+=1
if %TRIES% GTR 45 (
    echo [ERROR] Server did not become ready on port %DASH_PORT%.
    echo Open the "Creasy" window and read the traceback.
    echo Common issues: missing install, port in use, data_dir permissions.
    call :maybe_pause
    exit /b 1
)
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:%DASH_PORT%/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto wait_backend
)

echo.
echo [OK] Creasy is up.
echo   Health   : http://127.0.0.1:%DASH_PORT%/health
echo   Dashboard: http://127.0.0.1:%DASH_PORT%/jobs
echo   Webhook  : POST http://127.0.0.1:%DASH_PORT%/webhook
echo   LAN      : http://^<this-pc-ip^>:%DASH_PORT%/jobs
echo.
call :maybe_pause
exit /b 0

:maybe_pause
if /i "%CREASY_NONINTERACTIVE%"=="1" exit /b 0
pause
exit /b 0
