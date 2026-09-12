"""打包后的运行窗口：双击 exe 看日志的地方，点 X 问「退出程序」还是「收进托盘」。

为什么需要它：exe 是 windowed 打的（console=False，双击不弹 CMD 黑框），
没有控制台就没地方看 uvicorn 的日志，这个窗口顶上——stdout/stderr 都被接进来。
命令行子命令那半边怎么办见 [winconsole.py](winconsole.py)。

Tk 只能在创建它的那个线程里调用，所以窗口跑在独立线程，别的线程一律通过队列投
日志和命令。tkinter 缺失或非 Windows 时 start() 返回 False，服务照常跑，只是没窗口。

配套的托盘图标在 [tray.py](tray.py)，退出回调也是那边装上来的。
"""
from __future__ import annotations

import queue
import re
import sys
import threading

_TITLE = '门户 Portal'
_MAX_LINES = 5000        # 文本框最多留多少行，超了从头截
_QUEUE_MAX = 5000        # 窗口还没起来时的日志积压上限，超了直接丢
_DRAIN_CHUNKS = 400      # 一次刷新最多合并多少块，避免刷屏把界面卡住
_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')   # uvicorn 认得控制台就上色

_logs: queue.Queue[str] = queue.Queue(maxsize=_QUEUE_MAX)
_cmds: queue.Queue[str] = queue.Queue(maxsize=64)

_started = False
_fatal = False           # 启动失败模式：X 直接关掉走人，不再问「收进托盘」
_on_quit = None

_ready = threading.Event()    # 窗口已建好
_closed = threading.Event()   # 窗口已关掉（mainloop 退出）

_root = None
_text = None


class _Tee:
    """把写入送进运行窗口，顺手也写一份给原始流。

    **base 经常是 None**：exe 是 windowed 打的，没有控制台，PyInstaller 把
    sys.stdout / sys.stderr 直接设成 None。所以这个类必须在没有底流的情况下
    照样是个合法的文件对象——uvicorn 配日志时会去碰 isatty()，logging 的
    StreamHandler 会调 write/flush，谁抛异常谁就把服务带崩。
    """

    def __init__(self, base):
        self._base = base

    def write(self, s):
        base = self.__dict__.get('_base')
        if base is not None:
            try:
                base.write(s)
            except Exception:
                pass
        if s:
            try:
                _logs.put_nowait(s)
            except queue.Full:
                pass
        return len(s) if s else 0

    def flush(self):
        base = self.__dict__.get('_base')
        if base is None:
            return
        try:
            base.flush()
        except Exception:
            pass

    def isatty(self):
        # 没有底流就老实说不是 tty：uvicorn 据此不上色，窗口里的日志更干净
        base = self.__dict__.get('_base')
        try:
            return bool(base) and base.isatty()
        except Exception:
            return False

    def fileno(self):
        base = self.__dict__.get('_base')
        if base is None:
            raise OSError('windowed 打包，没有控制台可用的文件描述符')
        return base.fileno()

    @property
    def encoding(self):
        return getattr(self.__dict__.get('_base'), 'encoding', 'utf-8')

    def __getattr__(self, name):
        base = self.__dict__.get('_base')
        if base is None:
            raise AttributeError(name)
        return getattr(base, name)


def _install_tee() -> None:
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if isinstance(stream, _Tee):
            continue
        try:
            setattr(sys, name, _Tee(stream))   # stream 可能是 None，_Tee 认这个
        except Exception:
            pass


def _emit(text: str) -> None:
    """直接往窗口里写一行（不经过 stdout），给 hold() 用。"""
    try:
        _logs.put_nowait(text)
    except queue.Full:
        pass


def start() -> bool:
    """接管 stdout/stderr 并在后台线程建窗口。非 Windows / 没有 tkinter 返回 False。

    必须在 import app.config 之前调用：conf.ini 不存在时 config 会直接 sys.exit，
    那句提示得能落进窗口里，不然双击的人只看到「闪一下什么都没有」。
    """
    global _started
    if _started or sys.platform != 'win32':
        return False
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    _started = True
    _install_tee()
    threading.Thread(target=_ui_main, name='portal-log-window', daemon=True).start()
    return True


def set_on_quit(callback) -> None:
    """装上「退出程序」回调，一般是 tray.attach 里那个设 server.should_exit 的。"""
    global _on_quit
    _on_quit = callback


def is_alive() -> bool:
    return _ready.is_set() and not _closed.is_set()


def show() -> bool:
    """显示并前置窗口。窗口不可用时返回 False。"""
    return _post_cmd('show')


def hide() -> bool:
    """把窗口收回托盘。"""
    return _post_cmd('hide')


def hold(message: str = '') -> bool:
    """启动失败时把窗口留在屏幕上，阻塞到人把它关掉为止。

    没有这一步，报错会连同窗口一起在零点几秒内消失：控制台已经被 hide-early 藏了，
    那边同样看不到，双击 exe 的人得到的信息量是零。
    """
    global _fatal
    if not _started or not _ready.wait(3):
        return False
    _fatal = True
    if message:
        _emit(message if message.endswith('\n') else message + '\n')
    _emit('\n—— 启动失败，程序没有起来。关掉这个窗口即可退出。 ——\n')
    _post_cmd('fatal')
    _closed.wait()
    return True


def _post_cmd(cmd: str) -> bool:
    if not is_alive():
        return False
    try:
        _cmds.put_nowait(cmd)
    except queue.Full:
        return False
    return True


# ---------------------------------------------------------------- UI 线程内部


def _apply_icon(root) -> None:
    """窗口和任务栏用门户网页的那个 favicon。找不到就用 Tk 默认图标，不影响使用。"""
    try:
        import tkinter as tk

        from .tray import icon_path

        path = icon_path('favicon.png')          # Tk 8.6 的 PhotoImage 认 PNG
        if path is None:
            return
        root._portal_icon = tk.PhotoImage(file=str(path))   # 得留引用，否则被 GC 掉
        root.iconphoto(True, root._portal_icon)
    except Exception:
        pass


def _ui_main() -> None:
    global _root, _text
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title(_TITLE)
        root.geometry('900x560')
        root.minsize(520, 300)
        _apply_icon(root)

        text = tk.Text(
            root,
            wrap='none',
            state='disabled',
            bg='#11141a',        # 和前端深色主题的 --bg 同一套配色
            fg='#e8ebf2',
            insertbackground='#e8ebf2',
            selectbackground='#4a6cf7',
            font=('Consolas', 10),
            borderwidth=0,
            highlightthickness=0,
        )
        yscroll = tk.Scrollbar(root, orient='vertical', command=text.yview)
        xscroll = tk.Scrollbar(root, orient='horizontal', command=text.xview)
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        text.grid(row=0, column=0, sticky='nsew')
        yscroll.grid(row=0, column=1, sticky='ns')
        xscroll.grid(row=1, column=0, sticky='ew')

        root.protocol('WM_DELETE_WINDOW', _on_close_clicked)

        _root, _text = root, text
        _ready.set()
        root.after(80, _pump)
        root.mainloop()
    except Exception:
        pass
    finally:
        _root = None
        _text = None
        _ready.set()      # 建窗口就失败时也要放行 hold() 里的等待
        _closed.set()


def _pump() -> None:
    """UI 线程定时任务：先处理投进来的命令，再把积压日志刷进文本框。"""
    root, text = _root, _text
    if root is None or text is None:
        return
    try:
        while True:
            cmd = _cmds.get_nowait()
            if cmd in ('show', 'fatal'):
                _do_show(root)
            elif cmd == 'hide':
                root.withdraw()
    except queue.Empty:
        pass

    chunks = []
    try:
        for _ in range(_DRAIN_CHUNKS):
            chunks.append(_logs.get_nowait())
    except queue.Empty:
        pass
    if chunks:
        _append(text, _ANSI_RE.sub('', ''.join(chunks)))

    root.after(80, _pump)


def _do_show(root) -> None:
    try:
        root.deiconify()
        root.lift()
        root.focus_force()
    except Exception:
        pass


def _append(text, content: str) -> None:
    at_bottom = text.yview()[1] >= 0.999      # 人往上翻着看时别硬拽回底部
    text.configure(state='normal')
    text.insert('end', content)
    total = int(text.index('end-1c').split('.')[0])
    if total > _MAX_LINES:
        text.delete('1.0', f'{total - _MAX_LINES + 1}.0')
    text.configure(state='disabled')
    if at_bottom:
        text.see('end')


def _on_close_clicked() -> None:
    root = _root
    if root is None:
        return
    if _fatal:
        # 服务根本没起来，没有「留在后台」这回事，X 就是退出
        try:
            root.destroy()
        except Exception:
            pass
        return
    choice = _ask_close_action(root)
    if choice == 'tray':
        root.withdraw()
        try:
            from .tray import notify
            notify('门户还在后台运行。点右下角托盘图标可以重新打开这个窗口。')
        except Exception:
            pass
    elif choice == 'exit':
        root.withdraw()
        callback = _on_quit
        if callback is None:
            import os
            os._exit(0)
        try:
            callback()
        except Exception:
            import os
            os._exit(0)


def _ask_close_action(root) -> str:
    """模态询问框，返回 'exit' / 'tray' / 'cancel'。"""
    import tkinter as tk

    result = {'value': 'cancel'}
    dlg = tk.Toplevel(root)
    dlg.title('关闭门户 Portal')
    dlg.resizable(False, False)
    dlg.transient(root)

    def _choose(value: str) -> None:
        result['value'] = value
        try:
            dlg.destroy()
        except Exception:
            pass

    body = tk.Frame(dlg, padx=20, pady=16)
    body.pack(fill='both', expand=True)
    tk.Label(body, text='要关掉门户吗？',
             font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')
    tk.Label(
        body,
        text='收进托盘：窗口隐藏，认证中心继续在后台跑，业务系统的单点登录不受影响。\n'
             '退出程序：停掉认证中心，已接入的系统会登不进去。',
        justify='left',
        font=('Microsoft YaHei UI', 9),
    ).pack(anchor='w', pady=(8, 16))

    buttons = tk.Frame(body)
    buttons.pack(anchor='e')
    tk.Button(buttons, text='收进托盘', width=12,
              command=lambda: _choose('tray')).pack(side='left')
    tk.Button(buttons, text='退出程序', width=12,
              command=lambda: _choose('exit')).pack(side='left', padx=8)
    tk.Button(buttons, text='取消', width=8,
              command=lambda: _choose('cancel')).pack(side='left')

    dlg.protocol('WM_DELETE_WINDOW', lambda: _choose('cancel'))
    dlg.bind('<Escape>', lambda _e: _choose('cancel'))

    dlg.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() - dlg.winfo_width()) // 2
    y = root.winfo_rooty() + (root.winfo_height() - dlg.winfo_height()) // 3
    dlg.geometry(f'+{max(x, 0)}+{max(y, 0)}')

    dlg.grab_set()
    root.wait_window(dlg)
    return result['value']
