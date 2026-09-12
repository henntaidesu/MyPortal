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
"""
import sys


def main() -> None:
    if len(sys.argv) > 1:
        import manage
        sys.exit(manage.run(sys.argv[1:]))

    from app.main import main as serve
    serve()


if __name__ == '__main__':
    main()
