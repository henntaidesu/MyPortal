"""门户自己的登录接口。前端只跟这几个打交道。"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import clients, db, settings
from ..deps import (clear_login_fail, clear_session_cookie, client_ip, current_session,
                    login_blocked, note_login_fail, set_session_cookie)
from ..notify import broadcast_logout

router = APIRouter(prefix='/api', tags=['auth'])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    new_password: str = Field(min_length=6, max_length=256)


class ProfileIn(BaseModel):
    """改自己的资料。字段全可选，只有传上来的才会动。"""
    username: Optional[str] = Field(None, min_length=2, max_length=64)
    display_name: Optional[str] = Field(None, max_length=128)
    email: Optional[str] = Field(None, max_length=191)


def _public(user: dict) -> dict:
    # id 要给前端：本地那份导航按 id 分区存，按用户名分区的话改个名就找不回来了
    return {k: user[k] for k in ('id', 'username', 'display_name', 'email', 'roles')}


@router.post('/login')
def login(body: LoginIn, request: Request, response: Response):
    key = body.username.strip().lower()
    wait = login_blocked(key)
    if wait:
        raise HTTPException(429, f'密码错误次数过多，请 {wait} 秒后再试')

    user = db.check_login(body.username, body.password)
    if user is None:
        note_login_fail(key)
        raise HTTPException(401, '用户名或密码错误')

    clear_login_fail(key)
    token, sid, expires_at = db.create_session(
        user['id'], request.headers.get('user-agent', ''), client_ip(request))
    set_session_cookie(response, token)
    return {'user': _public(user), 'sid': sid, 'expires_at': expires_at}


@router.get('/me')
def me(request: Request):
    session = current_session(request)
    if session is None:
        raise HTTPException(401, '未登录')
    return {'user': _public(session['user']), 'sid': session['sid'],
            'expires_at': session['expires_at']}


@router.post('/logout', status_code=204)
async def logout(request: Request, response: Response):
    sid = db.drop_session(request.cookies.get(settings.session_cookie(), ''))
    clear_session_cookie(response)
    if sid:
        await broadcast_logout(sid)


@router.post('/password', status_code=204)
def change_password(body: PasswordIn, request: Request):
    """改自己的密码。

    **不验原密码是刻意的**（产品要求），所以这个接口的安全性完全压在会话 Cookie 上：
    谁拿到会话，谁就能改掉这个账号的密码。别在这上面再加别的权限动作。
    会话本身的防线还在——Cookie 是 httponly、只存指纹、登录有限流。
    """
    session = current_session(request)
    if session is None:
        raise HTTPException(401, '未登录')
    db.set_password(session['user']['username'], body.new_password)


@router.patch('/profile')
def update_profile(body: ProfileIn, request: Request):
    """改自己的用户名 / 显示名 / 邮箱。有会话就能改，不再验当前密码。

    原来改用户名要验一次密码，但 /api/password 已经不验原密码了——拿到会话的人
    先改掉密码就能过这道校验，挡不住人只挡手，索性去掉。所以这里和改密码一样，
    安全性全压在会话 Cookie 上。
    改完不掉线，导航也不会丢——会话和 nav 表记的都是 user_id，不是用户名。
    """
    session = current_session(request)
    if session is None:
        raise HTTPException(401, '未登录')

    me = session['user']['username']
    new_name = (body.username or '').strip()

    try:
        user = db.update_user(me, new_username=new_name or None,
                              display_name=body.display_name, email=body.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except db.IntegrityError:
        raise HTTPException(400, f'用户名 {new_name} 已经有人用了') from None
    if user is None:
        raise HTTPException(404, '账号已经不在了，请重新登录')
    return {'user': _public(user)}


@router.get('/sso/clients')
def sso_clients(request: Request):
    """给导航卡片的「免登录跳转」下拉框用。只返回 id 和名字，不含 secret。"""
    if current_session(request) is None:
        raise HTTPException(401, '未登录')
    return {'clients': clients.public_list()}
