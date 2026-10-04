@echo off
REM Bidder Asset Test - double-click to start (Windows).
REM First run on a new computer sets everything up automatically (needs internet, about 5 minutes, no admin rights).
cd /d "%~dp0"
title Bidder Asset Test
echo === Bidder Asset Test ===

".venv\Scripts\python.exe" -c "import pdfplumber, openpyxl, pypdfium2, rapidocr, onnxruntime" >nul 2>nul
if not errorlevel 1 goto run

echo First-time setup on this computer - please wait, needs internet, about 5 minutes...
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%UV%" goto haveuv
echo Downloading the Python installer uv...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_NO_MODIFY_PATH='1'; irm https://astral.sh/uv/install.ps1 | iex"
:haveuv
if not exist "%UV%" goto fail
if exist ".venv" rmdir /s /q ".venv"
"%UV%" venv --python 3.14 .venv
if errorlevel 1 goto fail
"%UV%" pip install --python .venv -r requirements.txt
if errorlevel 1 goto fail
echo Preparing the scanner, one time...
".venv\Scripts\python.exe" -c "from rapidocr import RapidOCR; RapidOCR()" >nul 2>nul
echo Setup complete.

:run
".venv\Scripts\python.exe" tools\asset_test_app.py %*
pause
exit /b 0

:fail
echo.
echo Setup failed. Check the internet connection and double-click this file again.
pause
exit /b 1
