"""自己改自己：改口令、改显示名、看绑了哪些 IdP、把自己所有设备踢下线。

和 app/routers/users.py 分开的原因：那一套动的是别人的账号，要管理员；
这一套动的是**自己**的，只要登录。混在一起的话，「能不能改这一行」这个判断
就得写进每个函数体里，早晚漏一个。

改口令要验旧口令。纯 OIDC 用户（password_hash 是 NULL）没有旧口令可验，
所以他也不能在这儿给自己设一个——那等于开一条绕过 IdP 的后门。
要给这种人加本地口令，由管理员走 /api/users/<id>/password。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import auth, users
from ..auth import require_login

router = APIRouter(prefix='/api/account', tags=['account'])


class ProfileIn(BaseModel):
    display_name: Optional[str] = None


class PasswordIn(BaseModel):
    old_password: str = ''
    new_password: str = ''


@router.get('')
def profile(user: dict = Depends(require_login)):
    row = users.by_id(int(user['id']))
    if row is None:
        raise HTTPException(401, '未登录')
    info = users.public(row) or {}
    info['has_password'] = row.get('password_hash') is not None
    info['identities'] = [
        {'provider': i['provider'], 'email': i.get('email') or ''}
        for i in users.identities(int(user['id']))
    ]
    return {'account': info}


@router.patch('')
def update_profile(body: ProfileIn, user: dict = Depends(require_login)):
    # 只让改显示名。角色和启用状态**不在这里**：放进来的话，任何一个登录用户
    # 给自己 PATCH 一个 role=admin 就成了管理员
    try:
        row = users.update(int(user['id']), display_name=body.display_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    auth.forget(int(user['id']))
    return {'account': users.public(row)}


@router.put('/password')
def change_password(body: PasswordIn, response: Response,
                    user: dict = Depends(require_login)):
    row = users.by_id(int(user['id']))
    if row is None:
        raise HTTPException(401, '未登录')
    if row.get('password_hash') is None:
        raise HTTPException(400, '这个账号没有本地口令，只能走单点登录；'
                                 '要加一个请找管理员')
    if not users.check_password(row['password_hash'], body.old_password):
        raise HTTPException(400, '旧口令不对')
    try:
        users.set_password(int(user['id']), body.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    auth.forget(int(user['id']))
    # set_password 把 token_version +1 了，手里这枚 Cookie 已经不算数。
    # 顺手换一张新的，不然改完口令自己先被踢回登录页——改口令不是登出
    fresh = users.by_id(int(user['id']))
    assert fresh is not None
    auth.set_cookie(response, auth.make_token(fresh))
    return {'ok': True}


@router.post('/logout-all')
def logout_all(response: Response, user: dict = Depends(require_login)):
    """把自己所有设备上的登录踢掉，包括当前这个。

    口令泄露之后的第一反应通常是这个，所以它和「改口令」分开：
    改口令要记新口令，而这条只要点一下。
    """
    users.bump_token(int(user['id']))
    auth.forget(int(user['id']))
    auth.clear_cookie(response)
    return {'ok': True}
