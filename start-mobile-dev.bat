@echo off
rem T-ZONE — start backend (reachable from phone) + Expo dev server for the mobile app.
rem The phone must be on the SAME Wi-Fi network as this PC.
cd /d "%~dp0"

echo Starting T-ZONE backend on 0.0.0.0:8000 (phone-reachable)...
start "T-ZONE Backend" cmd /k ".venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000"

echo Starting Expo dev server for the mobile app...
start "T-ZONE Mobile (Expo)" cmd /k "cd mobile && npx expo start"

echo.
echo Two windows opened: backend + Expo.
echo On your iPhone: install "Expo Go" from the App Store, then scan the QR code
echo shown in the Expo window with the iPhone Camera app.
echo If Windows Firewall asks for permission, click "Allow".
