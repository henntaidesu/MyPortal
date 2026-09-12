@echo off
title HomePage Nav + SSO - http://localhost:9920
cd /d "%~dp0"

rem ============================================================
rem  9920  前端导航主页（vite）
rem  9921  认证中心（FastAPI），前端的 /api 和 /sso 都代理到它
rem ============================================================

where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js not found. Please install it from https://nodejs.org
    pause
    exit /b 1
)

set "PY="
python --version >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
    py --version >nul 2>nul
    if not errorlevel 1 set "PY=py"
)
if not defined PY (
    echo [ERROR] Python 3.10+ not found. Please install it from https://www.python.org
    pause
    exit /b 1
)

rem Port 9920 already taken? Most likely it is already running - just open it.
set "PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP .*:9920 .*LISTENING"') do set "PID=%%p"
if defined PID (
    echo.
    echo   Port 9920 is already in use by PID %PID%.
    echo   Opening http://localhost:9920 in your browser...
    echo.
    echo   If that is NOT this app, free the port with:
    echo       taskkill /PID %PID% /F
    echo.
    start "" http://localhost:9920
    "%SystemRoot%\System32\ping.exe" -n 7 127.0.0.1 >nul
    exit /b 0
)

rem ---------- 前端依赖 ----------
if not exist "webside\node_modules" (
    echo First run - installing frontend dependencies, please wait...
    pushd webside
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed. Check your network and try again.
        popd
        pause
        exit /b 1
    )
    popd
)

rem ---------- 后端依赖 ----------
%PY% -c "import fastapi, uvicorn, httpx" >nul 2>nul
if errorlevel 1 (
    echo First run - installing backend dependencies, please wait...
    %PY% -m pip install -r backend\requirements.txt
    if errorlevel 1 (
        echo [ERROR] pip install failed. Check your network and try again.
        pause
        exit /b 1
    )
)

rem ---------- 第一次要建账号 ----------
if not exist "backend\data\sso.db" (
    echo.
    echo   No account yet. Let's create the first one.
    echo.
    pushd backend
    %PY% manage.py init
    popd
    if not exist "backend\data\sso.db" (
        echo [ERROR] Setup cancelled.
        pause
        exit /b 1
    )
)

rem ---------- 起后端 ----------
echo.
echo   Starting auth server on port 9921...
start "HomePage SSO backend (9921)" /D "%~dp0backend" /MIN %PY% -m app.main
"%SystemRoot%\System32\ping.exe" -n 4 127.0.0.1 >nul

set "BACKPID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP .*:9921 .*LISTENING"') do set "BACKPID=%%p"
if not defined BACKPID (
    echo.
    echo   [WARN] Auth server is not listening on 9921.
    echo          The nav page will open but login will fail.
    echo          Run it by hand to see the error:
    echo              cd backend ^&^& %PY% -m app.main
    echo.
    "%SystemRoot%\System32\ping.exe" -n 4 127.0.0.1 >nul
)

rem ---------- 起前端 ----------
echo.
echo   Home nav:  http://localhost:9920
echo   LAN:       see the Network address printed below
echo   Close this window to stop both servers.
echo.

pushd webside
call npm run dev -- --open
popd

rem ---------- 前端退出后把后端也收掉 ----------
echo.
echo Stopping auth server...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP .*:9921 .*LISTENING"') do taskkill /PID %%p /F >nul 2>nul
echo Server stopped.
pause
