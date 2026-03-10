@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "FOLDER_NAME=Drago's Moderation Tool"
set "SKIP_DEPS=0"
set "NO_SHORTCUT=0"
set "FORCE=0"

:parse_args
if "%~1"=="" goto parsed
if /I "%~1"=="-SkipDeps" (
  set "SKIP_DEPS=1"
  shift
  goto parse_args
)
if /I "%~1"=="--skip-deps" (
  set "SKIP_DEPS=1"
  shift
  goto parse_args
)
if /I "%~1"=="-NoShortcut" (
  set "NO_SHORTCUT=1"
  shift
  goto parse_args
)
if /I "%~1"=="--no-shortcut" (
  set "NO_SHORTCUT=1"
  shift
  goto parse_args
)
if /I "%~1"=="-Force" (
  set "FORCE=1"
  shift
  goto parse_args
)
if /I "%~1"=="--force" (
  set "FORCE=1"
  shift
  goto parse_args
)
if /I "%~1"=="-FolderName" (
  if "%~2"=="" (
    echo [!] Missing value after -FolderName
    exit /b 1
  )
  set "FOLDER_NAME=%~2"
  shift
  shift
  goto parse_args
)
if /I "%~1"=="--folder" (
  if "%~2"=="" (
    echo [!] Missing value after --folder
    exit /b 1
  )
  set "FOLDER_NAME=%~2"
  shift
  shift
  goto parse_args
)
echo [!] Unknown option: %~1
exit /b 1

:parsed
echo.
echo By continuing, you agree that you are solely responsible for how this software is used.
echo See DISCLAIMER.txt for full terms.
echo.
if "%FORCE%" neq "1" (
  choice /M "Continue installation"
  if errorlevel 2 (
    echo Installation cancelled.
    exit /b 1
  )
)

for %%I in ("%~dp0.") do set "SOURCE_ROOT=%%~fI"
set "DESKTOP=%USERPROFILE%\Desktop"
set "TARGET_ROOT=%DESKTOP%\%FOLDER_NAME%"

echo [1/5] Preparing install location: %TARGET_ROOT%
if not exist "%TARGET_ROOT%" mkdir "%TARGET_ROOT%"
if errorlevel 1 exit /b %errorlevel%

if /I "%SOURCE_ROOT%"=="%TARGET_ROOT%" (
  echo [2/5] Installer is running from the target folder. Skipping file copy.
) else (
  echo [2/5] Syncing app files and resources...
  robocopy "%SOURCE_ROOT%" "%TARGET_ROOT%" /MIR /R:1 /W:1 /NFL /NDL /NP /NJH /NJS ^
    /XD ".git" ".venv" "__pycache__" "dist" "build" "build_pyinstaller" "build_pyinstaller_gui" "tmp_previews" ^
    /XF "*.pyc" "*.pyo" "moderation_tool_settings.json" "players.txt" "active_players.txt" "active_ips.txt" "session_history.log"
  if errorlevel 8 (
    echo [!] File sync failed during sync.
    exit /b 1
  )
)

if "%SKIP_DEPS%"=="1" (
  echo [3/5] Dependency installation skipped.
) else (
  echo [3/5] Creating/updating Python environment...
  call "%TARGET_ROOT%\install_deps.bat" --target "%TARGET_ROOT%"
  if errorlevel 1 exit /b %errorlevel%
)

set "DESKTOP_BAT=%DESKTOP%\Drago's Moderation Tool.bat"
(
  echo @echo off
  echo setlocal
  echo cd /d "%TARGET_ROOT%"
  echo call "%TARGET_ROOT%\run_tool.bat" %%%%*
) > "%DESKTOP_BAT%"
echo [4/5] Desktop launcher created: %DESKTOP_BAT%

if "%NO_SHORTCUT%"=="1" (
  echo [5/5] Shortcut creation skipped.
  goto done
)

set "SHORTCUT_PATH=%DESKTOP%\Drago's Moderation Tool.lnk"
set "ICON_PATH=%TARGET_ROOT%\assets\icons\favicon.ico"
set "VBS_FILE=%TEMP%\dragos_moderation_tool_shortcut_%RANDOM%%RANDOM%.vbs"
(
  echo Set oWS = CreateObject("WScript.Shell"^)
  echo Set oLink = oWS.CreateShortcut("%SHORTCUT_PATH%"^)
  echo oLink.TargetPath = "%TARGET_ROOT%\run_tool.bat"
  echo oLink.WorkingDirectory = "%TARGET_ROOT%"
  echo oLink.IconLocation = "%ICON_PATH%"
  echo oLink.Save
) > "%VBS_FILE%"
cscript //nologo "%VBS_FILE%" >nul 2>nul
if exist "%VBS_FILE%" del "%VBS_FILE%" >nul 2>nul
if errorlevel 1 (
  echo [5/5] Could not create desktop shortcut.
) else (
  echo [5/5] Desktop shortcut created: %SHORTCUT_PATH%
)

:done
echo.
echo Install complete.
echo Folder: %TARGET_ROOT%
echo Run:    %DESKTOP_BAT%
