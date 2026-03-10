@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "ASSETS=%CD%\assets"
set "REPORT=%CD%\asset_duplicate_report.txt"
set "TMP_HASHES=%TEMP%\dragos_asset_hashes_%RANDOM%%RANDOM%.txt"

if not exist "%ASSETS%" (
  echo [!] assets folder not found.
  exit /b 1
)

if exist "%TMP_HASHES%" del "%TMP_HASHES%" >nul 2>nul

for /r "%ASSETS%" %%F in (*) do call :hash_file "%%~fF"

if not exist "%TMP_HASHES%" (
  echo No assets found. > "%REPORT%"
  echo Report written: %REPORT%
  exit /b 0
)

sort "%TMP_HASHES%" /o "%TMP_HASHES%"

> "%REPORT%" (
  echo Asset Duplicate Report
  echo Generated: %DATE% %TIME%
  echo.
)

set "PREV_HASH="
set "PREV_INFO="
set "GROUP_OPEN=0"
set "FOUND=0"

for /f "usebackq tokens=1,* delims=|" %%A in ("%TMP_HASHES%") do (
  set "CUR_HASH=%%A"
  set "CUR_INFO=%%B"
  if "!CUR_HASH!"=="!PREV_HASH!" (
    if "!GROUP_OPEN!"=="0" (
      >> "%REPORT%" echo Hash: !CUR_HASH!
      >> "%REPORT%" echo   - !PREV_INFO!
      set "GROUP_OPEN=1"
      set "FOUND=1"
    )
    >> "%REPORT%" echo   - !CUR_INFO!
  ) else (
    if "!GROUP_OPEN!"=="1" >> "%REPORT%" echo.
    set "GROUP_OPEN=0"
  )
  set "PREV_HASH=!CUR_HASH!"
  set "PREV_INFO=!CUR_INFO!"
)

if "!FOUND!"=="0" (
  > "%REPORT%" echo No duplicate assets found.
)

if exist "%TMP_HASHES%" del "%TMP_HASHES%" >nul 2>nul
echo Report written: %REPORT%
exit /b 0

:hash_file
set "FILE=%~1"
set "HASH="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%FILE%" SHA256 ^| findstr /R /V /C:"hash of file" /C:"CertUtil:" /C:"SHA256"') do (
  if not defined HASH set "HASH=%%H"
)
set "HASH=%HASH: =%"
if defined HASH >> "%TMP_HASHES%" echo %HASH%^|%~1
set "HASH="
exit /b 0
