@echo off
echo ============================================================
echo   Nuitka Standalone Build - CourseEnroll v3.2
echo ============================================================
echo.

rem Card signing keys are never embedded. The first terminal run creates
rem card_signing_private.pem and card_signing_public.pem beside the executable.
python -m nuitka ^
    --standalone ^
    --output-dir=build ^
    --output-filename=CourseEnroll.exe ^
    --include-data-dir=static_dist=static_dist ^
    --nofollow-import-to=paddleocr ^
    --nofollow-import-to=paddle ^
    --nofollow-import-to=paddlepaddle ^
    --nofollow-import-to=torch ^
    --nofollow-import-to=torchvision ^
    --nofollow-import-to=skimage ^
    --nofollow-import-to=scipy ^
    --nofollow-import-to=pandas ^
    --include-package=Crypto ^
    --include-package=cv2 ^
    --include-package=ddddocr ^
    --include-package-data=ddddocr ^
    --include-package=onnxruntime ^
    --no-deployment-flag=excluded-module-usage ^
    --enable-plugin=no-qt ^
    --windows-console-mode=force ^
    --assume-yes-for-downloads ^
    main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Build complete
echo ============================================================
echo.

if exist "build\CourseEnroll" rmdir /s /q "build\CourseEnroll"
rename "build\main.dist" "CourseEnroll"

echo   Output: build\CourseEnroll\CourseEnroll.exe
echo.
pause
