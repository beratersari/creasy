@echo off
REM Online machine: fetch bundled CPython, OpenCode CLI, and wheels.
REM Same as: python packaging\build_dist.py --in-place
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

set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%a in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%a"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=*" %%a in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%a"
    )
)
if not defined PY (
    echo [ERROR] Need a network Python to run packaging\build_dist.py --in-place.
    call :maybe_pause
    exit /b 1
)

echo ========================================
echo   Creasy - build_dist --in-place
echo ========================================
"%PY%" "%ROOT%\packaging\build_dist.py" --in-place
if errorlevel 1 (
    echo [ERROR] build_dist failed.
    call :maybe_pause
    exit /b 1
)
echo.
echo Then on this machine or after copying vendor\:
echo   scripts\install.bat
echo   scripts\install-opencode.bat
echo   scripts\start.bat
echo.
call :maybe_pause
exit /b 0

:maybe_pause
if /i "%CREASY_NONINTERACTIVE%"=="1" exit /b 0
pause
exit /b 0
