@echo off
setlocal
chcp 65001 >nul
python scripts\build_release.py %*
if errorlevel 1 (
    echo.
    echo [ERROR] Release build failed.
    pause
    exit /b 1
)
echo.
echo Release package created in the release directory.
