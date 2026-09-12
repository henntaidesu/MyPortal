"""exe 打成 windowed 之后，命令行子命令那一半的补救。

为什么打成 windowed（console=False，见 portal.spec）：双击 exe 不能弹 CMD 黑框。
PyInstaller 的 `hide_console` 在 Win11 上靠不住——默认终端换成 Windows Terminal
以后，`GetConsoleWindow()` 拿到的是个代理窗口，藏了它屏幕上那个黑框照样在，
实测从启动到退出一直挂着。

代价是 GUI 子系统的进程启动时不带控制台，`sys.stdout/stderr` 全是 None，
`Portal.exe users` 这种从 cmd 里调起来的子命令会什么都不打。这里补两件事：

1. `attach_parent()`：沿进程链往上找一个有控制台的祖先，借它的控制台来输出。
   注意 cmd **不等** GUI 子系统的进程，提示符已经还回去了，所以输出会打在新提示符
   后面，看着有点挤——Windows 上所有 GUI 子系统程序都这样，绕不过去。
2. `can_prompt()` / `ask_password()`：借来的控制台**不能读输入**，人和我们会抢同一个
   输入缓冲，敲进去的字符两边各拿一半。所以要口令时改弹一个 Tk 小框，
   见 manage.py 的 `_ask_password`。

非 Windows、非冻结态（源码跑 `python manage.py`）时整个模块都是 no-op，
`can_prompt()` 返回 True，走原来的 getpass。
"""
from __future__ import annotations

import sys

_STD_STREAMS = (
    # 属性名, 控制台设备名, 打开模式
    ('stdout', 'CONOUT$', 'w'),
    ('stderr', 'CONOUT$', 'w'),
    ('stdin', 'CONIN$', 'r'),
)


def attach_parent() -> bool:
    """借一个祖先进程的控制台，把 None 掉的那几个标准流接上去。

    **不能用 AttachConsole(ATTACH_PARENT_PROCESS)**：onefile 打出来的 exe 跑起来是
    两个进程，真正执行代码的是引导器 fork 出来的子进程，它的「父进程」是引导器而不是
    cmd，而引导器自己也是 GUI 子系统、同样没有控制台，于是必然拿到 ERROR_INVALID_HANDLE
    （实测 err=6）。所以得顺着进程链一路往上试，直到碰见那个 cmd。

    返回 True 表示借到了。非 Windows / 非冻结态 / 链上没有任何控制台
    （比如从资源管理器里带参数启动）都返回 False。
    """
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return False
    # 调用方做了重定向（`Portal.exe clients > out.txt`、管道）时句柄是有效的，
    # Python 自己已经把 sys.stdout 建好了，不用也不该去动它
    missing = [name for name, _, _ in _STD_STREAMS if getattr(sys, name, None) is None]
    if 'stdout' not in missing and 'stderr' not in missing:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for pid in _ancestor_pids():
            if kernel32.AttachConsole(pid):
                break
        else:
            return False

        # 用控制台自己的代码页，别去改它（SetConsoleOutputCP 会一直留在人家那个
        # cmd 窗口上）。cmd 默认 936，硬按 UTF-8 写出去中文就是一堆乱码。
        out_cp = kernel32.GetConsoleOutputCP() or 0
        in_cp = kernel32.GetConsoleCP() or 0

        for name, target, mode in _STD_STREAMS:
            if name not in missing:
                continue                    # 这一路被重定向了，保持原样
            cp = in_cp if mode == 'r' else out_cp
            try:
                setattr(sys, name,
                        open(target, mode, encoding=f'cp{cp}' if cp else 'utf-8',
                             errors='replace', buffering=1 if mode == 'w' else -1))
            except Exception:
                pass
        return True
    except Exception:
        return False


def _ancestor_pids() -> list[int]:
    """自己往上的祖先 pid，由近及远。拿不到就返回空表。"""
    import ctypes
    import ctypes.wintypes as wt
    import os

    class _Entry(ctypes.Structure):
        _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD),
                    ('th32ProcessID', wt.DWORD),
                    ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                    ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                    ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long),
                    ('dwFlags', wt.DWORD), ('szExeFile', ctypes.c_char * 260)]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)     # TH32CS_SNAPPROCESS
    if not snapshot:
        return []
    parent_of: dict[int, int] = {}
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        handle = ctypes.c_void_p(snapshot)
        if kernel32.Process32First(handle, ctypes.byref(entry)):
            while True:
                parent_of[entry.th32ProcessID] = entry.th32ParentProcessID
                if not kernel32.Process32Next(handle, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(snapshot))

    chain: list[int] = []
    pid, seen = os.getpid(), set()
    # pid 是会被系统回收复用的，绕成环并非不可能，seen 和上限一起兜住
    while pid in parent_of and pid not in seen and len(chain) < 16:
        seen.add(pid)
        pid = parent_of[pid]
        if pid not in parent_of:
            break
        chain.append(pid)
    return chain


def can_prompt() -> bool:
    """控制台输入靠不靠得住。

    打包成 windowed exe 之后一律算不靠谱：控制台是借来的，cmd 的提示符早还回去了，
    再 getpass 就是和人抢同一个输入缓冲。源码态返回 True，走原来那套 getpass。
    """
    return not (sys.platform == 'win32' and getattr(sys, 'frozen', False))


def ask_password(title: str = '设置口令', hint: str = '') -> str | None:
    """弹个 Tk 小框收口令（两遍一致、至少 6 位）。取消或没有 tkinter 返回 None。

    只在 can_prompt() 为假时用得上。校验规则和 manage.py 里 getpass 那条路一致。
    """
    try:
        import tkinter as tk
    except Exception:
        return None

    result: dict[str, str | None] = {'value': None}

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    try:
        from .tray import icon_path

        path = icon_path('favicon.png')
        if path is not None:
            root._portal_icon = tk.PhotoImage(file=str(path))
            root.iconphoto(True, root._portal_icon)
    except Exception:
        pass

    body = tk.Frame(root, padx=20, pady=16)
    body.pack(fill='both', expand=True)
    if hint:
        tk.Label(body, text=hint, font=('Microsoft YaHei UI', 9)).pack(anchor='w',
                                                                      pady=(0, 10))
    tk.Label(body, text='口令（至少 6 位）', font=('Microsoft YaHei UI', 9)).pack(anchor='w')
    first = tk.Entry(body, show='*', width=30)
    first.pack(anchor='w', pady=(2, 8))
    tk.Label(body, text='再输一次', font=('Microsoft YaHei UI', 9)).pack(anchor='w')
    second = tk.Entry(body, show='*', width=30)
    second.pack(anchor='w', pady=(2, 6))
    error = tk.Label(body, text='', fg='#e5484d', font=('Microsoft YaHei UI', 9))
    error.pack(anchor='w', pady=(0, 8))

    def _ok(_event=None) -> None:
        pw, again = first.get(), second.get()
        if len(pw) < 6:
            error.configure(text='至少 6 位。')
            return
        if pw != again:
            error.configure(text='两次不一致。')
            return
        result['value'] = pw
        root.destroy()

    def _cancel(_event=None) -> None:
        result['value'] = None
        root.destroy()

    buttons = tk.Frame(body)
    buttons.pack(anchor='e')
    tk.Button(buttons, text='确定', width=10, command=_ok).pack(side='left')
    tk.Button(buttons, text='取消', width=10, command=_cancel).pack(side='left', padx=(8, 0))

    root.bind('<Return>', _ok)
    root.bind('<Escape>', _cancel)
    root.protocol('WM_DELETE_WINDOW', _cancel)

    root.update_idletasks()
    screen_x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    screen_y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f'+{max(screen_x, 0)}+{max(screen_y, 0)}')
    first.focus_force()
    root.lift()
    root.attributes('-topmost', True)     # 借来的控制台在前面，不置顶人看不见这个框
    root.after(200, lambda: root.attributes('-topmost', False))
    root.mainloop()
    return result['value']
