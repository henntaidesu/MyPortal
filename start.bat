@echo off
title Portal - http://localhost:9920
cd /d "%~dp0"

rem ============================================================
rem  9920  portal frontend (vite)
rem  9921  auth server (FastAPI); the frontend proxies /api and /sso to it
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
%PY% -c "import fastapi, uvicorn, httpx, pymysql" >nul 2>nul
if errorlevel 1 (
    echo First run - installing backend dependencies, please wait...
    %PY% -m pip install -r backend\requirements.txt
    if errorlevel 1 (
        echo [ERROR] pip install failed. Check your network and try again.
        pause
        exit /b 1
    )
)

rem ---------- first run: generate conf.ini, then let the user fill in MySQL ----------
if not exist "backend\conf.ini" (
    echo.
    echo   First run - creating backend\conf.ini ...
    echo.
    pushd backend
    %PY% manage.py init
    popd
    echo.
    echo   [ACTION] Now edit backend\conf.ini, fill in your MySQL settings,
    echo            then run start.bat again. The database and tables are
    echo            created automatically on startup.
    echo.
    pause
    exit /b 1
)

rem ---------- create tables + first account (skipped when they already exist) ----------
pushd backend
%PY% manage.py init
set "INITRC=%ERRORLEVEL%"
popd
if not "%INITRC%"=="0" (
    echo.
    echo   [ERROR] Database is not ready. See the message above,
    echo           then check [database] in backend\conf.ini.
    echo.
    pause
    exit /b 1
)

rem ---------- start the backend ----------
rem  /B keeps it inside THIS console instead of opening a second window:
rem  both servers log here, and closing this window stops both of them.
echo.
echo   Starting auth server on port 9921...
start "" /D "%~dp0backend" /B %PY% -m app.main
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

rem ---------- start the frontend ----------
echo.
echo   Portal:    http://localhost:9920
echo   LAN:       see the Network address printed below
echo   Both servers log into this window - close it to stop both.
echo.

pushd webside
call npm run dev -- --open
popd

rem ---------- frontend exited: shut the backend down too ----------
echo.
echo Stopping auth server...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"TCP .*:9921 .*LISTENING"') do taskkill /PID %%p /F >nul 2>nul
echo Server stopped.
pause
