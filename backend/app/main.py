"""门户 Portal：导航接口 + 静态站点。

开发时前端跑 vite(9920)，接口由 vite 代理到这里(9921)。
部署时先 npm run build，这个进程直接把 webside/dist 托出去，同源、不用配 CORS。

没有数据库：导航数据是一个 JSON 文件（app/navstore.py），
图标缓存是一个磁盘目录（app/iconcache.py），登录认的是 conf.json 里那对用户名口令
（app/auth.py，签名 Cookie，服务端不存会话）。
"""
import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import iconcache, navstore, proxy
from .config import AUTH_USERNAME, CONF_PATH, DIST_DIR, HOST, PORT, uses_default_password
from .icon import router as icon_router
from .proxy import router as proxy_router
from .routers.auth import router as auth_router
from .routers.nav import router as nav_router

PURGE_INTERVAL = 3600


async def _purge_loop() -> None:
    while True:
        await asyncio.sleep(PURGE_INTERVAL)
        await asyncio.to_thread(iconcache.purge)   # 过期的图标缓存清一清


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    iconcache.ensure()          # 图标缓存目录，没有就建
    iconcache.purge()
    print(f'[portal] 导航数据 {navstore.describe()}')
    print(f'[portal] 图标缓存 {iconcache.describe()}')
    print(f'[portal] 门户代理 {proxy.describe()}')
    if uses_default_password():
        # 故意每次启动都喊。谁能打开这个门户，谁就能改掉这一页所有人的入口
        print(f'[portal] !! 账号 {AUTH_USERNAME} 还在用默认口令，'
              f'改掉：编辑 {CONF_PATH} 里的 auth.password，然后重启')
    task = asyncio.create_task(_purge_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await proxy.shutdown()      # 转发用的那个连接池


app = FastAPI(title='Portal', version='2.0.0', lifespan=lifespan)

# 被代理页面里的根绝对地址（/static/x.js）靠它转回 /api/proxy/<id>/ 底下，
# 见 app/proxy.py 末尾。中间件比路由先跑，所以这行放哪儿都行，放在这里只是挨着路由
app.add_middleware(proxy.Fallback)

app.include_router(auth_router)
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
