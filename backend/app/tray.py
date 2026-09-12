"""系统托盘图标（Windows 右下角通知区），只在打包后的 exe 里出现。

菜单：
  - 显示窗口 / 收进托盘：操作 [logwindow.py](logwindow.py) 那个运行窗口
  - 打开门户：浏览器打开本机门户地址
  - 退出程序：让 uvicorn 优雅停机

依赖 pystray + Pillow。这两个**不在** backend/requirements.txt 里——源码态
`python -m app.main` 用不上它们，只有 pyinstaller.bat 打包前会装。所以这里所有
import 都写在函数里，缺了就静默跳过，认证中心照常跑，只是没有托盘。

图标就是门户网页的那个 favicon（webside/public/favicon.png 的指南针），exe 图标、
托盘图标、运行窗口图标三处同一份，见 portal.spec。文件找不到时退回现画一个。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

_PRIMARY = (74, 108, 247, 255)      # --primary，取自 webside/src/styles.css
_QUIT_TIMEOUT = 8                   # 优雅停机等这么久还不退，就强杀

_icon = None                        # 已启动的 pystray.Icon，供 stop / notify 用


def icon_path(name: str = 'favicon.png') -> Path | None:
    """找门户图标文件。规矩和 config.DIST_DIR 一样：exe 同级的 webside 目录优先，
    没有才用打进 exe 的那份——换前端不用重新打包，图标跟着一起换。

    这里**不 import app.config**：运行窗口得在 conf.ini 还没读之前就建起来，
    而 config 一被 import，缺 conf.ini 时就直接 sys.exit 了。
    """
    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(Path(sys.executable).resolve().parent / 'webside' / name)
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidates.append(Path(meipass) / 'webside' / name)
    else:
        # 源码态：app/tray.py → backend/ → 仓库根
        root = Path(__file__).resolve().parents[2]
        candidates.append(root / 'webside' / 'public' / name)
        candidates.append(root / 'webside' / 'dist' / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _make_image():
    """托盘图标。优先读门户的 favicon.png，读不到就现画一个。Pillow 缺失返回 None。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    path = icon_path()
    if path is not None:
        try:
            return Image.open(str(path)).convert('RGBA')
        except Exception:
            pass

    # 兜底：圆角方块底 + 白色指北针，意思和 favicon 一致
    size = 64
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=14, fill=_PRIMARY)
    draw.ellipse((13, 13, size - 14, size - 14), outline=(255, 255, 255, 190), width=2)

    north, east, south, west = (32, 13), (39, 32), (32, 51), (25, 32)
    draw.polygon([north, east, west], fill=(255, 255, 255, 255))   # 指北的那半，实白
    draw.polygon([south, east, west], fill=(255, 255, 255, 110))   # 另一半，半透
    return image


def start(on_quit) -> bool:
    """在后台线程启动托盘图标。

    on_quit: 无参回调，触发优雅退出。
    返回 True 表示起来了；False 表示非 Windows 或 pystray/Pillow 没装（静默跳过）。
    """
    global _icon
    if sys.platform != 'win32':
        return False
    try:
        import pystray
    except Exception:
        return False

    from . import logwindow

    image = _make_image()
    if image is None:
        return False

    def _on_show(icon, item):
        logwindow.show()

    def _on_hide(icon, item):
        logwindow.hide()

    def _on_open(icon, item):
        import webbrowser

        from .config import PORT
        # HOST 常是 0.0.0.0，那是监听地址不是能访问的地址，一律开本机回环
        webbrowser.open(f'http://127.0.0.1:{PORT}/')

    def _on_quit(icon, item):
        stop()
        try:
            on_quit()
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem('显示窗口', _on_show, default=True),
        pystray.MenuItem('收进托盘', _on_hide),
        pystray.MenuItem('打开门户', _on_open),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出程序', _on_quit),
    )
    _icon = pystray.Icon('Portal', image, '门户 Portal', menu)
    _icon.run_detached()        # 自带消息循环，跑在它自己的线程里
    return True


def stop() -> None:
    """摘掉托盘图标。退出前调用，免得进程没了图标还赖在通知区。重复调用安全。"""
    global _icon
    icon, _icon = _icon, None
    if icon is None:
        return
    try:
        icon.visible = False
    except Exception:
        pass
    try:
        icon.stop()
    except Exception:
        pass


def notify(message: str, title: str = '门户 Portal') -> None:
    """弹一条托盘气泡。托盘没起来时静默忽略。"""
    icon = _icon
    if icon is None:
        return
    try:
        icon.notify(message, title)
    except Exception:
        pass


def attach(server) -> bool:
    """把托盘和运行窗口接到 uvicorn 上。非冻结态 / 非 Windows 直接 no-op。

    server 是 uvicorn.Server：退出走 should_exit 优雅停机，而不是当场杀进程——
    在途的 /sso/validate 得让它把票换完，否则业务系统那边会拿到半截失败。
    """
    if not getattr(sys, 'frozen', False) or sys.platform != 'win32':
        return False

    from . import logwindow

    def _quit() -> None:
        stop()                       # 图标先消失：优雅停机可能要几秒，别让人以为点了没反应
        server.should_exit = True

        def _watchdog() -> None:
            # 兜底：优雅停机彻底卡住时（比如某个连接始终不断开）到点强退，
            # 不然托盘图标没了、窗口也关了，进程却还在后台占着 9921 端口
            time.sleep(_QUIT_TIMEOUT)
            os._exit(0)

        threading.Thread(target=_watchdog, daemon=True).start()

    logwindow.set_on_quit(_quit)
    return start(_quit)
