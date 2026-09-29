@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto no_python
py -3.12 --version >nul 2>nul
if errorlevel 1 goto no_python

set "VENV=.standalone-venv"
if not exist "%VENV%\Scripts\python.exe" (
  py -3.12 -m venv "%VENV%"
  if errorlevel 1 goto failed
)
if not exist "%VENV%\.seeosk-ready" (
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 goto failed
  "%VENV%\Scripts\python.exe" -m pip install -r requirements-standalone.txt
  if errorlevel 1 goto failed
  type nul > "%VENV%\.seeosk-ready"
) 

"%VENV%\Scripts\python.exe" -u engine\standalone.py %*
if errorlevel 1 goto failed
exit /b 0

:no_python
echo Python 3.12 is required. Install Python 3.12 from python.org and enable the Python Launcher.
pause
exit /b 1

:failed
echo SeeOSK did not start. Check the error above, camera permissions, and Python 3.12 installation.
pause
exit /b 1
