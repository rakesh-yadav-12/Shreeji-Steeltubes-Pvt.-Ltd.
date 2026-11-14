@echo off
REM VS Code ke terminal me:  push.bat "kya badla"   (message optional)
REM Ye sab changes commit karke GitHub par push karta hai -> Render live site khud update kar deta hai.
cd /d "%~dp0"
set MSG=%~1
if "%MSG%"=="" set MSG=update
git add -A
git commit -m "%MSG%"
git pull --rebase origin main
git push origin main
echo.
echo Push ho gaya. Render ab deploy kar raha hai (1-3 min). Status: Render Dashboard ^> Events/Logs
pause
