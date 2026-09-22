"""门户 Portal：多用户导航接口 + 静态站点。

开发时前端跑 vite(9920)，接口由 vite 代理到这里(9921)。
部署时先 npm run build，这个进程直接把 webside/dist 托出去，同源、不用配 CORS。

数据在 MySQL 里（app/db.py）：用户、每个人自己的导航、OIDC 绑定关系。
conf.json 只剩配置（app/config.py）。图标缓存仍然是一个磁盘目录
（app/iconcache.py），随时可以整个删掉。

登录两条路：本地账号（口令哈希在 users 表里）和 OIDC 单点登录（app/oidc.py）。
会话是一枚签名 Cookie，服务端什么都不存（app/auth.py）。
"""
import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import cookiejar, db, iconcache, navstore, oidc, proxy, users
from .config import CONF_PATH, DIST_DIR, HOST, PORT, uses_default_password
from .icon import router as icon_router
from .proxy import router as proxy_router
from .routers.account import router as account_router
from .routers.auth import router as auth_router
from .routers.cookies import router as cookies_router
from .routers.nav import router as nav_router
from .routers.users import router as users_router

PURGE_INTERVAL = 3600


async def _purge_loop() -> None:
    while True:
        await asyncio.sleep(PURGE_INTERVAL)
        await asyncio.to_thread(iconcache.purge)   # 过期的图标缓存清一清
        # 过期的、以及太久没动过的上游 Cookie。放在同一趟里，不另起一个任务：
        # 两件事都是「每小时扫一遍、慢一点也无所谓」
        try:
            await asyncio.to_thread(cookiejar.purge)
        except Exception as exc:                   # noqa: BLE001
            print(f'[Cookie 代理] 清理过期数据失败，下一轮再试: {exc}')


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 数据库要第一个起来：后面每一句都要用它，连不上就 SystemExit，
    # 带着一个连不上的库把服务起起来只会让每个接口都 500
    db.init()
    first = users.ensure_bootstrap()
    if first is not None:
        # 第一个管理员刚建出来，老版本 conf.json 里那段导航就搬到他名下
        navstore.import_legacy(int(first['id']))

    iconcache.ensure()          # 图标缓存目录，没有就建
    iconcache.purge()
    print(f'[portal] 数据库 {db.describe()}')
    print(f'[portal] 用户 {users.describe()}')
    print(f'[portal] 导航 {navstore.describe()}')
    print(f'[portal] 单点登录 {oidc.describe()}')
    print(f'[portal] 图标缓存 {iconcache.describe()}')
    print(f'[portal] 门户代理 {proxy.describe()}')
    print(f'[portal] Cookie 代理 {cookiejar.describe()}')

    if users.count() == 0:
        # 一个用户都没有 = 谁都登不进来。不停下来是故意的：停了就连改配置的机会都没有
        print(f'[portal] !! 库里一个用户都没有，现在谁都登不进来。'
              f'在 {CONF_PATH} 的 auth.bootstrap_admin 里填一对用户名口令再重启')
    elif uses_default_password():
        # 故意每次启动都喊。谁能打开这个门户，谁就能改掉所有人的账号
        print(f'[portal] !! conf.json 里第一个管理员还配着默认口令 admin/admin，'
              f'登进去之后在「账号」里改掉，然后把 {CONF_PATH} 的 bootstrap_admin 删了')

    task = asyncio.create_task(_purge_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await proxy.shutdown()      # 转发用的那个连接池
        db.close()


app = FastAPI(title='Portal', version='3.0.0', lifespan=lifespan)

# 被代理页面里的根绝对地址（/static/x.js）靠它转回 /api/proxy/<id>/ 底下，
# 见 app/proxy.py 末尾。中间件比路由先跑，所以这行放哪儿都行，放在这里只是挨着路由
app.add_middleware(proxy.Fallback)

app.include_router(auth_router)
app.include_router(account_router)
app.include_router(cookies_router)
app.include_router(users_router)
app.include_router(icon_router)
app.include_router(nav_router)
app.include_router(proxy_router)


@app.get('/healthz', include_in_schema=False)
def healthz():
    return {'ok': True}


# 静态站点挂在最后：前面的接口路由先匹配，剩下的才交给它。
# 新增路由必须在这行 mount 之前 include，而且前缀要落在 /api 下，否则 dev 时 vite 代理不到。
if DIST_DIR.is_dir():
    app.mount('/', StaticFiles(directory=DIST_DIR, html=True), name='portal')
else:
    @app.get('/', include_in_schema=False)
    def _no_dist():
        return HTMLResponse(
            '<!doctype html><meta charset="utf-8">'
            '<h2>前端还没打包</h2>'
            f'<p>没找到 <code>{DIST_DIR}</code>。</p>'
            '<p>开发时请访问 <a href="http://localhost:9920">http://localhost:9920</a>；'
            '部署前先在 webside 目录执行 <code>npm run build</code>。</p>',
            status_code=200)


def main() -> None:
    import uvicorn

    # 不用 uvicorn.run()：托盘的「退出程序」要拿到 Server 对象才能设 should_exit。
    # timeout_graceful_shutdown 是兜底上限，免得哪个连接一直不断开就永远停不下来。
    #
    # **只能单进程**（没有 workers=N）：登录限流、导航改动计数、代理的目标表缓存
    # 都是进程内的内存，多 worker 会让它们各说各话。真要横着扩，前面放反代、
    # 后面起多个进程也能跑（会话是签名 Cookie，密钥在库里，共享得了），
    # 只是限流会被摊薄、缓存会多 5 秒延迟。
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level='info',
                            timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    # 打包成 exe 且在 Windows 上时挂托盘图标和运行窗口，其余情况整段是 no-op；
    # pystray/Pillow 没装也只是没托盘，不能因此起不来服务
    with contextlib.suppress(Exception):
        from .tray import attach
        attach(server)
    server.run()


if __name__ == '__main__':
    main()
