@echo off
REM VS Code terminal:  push.bat "kya badla"   (message optional)
REM Commit + push karta hai aur ASLI result dikhata hai (galat "success" nahi).
cd /d "%~dp0"
set MSG=%~1
if "%MSG%"=="" set MSG=update

git add -A
git diff --cached --quiet
if errorlevel 1 goto :commit
echo.
echo [INFO] Koi NAYA change nahi mila (file save nahi hui ya doosre folder me edit hui?). Sirf pending push check kar raha hoon.
goto :pull

:commit
git commit -m "%MSG%"
if errorlevel 1 goto :fail

:pull
git pull --rebase origin main
if errorlevel 1 goto :fail
git push origin main
if errorlevel 1 goto :fail

for /f %%i in ('git rev-parse HEAD') do set LOCAL=%%i
for /f %%i in ('git rev-parse origin/main') do set REMOTE=%%i
echo.
git log -1 --format="Last commit: %%h  %%ad  %%s" --date=format:"%%d-%%m %%H:%%M"
if not "%LOCAL%"=="%REMOTE%" goto :fail
echo.
echo ===== OK: GitHub par ye commit pahunch gaya. Render ab deploy karega (1-3 min). =====
echo Render Dashboard ^> Events me "Deploy live" aane tak ruko, phir Ctrl+Shift+R karo.
pause
exit /b 0

:fail
echo.
echo ===== FAIL: push nahi hua! Upar ka error padho (conflict / login / internet). =====
pause
exit /b 1
