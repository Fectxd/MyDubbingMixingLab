@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VIDEO=%~1"
if "%VIDEO%"=="" (
  set "VIDEO="
  for %%f in (*.mp4 *.mov *.mkv *.m4a *.MP4 *.MOV *.MKV) do (
    if not defined VIDEO set "VIDEO=%%f"
  )
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10-3.14 x64 from python.org and check "Add to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating environment and installing dependencies (a few minutes)...
  python -m venv .venv
  if errorlevel 1 goto :err
  ".venv\Scripts\python" -m pip install --upgrade pip
  if errorlevel 1 goto :err
  ".venv\Scripts\python" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
  if errorlevel 1 goto :err
  ".venv\Scripts\python" -m pip install -r requirements_gpu.txt
  if errorlevel 1 goto :err
  echo Environment ready.
)

if not "%VIDEO%"=="" (
  echo Running full pipeline on: %VIDEO%
  set "HF_ENDPOINT=https://hf-mirror.com"
  ".venv\Scripts\python" run_all.py --video "%VIDEO%"
) else (
  echo Running full pipeline (auto-detecting picture file)...
  set "HF_ENDPOINT=https://hf-mirror.com"
  ".venv\Scripts\python" run_all.py
)
if errorlevel 1 goto :err

echo.
echo DONE. See work\separated\, work\enhanced\, work\reaper\ and work\final\ (成片 mp4).
pause
exit /b 0

:err
echo.
echo FAILED - see messages above.
pause
exit /b 1
