@echo off
title FreeCodeMonkey
rem Double-click launcher. Keeps the console open so errors remain visible.
setlocal EnableExtensions

cd /d "%~dp0"

set "PYEXE=.venv\Scripts\python.exe"

if not exist "%PYEXE%" goto NO_VENV
if not exist "main.py" goto NO_MAIN

echo Starting FreeCodeMonkey ...
"%PYEXE%" main.py
set "RC=%ERRORLEVEL%"

echo.
echo Application exited with code %RC%.
echo Press any key to close this window.
pause
endlocal
exit /b %RC%

:NO_VENV
echo [ERROR] Virtual environment not found: %PYEXE%
echo.
echo Create it first:
echo   python -m venv .venv
echo Then install dependencies:
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo Press any key to close this window.
pause
exit /b 1

:NO_MAIN
echo [ERROR] main.py not found in %CD%
echo.
echo Press any key to close this window.
pause
exit /b 1
