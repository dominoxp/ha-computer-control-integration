@echo off
REM Delegiert an check.sh unter WSL: Home Assistant Core startet wegen des
REM POSIX-only fcntl-Moduls nicht unter nativem Windows-Python, siehe README.
REM check.sh legt sein eigenes .venv-wsl bei Bedarf selbst an.
REM Aufruf:  check.cmd  |  check.cmd --fix
setlocal
pushd "%~dp0"

where wsl >nul 2>nul
if errorlevel 1 (
    echo WSL wurde nicht gefunden - ohne WSL kann Home Assistant Core nicht
    echo getestet werden ^(siehe README, Abschnitt Entwicklung^).
    popd
    endlocal
    exit /b 1
)

for /f "usebackq delims=" %%i in (`wsl -d Ubuntu wslpath -a "%cd%"`) do set "WSL_PATH=%%i"

set "FIX_ARG="
if /I "%~1"=="--fix" set "FIX_ARG=--fix"

wsl -d Ubuntu -- bash "%WSL_PATH%/check.sh" %FIX_ARG%
set "RESULT=%ERRORLEVEL%"

popd
endlocal & exit /b %RESULT%
