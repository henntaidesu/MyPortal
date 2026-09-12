"""业务系统接入示例（Python / FastAPI）。

跑起来：
    pip install fastapi uvicorn httpx
    python examples/python-demo/app.py
然后在 server 目录注册它：
    python manage.py addclient demo-py --name "Python 示例" \
        --redirect-uri http://127.0.0.1:8801/sso/callback \
        --logout-uri  http://127.0.0.1:8801/sso/logout-notify \
        --home-url    http://127.0.0.1:8801/
把上面打印出来的 client_secret 填到下面 CLIENT_SECRET。

要接到你自己的系统里，真正要写的只有三段：
    1. 没登录 -> 302 去 SSO_BASE/sso/authorize
    2. /sso/callback 拿 ticket -> 后端 POST /sso/validate 换身份 -> 建自己的会话
    3. /sso/logout-notify 收到通知 -> 销毁对应的本地会话
"""
import hmac
import os
import secrets
from urllib.parse import urlencode

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

# ------------------------------------------------------------------ 配置
SSO_BASE = os.environ.get('SSO_BASE', 'http://127.0.0.1:9920')   # 主页/认证中心
SELF_BASE = os.environ.get('SELF_BASE', 'http://127.0.0.1:8801')  # 本系统
CLIENT_ID = os.environ.get('CLIENT_ID', 'demo-py')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET', '把 addclient 打印的 secret 填这里')

COOKIE = 'demo_py_session'
CALLBACK = SELF_BASE + '/sso/callback'

# 本地会话。真实项目换成 Redis 或数据库；记得存 sso_sid，单点登出要用它对号入座
SESSIONS: dict[str, dict] = {}

app = FastAPI(title='SSO 接入示例 - Python')


def current_user(request: Request) -> dict | None:
    session = SESSIONS.get(request.cookies.get(COOKIE, ''))
    return session['user'] if session else None


# ------------------------------------------------------------------ 1. 没登录就去要票

@app.get('/')
def home(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse('/sso/login', status_code=302)
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8">
<title>Python 示例系统</title>
<style>body{{font:15px/1.8 system-ui,"Microsoft YaHei",sans-serif;padding:48px;max-width:680px}}
code{{background:#f2f4f7;padding:2px 6px;border-radius:4px}}</style>
<h2>这里是「Python 示例系统」</h2>
<p>当前用户：<b>{user['display_name']}</b>（{user['username']}）</p>
<p>角色：<code>{', '.join(user['roles']) or '无'}</code></p>
<p>这个页面没让你输过一次账号密码 —— 身份是从主页的会话换过来的。</p>
<p><a href="/logout">退出本系统</a> ｜ <a href="/logout-all">退出所有系统</a></p>""")


@app.get('/sso/login')
def sso_login():
    """把浏览器送去认证中心。state 用来防 CSRF，示例里简化成随机串。"""
    query = urlencode({'client_id': CLIENT_ID, 'redirect_uri': CALLBACK,
                       'state': secrets.token_urlsafe(8)})
    return RedirectResponse(f'{SSO_BASE}/sso/authorize?{query}', status_code=302)


# ------------------------------------------------------------------ 2. 拿票换身份

@app.get('/sso/callback')
async def sso_callback(request: Request, ticket: str = '', state: str = ''):
    if not ticket:
        return HTMLResponse('缺少 ticket', status_code=400)

    # 关键：换身份是后端对后端的请求，client_secret 绝不能出现在浏览器里
    async with httpx.AsyncClient(timeout=5) as http:
        resp = await http.post(f'{SSO_BASE}/sso/validate', json={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'ticket': ticket,
        })
    if resp.status_code != 200:
        return HTMLResponse(f'票据校验失败：{resp.text}', status_code=401)

    data = resp.json()
    local_sid = secrets.token_urlsafe(32)
    SESSIONS[local_sid] = {'user': data['user'], 'sso_sid': data['sid']}

    response = RedirectResponse('/', status_code=302)
    response.set_cookie(COOKIE, local_sid, httponly=True, samesite='lax', path='/')
    return response


# ------------------------------------------------------------------ 3. 单点登出

@app.post('/sso/logout-notify')
async def logout_notify(request: Request):
    """主页退出时会 POST 过来。校验 secret，否则谁都能把别人踢下线。"""
    body = await request.json()
    if not hmac.compare_digest(str(body.get('secret', '')), CLIENT_SECRET):
        return Response(status_code=403)

    sso_sid = body.get('sid')
    for local_sid in [k for k, v in SESSIONS.items() if v['sso_sid'] == sso_sid]:
        SESSIONS.pop(local_sid, None)
    return {'ok': True}


@app.get('/logout')
def logout(request: Request):
    """只退本系统，主页那边还登着。"""
    SESSIONS.pop(request.cookies.get(COOKIE, ''), None)
    response = RedirectResponse('/', status_code=302)
    response.delete_cookie(COOKIE, path='/')
    return response


@app.get('/logout-all')
def logout_all(request: Request):
    """连主页和其它系统一起退。退完回到注册时填的 home_url。"""
    SESSIONS.pop(request.cookies.get(COOKIE, ''), None)
    query = urlencode({'client_id': CLIENT_ID})
    response = RedirectResponse(f'{SSO_BASE}/sso/logout?{query}', status_code=302)
    response.delete_cookie(COOKIE, path='/')
    return response


if __name__ == '__main__':
    print(f'示例系统跑在 {SELF_BASE}')
    uvicorn.run(app, host='127.0.0.1', port=8801, log_level='warning')
