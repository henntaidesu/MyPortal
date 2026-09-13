@echo off
echo ========================================
echo   Portal one-click build (PyInstaller)
echo ========================================

rem ===== Version (edit this on each release) =====
set VERSION=v2.0.0

rem ===== Optional conda env. Leave it empty to use the current python =====
set CONDA_ENV=

set ROOT=%~dp0
cd /d "%ROOT%"
set RELEASE=%ROOT%Releases\%VERSION%

rem ===== Activate conda env if one is configured =====
if not "%CONDA_ENV%"=="" (
    echo.
    echo Activating conda env %CONDA_ENV% ...
    call conda activate %CONDA_ENV%
    if errorlevel 1 (
        echo ERROR: failed to activate conda env %CONDA_ENV%
        pause
        exit /b 1
    )
)

rem ===== 依赖检查 =====
echo.
echo [1/4] Checking build dependencies ...

rem 注意：必须用 "python -m PyInstaller"，不能直接写 "pyinstaller"。
rem 本脚本就叫 pyinstaller.bat，cmd 解析命令时当前目录优先于 PATH，
rem 裸写 pyinstaller 会重新调用这个脚本自己，死循环。
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo pyinstaller not found, installing...
    python -m pip install pyinstaller
    if errorlevel 1 ( echo ERROR: pip install pyinstaller failed & pause & exit /b 1 )
)

rem 后端依赖没装齐的话，PyInstaller 只会打出一个一启动就 ImportError 的 exe
python -c "import fastapi, uvicorn, httpx" >nul 2>&1
if errorlevel 1 (
    echo Backend deps missing, installing from backend\requirements.txt ...
    python -m pip install -r "%ROOT%backend\requirements.txt"
    if errorlevel 1 ( echo ERROR: pip install failed & pause & exit /b 1 )
)

rem ===== Run window (tkinter) + tray icon (pystray/Pillow) =====
rem Deliberately NOT in backend requirements.txt: the server itself does not
rem need them, only the packaged desktop shell does (logwindow.py, tray.py).
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo ERROR: this Python has no tkinter, the run window cannot be bundled.
    echo        Reinstall Python with the tcl/tk option ^(or use a python.org build^), then rerun.
    pause
    exit /b 1
)
python -c "import pystray, PIL" >nul 2>&1
if errorlevel 1 (
    echo Tray deps missing, installing pystray + pillow ...
    python -m pip install pystray pillow
    if errorlevel 1 ( echo ERROR: pip install pystray pillow failed & pause & exit /b 1 )
)

rem ===== 准备发布目录 =====
echo.
echo [2/4] Cleaning and creating release dir %RELEASE% ...
if exist "%RELEASE%" rmdir /s /q "%RELEASE%"
mkdir "%RELEASE%"
if exist "%ROOT%build" rmdir /s /q "%ROOT%build"

rem ===== 构建前端 =====
echo.
echo [3/4] Building frontend webside ...
where npm >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm not found, please install Node.js: https://nodejs.org/
    pause
    exit /b 1
)
pushd "%ROOT%webside"
if not exist "node_modules" (
    echo Installing frontend deps...
    call npm install
    if errorlevel 1 ( echo ERROR: npm install failed & popd & pause & exit /b 1 )
)
call npm run build
if errorlevel 1 ( echo ERROR: frontend build failed & popd & pause & exit /b 1 )
popd
if not exist "%ROOT%webside\dist\index.html" (
    echo ERROR: webside\dist\index.html not found, frontend build may have failed
    pause
    exit /b 1
)

rem ===== 构建 Portal.exe =====
echo.
echo [4/4] Building Portal.exe (windowed; frontend + run window + tray bundled in) ...
python -m PyInstaller --clean --noconfirm "%ROOT%portal.spec" ^
    --distpath "%RELEASE%" --workpath "%ROOT%build"
if errorlevel 1 (
    echo ERROR: Portal.exe build failed
    pause
    exit /b 1
)
if not exist "%RELEASE%\Portal.exe" (
    echo ERROR: Portal.exe not found in %RELEASE%
    pause
    exit /b 1
)

rem ===== Release dir must contain Portal.exe and nothing else =====
rem The integration doc under docs\ is NOT copied here on purpose - send it
rem to the other team straight from the repo.
rem conf.json is not bundled and not copied from this machine either - it holds
rem this install's password and nav data. Portal.exe creates it next to itself
rem on first run and just keeps going; there is nothing to fill in.

rem ===== 清理构建中间产物 =====
if exist "%ROOT%build" rmdir /s /q "%ROOT%build"

echo.
echo ========================================
echo   Build complete! Single file: %RELEASE%\Portal.exe
echo ========================================
dir /b "%RELEASE%"
echo ----------------------------------------
echo   Just double-click Portal.exe. It creates conf.json next to itself - listen
echo   host/port, the login user/password, and later your nav cards, all in there.
echo   Then open http://localhost:9921 and log in as admin / admin.
echo.
echo   [ACTION] Change the password: edit conf.json - auth.password - and restart.
echo            Until you do, every startup prints a warning.
echo.
echo   Backup = copy conf.json. Restore = copy it back.
echo.
echo   Portal.exe is windowed: double-clicking opens a run window with live logs,
echo   no CMD box. Clicking X asks "minimize to tray" or "quit"; the tray icon at
echo   the bottom-right keeps it running in the background and reopens the window.
echo   The frontend is bundled inside Portal.exe. To swap it without rebuilding,
echo   put a "webside" folder (the contents of webside\dist) next to Portal.exe.
echo   Plain HTTP. Put nginx in front for HTTPS, then set cookie_secure = true in
echo   conf.json - turning it on without HTTPS makes the browser drop the cookie,
echo   which looks like "login succeeds then immediately logs out again".
echo ========================================
pause
