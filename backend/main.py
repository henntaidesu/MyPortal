#!/usr/bin/env python
"""打包入口：PyInstaller 从这里进来，开发时用不着它。

    Portal.exe                起认证中心（等同 python -m app.main）
    Portal.exe init           建库 + 建第一个账号
    Portal.exe adduser 张三   其余子命令和 manage.py 完全一样

合成一个 exe 而不是打两个：两个 exe 要多背一份几十 MB 的 Python 运行时，
而且拷贝的人十有八九只拷走其中一个，到了现场就建不了账号。

import 都写在函数里：起服务不必把 manage 拖进来，走子命令也不必把 uvicorn 拖进来。

注意 `import manage` 会连带导入 app.config，而它读不到 conf.ini 就会先生成一份模板
再 sys.exit。所以在还没配过的机器上，`Portal.exe --help` 也会先走这一步——
和源码态 `python manage.py --help` 的表现一致，不是打包引入的毛病。

## 两副面孔：窗口 和 命令行

exe 打成 windowed（console=False），双击不弹 CMD 黑框。于是两条路各走各的：

  无参（起服务）    → 弹运行窗口（[app/logwindow.py](app/logwindow.py)）显示日志，
                      点 X 可以收进托盘接着在后台跑（[app/tray.py](app/tray.py)）
  带参（子命令）    → 借调用方那个 cmd 的控制台来输出，要口令时弹小框收，
                      见 [app/winconsole.py](app/winconsole.py) 开头那段说明

本来更想用「console=True + PyInstaller 的 hide_console」——那样命令行这半边一点
不用改。但 Win11 默认终端换成 Windows Terminal 之后 hide_console 是空转的：
它靠 ShowWindow(GetConsoleWindow()) 藏窗口，而那个句柄是个代理窗口，藏了屏幕上的
黑框照样在。实测从启动到退出全程挂着，所以只能走 windowed。
"""
import sys


def _start_window() -> bool:
    """无参启动（起服务）时把日志接进运行窗口。必须在 import app.config 之前调用。

    conf.ini 不存在时 app.config 会直接 sys.exit，那句「去填数据库密码」得能落进
    窗口里——windowed 打包后根本没有控制台，双击的人在别处什么也看不到。
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
    # 带参数 = 子命令：先把调用方的控制台借过来，不然 windowed 打包后
    # stdout 是 None，`Portal.exe users` 从头到尾一个字都不打
    if len(sys.argv) > 1:
        from app.winconsole import attach_parent
        attach_parent()
        import manage
        sys.exit(manage.run(sys.argv[1:]))

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
