# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec —— 门户 Portal（FastAPI + uvicorn，同端口提供接口和前端）。

产物是单文件 Portal.exe，**windowed（console=False）**：双击不弹 CMD 黑框，
日志看运行窗口（app/logwindow.py），点 X 可以收进托盘后台跑（app/tray.py）。

本来更想用 console=True + hide_console='hide-early'。但 Win11 默认终端换成
Windows Terminal 之后 hide_console 是空转的：它靠 ShowWindow(GetConsoleWindow())
藏窗口，而那个句柄是代理窗口，藏了屏幕上的黑框照样在（实测全程挂着）。所以只能 windowed。

三件和路径有关、改之前要想清楚的事（对应 backend/app/config.py 里的 FROZEN 分支）：

1. 前端 webside/dist 打进 exe，运行时解压在 _MEIPASS/webside。
   exe 同级放一个 webside 目录就能盖掉它，换前端不用重新打包。
2. conf.json 不打进来。它必须待在 exe 同级目录：打进去的话每次启动都会被
   临时解压目录里的那份盖掉，改了口令、加了卡片全白改；而且登录口令会跟着 exe
   一起发出去。它同时是用户的全部数据，更不能进 exe。
"""
import os

# 用 SPECPATH（PyInstaller 注入的 spec 文件所在目录）定位项目根，不要用 os.getcwd()——
# 在哪个目录下调起来的就会算到哪儿去，表现成「前端悄悄没打进 exe」
ROOT = os.path.abspath(globals().get('SPECPATH', os.getcwd()))
BACKEND = os.path.join(ROOT, 'backend')

datas = []
binaries = []

# 依赖清一色纯 Python，靠静态分析加 PyInstaller 自带的 hook 就够了。
#
# 特意**不用 collect_all**：它会把包里的可选子模块一并列成隐藏导入——
# fastapi.testclient、starlette.testclient、pydantic.mypy、anyio.pytest_plugin 之类。
# 在 anaconda base 这种什么都装了的环境里，它们会顺藤摸瓜把 pytest / mypy /
# IPython / Jupyter / Qt 全拖进来，最后以一句
# 「attempt to collect multiple Qt bindings packages」直接构建失败。
hiddenimports = [
    # uvicorn[standard] 的这几个实现是运行时按字符串名字导入的，静态分析看不见
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets.auto',
    # 上面那几个 auto 会按「装没装」去探测这两个，探不到就退回纯 Python 实现
    'httptools',
    'websockets',

    # 后端自己的模块。显式列出来而不是 collect_submodules('app')：后者打包时会
    # 真的 import 一遍每个模块，副作用（比如 app.config 生成 conf.json）会落到
    # 构建机器上，而不是跑 exe 的那台。
    'app', 'app.main', 'app.config', 'app.auth', 'app.navstore',
    'app.db', 'app.users', 'app.oidc',       # MySQL 数据层、用户表、单点登录
    'app.cookiejar', 'app.secretbox',        # Cookie 代理：上游登录态加密存库
    'app.icon', 'app.iconcache', 'app.proxy',
    'app.proxyrewrite', 'app.proxyhook',     # 整站代理的正文改写和注入脚本
    'app.routers', 'app.routers.auth', 'app.routers.nav',
    'app.routers.users', 'app.routers.account', 'app.routers.cookies',
    'app.logwindow', 'app.tray',     # 桌面外壳，见下面那段

    # PyMySQL 按 conf.json 里的 charset 在运行时挑编解码器，静态分析看不见。
    # 漏了的表现是「exe 一连库就 LookupError: unknown encoding: utf8mb4」
    'pymysql', 'pymysql.cursors',

    # 各站的特化规则。**新写一个站点文件就要在这儿加一行**——这一串是显式列的
    # （理由见上面那段），漏了的话源码态好好的，exe 里那个站就只剩通用规则：
    # 图和接口在别的域名上的站点会变成「页面打得开，图全裂」。
    'app.proxy_webside',
    'app.proxy_webside._yahoo',
    'app.proxy_webside.github',
    'app.proxy_webside.mercari',
    'app.proxy_webside.paypayfleamarket',
    'app.proxy_webside.yahoo_auctions',
]

# 门户代理（app/proxy.py）转 WebSocket 时用的是 websockets 的**客户端**。
# 光列 'websockets' 不够：这个包的 __init__ 是个 __getattr__ 懒加载壳子，
# websockets.connect 真身在下面这些子模块里，静态分析跟不进去。
# 漏了的话源码态一切正常，只有 exe 里的 WebSocket 代理会挂——而且要到现场
# 打开带 web 终端的页面才发现。
# 两套都探一遍：13 之前只有 legacy，14 之后是 asyncio 那套，装的是哪版就列哪个。
import importlib.util

for _mod in ('websockets.asyncio.client', 'websockets.legacy.client', 'websockets.client'):
    try:
        if importlib.util.find_spec(_mod) is not None:
            hiddenimports.append(_mod)
    except (ImportError, ValueError):
        pass

# 桌面外壳（app/logwindow.py / app/tray.py）的依赖。它们的
# import 全写在函数里——源码态起服务不该为了一个托盘图标去装 pystray——静态分析
# 看不见，得显式列。tkinter 还要靠自带的 hook 把 tcl/tk 那堆运行时文件一起带上。
hiddenimports += [
    'tkinter',
    'pystray',
    'pystray._win32',    # pystray 按平台在运行时选后端，静态分析同样看不见
    'PIL.Image',
    'PIL.ImageDraw',
]

# 前端构建产物整体打入（onefile 运行时解压到 _MEIPASS/webside）
WEBSIDE_DIST = os.path.join(ROOT, 'webside', 'dist')
if os.path.isdir(WEBSIDE_DIST):
    for _dp, _ds, _fs in os.walk(WEBSIDE_DIST):
        _rel = os.path.relpath(_dp, WEBSIDE_DIST)
        _dest = 'webside' if _rel == '.' else os.path.join('webside', _rel)
        for _f in _fs:
            datas.append((os.path.join(_dp, _f), _dest))
    print('[portal.spec] 已打入前端 webside/dist -> webside')
    # favicon.png / favicon.ico 就在 public/ 里，跟着 dist 一起进来了，
    # 托盘和运行窗口运行时从 webside/favicon.png 读，见 app/tray.py 的 icon_path
else:
    # 直接失败，不能只警告：少了前端的 exe 一样能跑起来，只是打开首页是一片
    # 「前端还没打包」，到现场才发现就晚了
    raise SystemExit(f'[portal.spec] 没找到 {WEBSIDE_DIST}，先在 webside 目录执行 npm run build')


# exe 图标用门户网页那个 favicon（指南针），和托盘、运行窗口是同一张图。
# 取 public/ 下的源文件而不是 dist 里的副本：这个不依赖前端有没有构建过。
ICON_ICO = os.path.join(ROOT, 'webside', 'public', 'favicon.ico')
if os.path.isfile(ICON_ICO):
    icon_arg = ICON_ICO
    print(f'[portal.spec] exe 图标 {ICON_ICO}')
else:
    icon_arg = None
    print('[portal.spec] 警告：没找到 webside/public/favicon.ico，exe 用 PyInstaller 默认图标')


a = Analysis(
    [os.path.join(BACKEND, 'main.py')],
    pathex=[BACKEND],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 这些一个都用不上。排掉不只是为了瘦身：在 anaconda base 里打包时，
    # 只要有哪条边不小心连到 PyQt5 和 PySide6 两个 Qt 绑定，PyInstaller 会
    # 直接中止构建（它不支持同时收集两个 Qt 绑定）
    # 注意 tkinter 和 PIL 不在这里：运行窗口用 tkinter，托盘图标用 PIL 现画，
    # 排掉的话构建照样过，跑起来是「双击 exe 只有一个空托盘/干脆没有窗口」
    excludes=[
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'IPython', 'ipykernel', 'jupyter', 'notebook', 'nbformat', 'zmq',
        'numpy', 'pandas', 'scipy', 'matplotlib',
        'pytest', 'mypy', 'sphinx', 'docutils', 'black', 'yapf',
        'jedi', 'parso', 'astroid', 'setuptools', 'pip',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Portal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # 无控制台：双击不弹黑框。别改回 console=True——
    # 上面文档里写了 hide_console 在 Win11 上为什么救不了场。
    console=False,
    icon=icon_arg,      # 门户网页的 favicon，见上面 ICON_ICO
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
