"""认证中心 + 门户静态站点。

开发时前端跑 vite(9920)，接口由 vite 代理到这里(9921)。
部署时先 npm run build，这个进程直接把 webside/dist 托出去，同源、不用配 CORS。
"""
import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import clients, db
from .config import DIST_DIR, HOST, PORT
from .icon import router as icon_router
from .routers.admin import router as admin_router
from .routers.auth import router as auth_router
from .routers.nav import router as nav_router
from .routers.sso import router as sso_router
from .routers.users import router as users_router

PURGE_INTERVAL = 3600


async def _purge_loop() -> None:
    while True:
        await asyncio.sleep(PURGE_INTERVAL)
        await asyncio.to_thread(db.purge_expired)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    db.purge_expired()
    registered = clients.load(force=True)
    print(f'[sso] 数据库 {db.describe()}')
    print(f'[sso] 已注册业务系统 {len(registered)} 个: {", ".join(registered) or "(无)"}')
    if db.uses_default_password():
        print(f'[sso] !! 账号 {db.DEFAULT_ADMIN} 还在用默认口令，'
              f'改掉：python manage.py passwd {db.DEFAULT_ADMIN}')
    task = asyncio.create_task(_purge_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title='Portal SSO', version='1.0.0', lifespan=lifespan)

app.include_router(auth_router)
app.include_router(sso_router)
app.include_router(icon_router)
app.include_router(admin_router)
app.include_router(users_router)
app.include_router(nav_router)


@app.get('/healthz', include_in_schema=False)
def healthz():
    return {'ok': True}


# 静态站点挂在最后：前面的接口路由先匹配，剩下的才交给它
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
    try:
        # 先探一下数据库。不然连不上时 uvicorn 会把 starlette 的 traceback
        # 整页糊在屏幕上，真正有用的那句提示反而被埋了
        db.init_db()
    except db.DatabaseUnavailable as exc:
        raise SystemExit(f'[数据库] {exc}')

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
