@echo off
echo ========================================
echo   Portal one-click build (PyInstaller)
echo ========================================

rem ===== Version (edit this on each release) =====
set VERSION=v1.0.0

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
python -c "import fastapi, uvicorn, httpx, pymysql" >nul 2>&1
if errorlevel 1 (
    echo Backend deps missing, installing from backend\requirements.txt ...
    python -m pip install -r "%ROOT%backend\requirements.txt"
    if errorlevel 1 ( echo ERROR: pip install failed & pause & exit /b 1 )
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
echo [4/4] Building Portal.exe (console; frontend bundled in) ...
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
rem conf.ini is not bundled and not copied from this machine either (the local
rem one holds a real database password). Portal.exe writes a template next to
rem itself on first run, then stops so somebody can fill it in.

rem ===== 清理构建中间产物 =====
if exist "%ROOT%build" rmdir /s /q "%ROOT%build"

echo.
echo ========================================
echo   Build complete! Single file: %RELEASE%\Portal.exe
echo ========================================
dir /b "%RELEASE%"
echo ----------------------------------------
echo   1. Run Portal.exe once - it creates conf.ini next to itself and stops.
echo   2. Fill in your MySQL host/user/password in conf.ini (the database and
echo      tables are created automatically, no need to create them by hand).
echo   3. Portal.exe init          - create the database and the first account
echo   4. Portal.exe               - start the auth server, open http://localhost:9921
echo.
echo   Account / client management uses the same exe:
echo      Portal.exe users / adduser / passwd / clients / addclient / settings
echo.
echo   The frontend is bundled inside Portal.exe. To swap it without rebuilding,
echo   put a "webside" folder (the contents of webside\dist) next to Portal.exe.
echo   Plain HTTP - put nginx in front for HTTPS, then: Portal.exe set cookie_secure true
echo ========================================
pause
