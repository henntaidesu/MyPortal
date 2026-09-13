@echo off
title Portal - http://localhost:9920
cd /d "%~dp0"

rem ============================================================
rem  9920  portal frontend (vite)
rem  9921  python backend (FastAPI); the frontend proxies /api to it
rem
rem  No database. One file holds everything: backend\conf.json - listen
rem  host/port, the login user/password, and the nav cards themselves.
rem
rem  ASCII ONLY in this file. cmd.exe parses .bat with the system ANSI codepage
rem  (936 here), so UTF-8 Chinese gets mis-paired byte by byte and part of a
rem  rem-line ends up being executed as a command. Keep comments and echo ASCII.
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

rem ---------- frontend dependencies ----------
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

rem ---------- backend dependencies ----------
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

rem ---------- start the backend ----------
rem  /B keeps it inside THIS console instead of opening a second window:
rem  both servers log here, and closing this window stops both of them.
rem  conf.json is created on first run - nothing to set up beforehand.
echo.
echo   Starting backend on port 9921...
start "" /D "%~dp0backend" /B %PY% -m app.main
"%SystemRoot%\System32\ping.exe" -n 4 127.0.0.1 >nul

set "BACKPID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP .*:9921 .*LISTENING"') do set "BACKPID=%%p"
if not defined BACKPID (
    echo.
    echo   [WARN] Backend is not listening on 9921.
    echo          The nav page will open but it cannot save.
    echo          Run it by hand to see the error:
    echo              cd backend ^&^& %PY% -m app.main
    echo.
    "%SystemRoot%\System32\ping.exe" -n 4 127.0.0.1 >nul
)

rem ---------- start the frontend ----------
echo.
echo   Portal:    http://localhost:9920
echo   LAN:       see the Network address printed below
echo   Login:     admin / admin on first run - see backend\conf.json
echo   All data:  backend\conf.json
echo   Both servers log into this window - close it to stop both.
echo.

pushd webside
call npm run dev -- --open
popd

rem ---------- frontend exited: shut the backend down too ----------
echo.
echo Stopping backend...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP .*:9921 .*LISTENING"') do taskkill /PID %%p /F >nul 2>nul
echo Server stopped.
pause
