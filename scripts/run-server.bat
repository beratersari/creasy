@echo off
REM Runs Creasy in the Creasy window. Do not nest this inside a quoted cmd /c.
setlocal EnableDelayedExpansion
cd /d "%~dp0\.."
set "GIT_TERMINAL_PROMPT=0"
set "GIT_SSL_NO_VERIFY=1"
set "PYTHONUNBUFFERED=1"
if exist "%USERPROFILE%\.opencode\bin" set "PATH=%USERPROFILE%\.opencode\bin;%PATH%"
if exist "%CD%\vendor\bin" set "PATH=%CD%\vendor\bin;%PATH%"
if not exist "%CD%\logs" mkdir "%CD%\logs"
set "CREASY_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%CREASY_PY%" (
    echo [ERROR] .venv python missing: %CREASY_PY%
    pause
    exit /b 1
)
"%CREASY_PY%" -m creasy
set "EC=!ERRORLEVEL!"
echo.
echo Creasy exited. code=!EC!
>>"%CD%\logs\wrapper-exit.log" echo %DATE% %TIME% exit=!EC!
if not "!EC!"=="0" echo No Python traceback usually means the process was killed from outside.
pause
exit /b !EC!
