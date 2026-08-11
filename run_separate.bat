@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "INPUT=%~1"
if "%INPUT%"=="" (
  set "INPUT="
  for %%f in (*.mp4 *.mov *.mkv *.wav *.m4a *.MP4 *.MOV *.MKV *.WAV) do (
    if not defined INPUT set "INPUT=%%f"
  )
)
if "%INPUT%"=="" (
  echo No input file found in this folder.
  echo Drag your video/audio file onto this .bat, or run:  run_separate.bat path\to\file
  pause
  exit /b 1
)
if not exist "%INPUT%" (
  echo File not found: %INPUT%
  pause
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10-3.14 x64 from python.org and check "Add to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating environment and installing CPU PyTorch (a few minutes)...
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

echo.
echo Separating: %INPUT%
set "HF_ENDPOINT=https://hf-mirror.com"
".venv\Scripts\python" separate.py --input "%INPUT%" --device auto
if errorlevel 1 goto :err

echo.
echo DONE. Results are in work\separated\
pause
exit /b 0

:err
echo.
echo FAILED - see messages above.
echo See README_GPU.txt for common fixes.
pause
exit /b 1
