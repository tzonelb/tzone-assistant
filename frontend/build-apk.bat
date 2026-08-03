@echo off
REM Build the T-ZONE Android app.
REM Usage: build-apk.bat [API_URL] [release]
REM   build-apk.bat                          -> debug APK, LAN server default
REM   build-apk.bat http://192.168.1.5:8000  -> debug APK, custom server
REM   build-apk.bat https://app.domain.com release -> signed store build (APK + AAB)
REM TEMP is redirected to C:\PROJECTS\tmp because this machine's default
REM TEMP path breaks Java's AF_UNIX sockets (Gradle daemon can't start).

setlocal
set "API_URL=%~1"
set "MODE=%~2"
if "%API_URL%"=="" set "API_URL=http://192.168.10.219:8000"
if /i "%~1"=="release" (
    set "MODE=release"
    set "API_URL=http://192.168.10.219:8000"
)

set "JAVA_HOME=C:\PROJECTS\android-build-tools\jdk-21.0.12+8"
set "ANDROID_HOME=C:\PROJECTS\android-build-tools\sdk"
set "TEMP=C:\PROJECTS\tmp"
set "TMP=C:\PROJECTS\tmp"
if not exist C:\PROJECTS\tmp mkdir C:\PROJECTS\tmp

cd /d "%~dp0"
set "VITE_API_BASE_URL=%API_URL%"
call npm run build || exit /b 1
call npx cap sync android || exit /b 1
pushd "%~dp0android"

if /i "%MODE%"=="release" (
    call .\gradlew.bat assembleRelease bundleRelease --no-daemon || exit /b 1
    popd
    copy /y "%~dp0android\app\build\outputs\apk\release\app-release.apk" "C:\PROJECTS\tzone-assistant\T-ZONE.apk"
    copy /y "%~dp0android\app\build\outputs\bundle\release\app-release.aab" "C:\PROJECTS\tzone-assistant\T-ZONE-playstore.aab"
    echo.
    echo Signed release ready:
    echo   C:\PROJECTS\tzone-assistant\T-ZONE.apk            ^(installable^)
    echo   C:\PROJECTS\tzone-assistant\T-ZONE-playstore.aab  ^(upload to Google Play^)
    echo   API: %API_URL%
) else (
    call .\gradlew.bat assembleDebug --no-daemon || exit /b 1
    popd
    copy /y "%~dp0android\app\build\outputs\apk\debug\app-debug.apk" "C:\PROJECTS\tzone-assistant\T-ZONE.apk"
    echo.
    echo APK ready: C:\PROJECTS\tzone-assistant\T-ZONE.apk  (API: %API_URL%)
)
endlocal
