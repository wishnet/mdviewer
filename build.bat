@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing dependencies...
pip install pyinstaller pywebview --quiet
pip install markdown pymdown-extensions Pygments --quiet 2>nul
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q "*.spec" 2>nul
echo Building...
pyinstaller --onefile --noconsole --icon=icon.ico --hidden-import webview --hidden-import webview.platforms.edgechromium --hidden-import json --hidden-import threading --collect-all webview --name MDViewer mdviewer.py
if errorlevel 1 (echo Build failed && pause && exit /b 1)
echo.
echo Done! Output: dist\MDViewer.exe
pause
