@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  goto run_venv
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  goto run_py
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  goto run_python
)

echo [!] Python 3 was not found. Run install_deps.bat first.
exit /b 1

:run_venv
set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m kryzln_vrc_logger.logger %*
exit /b %ERRORLEVEL%

:run_py
set "PYTHONPATH=%CD%\src"
py -3 -m kryzln_vrc_logger.logger %*
exit /b %ERRORLEVEL%

:run_python
set "PYTHONPATH=%CD%\src"
python -m kryzln_vrc_logger.logger %*
exit /b %ERRORLEVEL%
