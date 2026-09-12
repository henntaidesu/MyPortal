"""门户自己的登录接口。前端只跟这几个打交道。"""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import clients, db
from ..config import SESSION_COOKIE
from ..deps import (clear_login_fail, clear_session_cookie, client_ip, current_session,
                    login_blocked, note_login_fail, set_session_cookie)
from ..notify import broadcast_logout

router = APIRouter(prefix='/api', tags=['auth'])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=6, max_length=256)


def _public(user: dict) -> dict:
    return {k: user[k] for k in ('username', 'display_name', 'email', 'roles')}


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
    sid = db.drop_session(request.cookies.get(SESSION_COOKIE, ''))
    clear_session_cookie(response)
    if sid:
        await broadcast_logout(sid)


@router.post('/password', status_code=204)
def change_password(body: PasswordIn, request: Request):
    session = current_session(request)
    if session is None:
        raise HTTPException(401, '未登录')
    if db.check_login(session['user']['username'], body.old_password) is None:
        raise HTTPException(400, '原密码不正确')
    db.set_password(session['user']['username'], body.new_password)


@router.get('/sso/clients')
def sso_clients(request: Request):
    """给导航卡片的「免登录跳转」下拉框用。只返回 id 和名字，不含 secret。"""
    if current_session(request) is None:
        raise HTTPException(401, '未登录')
    return {'clients': clients.public_list()}
