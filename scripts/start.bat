@echo off
REM Creasy — start the webhook + dashboard server.
REM IMPORTANT: never use unescaped "->" in echo lines (cmd redirect).

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

set "GIT_TERMINAL_PROMPT=0"
set "PYTHONUNBUFFERED=1"
if exist "%USERPROFILE%\.opencode\bin" set "PATH=%USERPROFILE%\.opencode\bin;%PATH%"
if exist "%ROOT%\vendor\bin" set "PATH=%ROOT%\vendor\bin;%PATH%"

echo ========================================
echo   Creasy
echo ========================================
echo Project : %ROOT%
echo Server  : http://127.0.0.1:%DASH_PORT%/
echo Dashboard: http://127.0.0.1:%DASH_PORT%/jobs
echo Webhook : POST http://127.0.0.1:%DASH_PORT%/webhook
echo.

if not exist "%VENV_PY%" (
    echo [ERROR] .venv is missing.
    echo Run scripts\install.bat first ^(offline wheels in vendor\python-wheels^).
    call :maybe_pause
    exit /b 1
)
echo Python  : %VENV_PY%

where git >nul 2>&1
if errorlevel 1 (
    echo [WARNING] git is not on PATH. Reviews cannot clone.
) else (
    echo [OK] git found
)

where opencode >nul 2>&1
if errorlevel 1 (
    echo [WARNING] opencode is not on PATH. Jobs will fail until OpenCode is installed.
    echo           Put the CLI on PATH or in vendor\bin.
) else (
    echo [OK] opencode on PATH
)

if exist "%ROOT%\.env.example" if not exist "%ROOT%\.env" (
    copy /y "%ROOT%\.env.example" "%ROOT%\.env" >nul
    echo [WARNING] Wrote .env from .env.example — set GITLAB_TOKEN and WEBHOOK_SECRET
)

if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"

echo Starting Creasy ^(Ctrl+C to stop^)...
"%VENV_PY%" -m creasy
set "EC=!ERRORLEVEL!"
echo.
echo Creasy exited. code=!EC!
>>"%ROOT%\logs\wrapper-exit.log" echo %DATE% %TIME% exit=!EC!
if not "!EC!"=="0" echo No Python traceback usually means the process was killed from outside.
call :maybe_pause
exit /b !EC!

:maybe_pause
if /i "%CREASY_NONINTERACTIVE%"=="1" exit /b 0
pause
exit /b 0
