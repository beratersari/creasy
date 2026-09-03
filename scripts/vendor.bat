@echo off
REM Download Python wheels into vendor\python-wheels (needs network).
REM Copy vendor\ + the repo to an air-gapped machine, then run install.bat.
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
set "WHEELS=%ROOT%\vendor\python-wheels"

set "PY="
if exist "%ROOT%\vendor\python\windows\python.exe" set "PY=%ROOT%\vendor\python\windows\python.exe"
if not defined PY (
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
    echo [ERROR] No Python on PATH. Install Python 3.10+ or place python.exe at
    echo         vendor\python\windows\python.exe
    call :maybe_pause
    exit /b 1
)

echo ========================================
echo   Creasy - vendor wheels ^(online^)
echo ========================================
echo Project : %ROOT%
echo Python  : %PY%
echo Wheels  : %WHEELS%
echo.

if not exist "%WHEELS%" mkdir "%WHEELS%"

echo Downloading pip / setuptools / wheel...
"%PY%" -m pip download -d "%WHEELS%" pip setuptools wheel
if errorlevel 1 (
    echo [ERROR] pip download of build tools failed.
    call :maybe_pause
    exit /b 1
)

echo Downloading Creasy runtime dependencies...
"%PY%" -m pip download -d "%WHEELS%" "fastapi>=0.115" "uvicorn[standard]>=0.32" "httpx>=0.27" "pydantic>=2.0" "python-dotenv>=1.0"
if errorlevel 1 (
    echo [ERROR] pip download of runtime deps failed.
    call :maybe_pause
    exit /b 1
)

echo.
echo [OK] Wheels are in vendor\python-wheels
echo Copy this repo ^(including vendor\^) to the offline host, then:
echo   scripts\install.bat
echo   scripts\start.bat
echo.
call :maybe_pause
exit /b 0

:maybe_pause
if /i "%CREASY_NONINTERACTIVE%"=="1" exit /b 0
pause
exit /b 0
