"""登录、登出、我是谁。

一个账号，认的是 conf.json 里那对用户名口令，细节见 app/auth.py。
"""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth

router = APIRouter(prefix='/api', tags=['auth'])


class LoginIn(BaseModel):
    username: str = ''
    password: str = ''


def _public(user: str | None) -> dict:
    """下发给前端的身份信息。只有一个账号，所以除了名字没别的可说。"""
    return {'user': {'username': user} if user else None}


@router.get('/me')
def me(request: Request):
    """没登录也返回 200（user 为 null）。

    不用 401：页面一起来就要问一次，401 会触发前端那条「会话过期」的通路，
    把「本来就还没登录」也报成掉线。
    """
    return _public(auth.current_user(request))


@router.post('/login')
def login(body: LoginIn, request: Request, response: Response):
    key = auth.client_ip(request)
    wait = auth.login_blocked(key)
    if wait:
        raise HTTPException(429, f'失败次数太多，请 {wait} 秒后再试')

    if not auth.check_credentials(body.username, body.password):
        auth.note_fail(key)
        # 不区分「用户名不对」和「口令不对」：分开说等于告诉对方用户名蒙对了
        raise HTTPException(401, '用户名或密码不对')

    auth.clear_fails(key)
    auth.set_cookie(response, auth.make_token())
    return _public(auth.AUTH_USERNAME)


@router.post('/logout')
def logout(response: Response):
    """只是把 Cookie 删掉。

    服务端不存会话，所以没有「把别的设备也踢下线」这回事——真要全部踢掉，
    改 conf.json 里的口令，签名密钥跟着变，所有已发出的 Cookie 当场失效。
    """
    auth.clear_cookie(response)
    return {'ok': True}
