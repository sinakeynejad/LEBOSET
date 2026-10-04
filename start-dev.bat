@echo off
setlocal DisableDelayedExpansion
title LEBOSET Development
pushd "%~dp0"
if errorlevel 1 goto location_error

if exist "backend\.venv\Scripts\python.exe" goto existing_python
py -3.12 -c "import sys; sys.exit(not sys.version_info >= (3, 12))" >nul 2>&1
if not errorlevel 1 goto python_launcher_312
py -3 -c "import sys; sys.exit(not sys.version_info >= (3, 12))" >nul 2>&1
if not errorlevel 1 goto python_launcher
python -c "import sys; sys.exit(not sys.version_info >= (3, 12))" >nul 2>&1
if not errorlevel 1 goto python_path

echo ERROR: Python 3.12 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH", then open this file again.
set "RESULT=1"
goto finish

:existing_python
"backend\.venv\Scripts\python.exe" "scripts\dev.py" %*
set "RESULT=%ERRORLEVEL%"
goto finish

:python_launcher_312
py -3.12 "scripts\dev.py" %*
set "RESULT=%ERRORLEVEL%"
goto finish

:python_launcher
py -3 "scripts\dev.py" %*
set "RESULT=%ERRORLEVEL%"
goto finish

:python_path
python "scripts\dev.py" %*
set "RESULT=%ERRORLEVEL%"
goto finish

:location_error
echo ERROR: Cannot access the LEBOSET project folder.
if not "%LEBOSET_NO_PAUSE%"=="1" pause
exit /b 1

:finish
popd
echo.
if not "%RESULT%"=="0" echo Setup stopped. Read the error above, fix it, and try again.
if not "%LEBOSET_NO_PAUSE%"=="1" pause
exit /b %RESULT%
