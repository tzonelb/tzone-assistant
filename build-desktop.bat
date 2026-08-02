@echo off
REM Builds the T-ZONE Windows desktop app installer.
REM Output: desktop\release\T-ZONE Setup <version>.exe

echo === 1/3 Building frontend ===
cd /d "%~dp0frontend"
call npm run build
if errorlevel 1 goto :fail

echo === 2/3 Copying frontend into desktop app ===
cd /d "%~dp0desktop"
if exist dist rmdir /s /q dist
xcopy /e /i /q "%~dp0frontend\dist" dist >nul
if errorlevel 1 goto :fail

echo === 3/3 Packaging Windows installer ===
if not exist node_modules call npm install
call npm run dist
if errorlevel 1 goto :fail

echo.
echo Done! Installer is in: %~dp0desktop\release\
exit /b 0

:fail
echo.
echo BUILD FAILED — see the error above.
exit /b 1
