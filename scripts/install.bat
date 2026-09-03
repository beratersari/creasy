@echo off
REM Creasy - offline install. Creates .venv from vendor\python-wheels.
REM Does NOT install OpenCode. Put the CLI on PATH or in vendor\bin.
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
set "VENV_DIR=%ROOT%\.venv"
set "WHEELS=%ROOT%\vendor\python-wheels"
cd /d "%ROOT%"

echo ========================================
echo   Creasy
echo   Install ^(offline^)
echo ========================================
echo.
echo Project : %ROOT%
echo.

if not exist "%WHEELS%" (
    echo [ERROR] vendor\python-wheels is missing.
    echo This installer is offline-only. On a machine with network run:
    echo   scripts\vendor.bat
    echo then copy vendor\python-wheels with the repo.
    call :maybe_pause
    exit /b 1
)

set "BUNDLED_PY=%ROOT%\vendor\python\windows\python.exe"
set "PY="
if exist "%BUNDLED_PY%" (
    set "PY=%BUNDLED_PY%"
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=*" %%a in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%a"
    )
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=*" %%a in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%a"
    )
)
if not defined PY (
    echo [ERROR] No Python interpreter.
    echo Place python.exe at vendor\python\windows\python.exe or install Python 3.10+.
    call :maybe_pause
    exit /b 1
)
for /f "tokens=*" %%a in ('"%PY%" --version 2^>^&1') do set "PYTHON_VERSION=%%a"
echo [OK] %PYTHON_VERSION%
echo      %PY%

where git >nul 2>&1
if errorlevel 1 (
    echo [WARNING] git is not on PATH. Clone jobs will fail until Git is installed.
) else (
    echo [OK] git found
)

echo.
echo Step 1: Python virtual environment...
if exist "%VENV_DIR%" (
    echo Removing existing .venv so it matches this interpreter...
    rmdir /s /q "%VENV_DIR%"
    if exist "%VENV_DIR%" (
        echo [ERROR] Could not remove %VENV_DIR%
        call :maybe_pause
        exit /b 1
    )
)
"%PY%" -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    call :maybe_pause
    exit /b 1
)
echo [OK] Created %VENV_DIR%
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo.
echo Step 2: Installing packages from vendor\python-wheels ^(no network^)...
"%VENV_PY%" -m pip install --upgrade pip --no-index --find-links="%WHEELS%"
if errorlevel 1 (
    echo [ERROR] Offline pip upgrade failed.
    call :maybe_pause
    exit /b 1
)
"%VENV_PY%" -m pip install --no-index --find-links="%WHEELS%" -e .
if errorlevel 1 (
    echo [ERROR] Offline package install failed.
    echo Wheels must match this interpreter. Re-run scripts\vendor.bat on the same OS.
    dir /b "%WHEELS%\*.whl" 2>nul
    call :maybe_pause
    exit /b 1
)
echo [OK] Creasy installed into .venv from local wheels

if exist "%ROOT%\.env.example" if not exist "%ROOT%\.env" (
    copy /y "%ROOT%\.env.example" "%ROOT%\.env" >nul
    echo [OK] Wrote .env from .env.example — set GITLAB_TOKEN and WEBHOOK_SECRET
)

if exist "%ROOT%\web\index.html" (
    echo [OK] Dashboard present: web\index.html
) else (
    echo [WARNING] web\index.html missing — /jobs will 404
)

echo.
echo ========================================
echo   Install complete
echo ========================================
echo.
echo Edit .env then:
echo   scripts\start.bat
echo Dashboard: http://127.0.0.1:8000/jobs
echo.
call :maybe_pause
exit /b 0

:maybe_pause
if /i "%CREASY_NONINTERACTIVE%"=="1" exit /b 0
pause
exit /b 0
