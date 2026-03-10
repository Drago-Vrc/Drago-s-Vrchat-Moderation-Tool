@echo off
setlocal EnableExtensions

set "TARGET_ROOT=%~dp0"

:parse_args
if "%~1"=="" goto parsed
if /I "%~1"=="--target" (
  if "%~2"=="" (
    echo [!] Missing path after --target
    exit /b 1
  )
  set "TARGET_ROOT=%~2"
  shift
  shift
  goto parse_args
)
echo [!] Unknown option: %~1
exit /b 1

:parsed
cd /d "%TARGET_ROOT%"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto run

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 -m venv .venv
  goto run
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python -m venv .venv
  goto run
)

echo [!] Python 3 was not found. Install Python 3.11+ and run again.
exit /b 1

:run
"%PYTHON_EXE%" -m pip install --upgrade pip
"%PYTHON_EXE%" -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
echo [+] Dependencies installed in .venv
