#!/usr/bin/env python
"""打包入口：PyInstaller 从这里进来，开发时用不着它（开发时是 `python -m app.main`）。

exe 没有子命令：门户的配置和数据全在一个 conf.json 里，要改直接用记事本，
不需要一套命令行来伺候。双击 Portal.exe 就是起服务。

## 为什么打成 windowed

exe 打成 windowed（console=False），双击不弹 CMD 黑框，起来的是一个运行窗口
（[app/logwindow.py](app/logwindow.py)）实时显示日志，点 X 可以收进托盘接着在后台跑
（[app/tray.py](app/tray.py)）。

本来更想用「console=True + PyInstaller 的 hide_console」，但 Win11 默认终端换成
Windows Terminal 之后 hide_console 是空转的：它靠 ShowWindow(GetConsoleWindow())
藏窗口，而那个句柄是个代理窗口，藏了屏幕上的黑框照样在。实测从启动到退出全程挂着，
所以只能走 windowed。

import 都写在函数里：窗口那套得在 import app.config 之前就建起来，
不然启动失败的提示会落在一个还不存在的窗口里。
"""
import sys


def _start_window() -> bool:
    """把日志接进运行窗口。必须在 import app.config 之前调用。

    conf.json 读不出来时 app.config 会直接 sys.exit，那句提示得能落进窗口里——
    windowed 打包后根本没有控制台，双击的人在别处什么也看不到。
    """
    if not getattr(sys, 'frozen', False) or sys.platform != 'win32':
        return False
    try:
        from app.logwindow import start
        return start()
    except Exception:
        return False


def _hold_on_failure(message: str = '') -> None:
    """启动失败时把窗口留在屏幕上，等人看完再关。窗口不可用时什么也不做。"""
    try:
        from app import logwindow
        logwindow.hold(message)
    except Exception:
        pass


def main() -> None:
    windowed = _start_window()
    try:
        from app.main import main as serve
        serve()
    except SystemExit as exc:
        # sys.exit('一句话') 的那句提示是解释器在退出时才打的，那会儿窗口已经
        # 跟着进程没了。所以这里自己先打一遍，再把窗口按住。
        code = exc.code
        if windowed and code not in (0, None):
            _hold_on_failure(str(code) if isinstance(code, str) else '')
        raise
    except BaseException:
        if windowed:
            import traceback
            traceback.print_exc()
            _hold_on_failure()
        raise


if __name__ == '__main__':
    main()
