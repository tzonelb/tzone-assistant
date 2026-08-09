@echo off
REM Starts the WhatsApp Web bridge (QR pairing — no Meta developer account).
REM First run: cd channels\whatsapp_qr\bridge && npm install
cd /d "%~dp0channels\whatsapp_qr\bridge"
node index.js
