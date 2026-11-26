@echo off
setlocal EnableExtensions DisableDelayedExpansion
title eLabLink Attachment Browser Setup

set "setupFile=%~dp0install_browser.py"
if not exist "%setupFile%" goto :extract
if not exist "%~dp0browser_env.py" goto :extract

set "probe=import sys, sysconfig; sys.exit(0 if sys.version_info[:2] >= (3, 10) and sysconfig.get_platform() == 'win-amd64' else 1)"
set "pyOption="
for %%P in (py.exe) do set "candidate=%%~$PATH:P"
if defined candidate (
    "%candidate%" -3 -I -c "%probe%" >nul 2>&1 && goto :pythonLauncher
)

set "activePython="
if defined CONDA_PREFIX set "activePython=%CONDA_PREFIX%\python.exe"
for %%P in (python.exe) do set "pathPython=%%~$PATH:P"
for %%P in ("%activePython%" "%USERPROFILE%\anaconda3\python.exe" "%USERPROFILE%\miniconda3\python.exe" "%LOCALAPPDATA%\anaconda3\python.exe" "%LOCALAPPDATA%\miniconda3\python.exe" "%ProgramData%\anaconda3\python.exe" "%ProgramData%\miniconda3\python.exe" "%pathPython%") do (
    if exist "%%~P" (
        "%%~P" -I -c "%probe%" >nul 2>&1 && (
            set "candidate=%%~P"
            goto :start
        )
    )
)

echo Setup needs 64-bit Python 3.10 or newer for Windows x64.
echo Install Python from python.org, or run this file from your Anaconda Prompt.
goto :failed

:pythonLauncher
set "pyOption=-3"

:start
echo Starting eLabLink setup...
"%candidate%" %pyOption% -I "%setupFile%" %*
if errorlevel 1 goto :failed
if "%~1"=="" pause
exit /b 0

:extract
echo Extract all files from the installer ZIP, then run Install-eLabLink-Attachment-Browser.cmd again.

:failed
if "%~1"=="" pause
exit /b 1
