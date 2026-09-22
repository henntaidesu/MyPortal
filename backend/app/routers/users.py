"""用户管理：只有管理员进得来（`require_admin`）。

这些接口改的是**别人**的账号，所以每一条都挂 `Depends(require_admin)`，
而不是在函数体里自己判断一次——挂在路由上，以后新增一条路也漏不掉。

「最后一个管理员不能降级 / 停用 / 删除」那几条拦在 app/users.py 里，不在这儿：
拦在数据层的话，以后多一条改用户的路也不会漏。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, users
from ..auth import require_admin

router = APIRouter(prefix='/api/users', tags=['users'],
                   dependencies=[Depends(require_admin)])


class CreateIn(BaseModel):
    username: str
    password: Optional[str] = None      # None = 纯 OIDC 用户，没有本地口令
    display_name: str = ''
    role: str = 'user'


class UpdateIn(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordIn(BaseModel):
    password: Optional[str] = None      # None = 取消本地口令，以后只能走 OIDC


def _row(row: dict) -> dict:
    """列表里那一行。口令哈希不在其中。"""
    return {
        'id': int(row['id']),
        'username': row['username'],
        'display_name': row.get('display_name') or row['username'],
        'role': row.get('role') or 'user',
        'is_active': bool(row.get('is_active', 1)),
        'has_password': bool(row.get('has_password', 0)),
        'identities': int(row.get('identities') or 0),
        'items': int(row.get('items') or 0),
        'created_at': str(row.get('created_at') or ''),
    }


@router.get('')
def list_users():
    return {'users': [_row(r) for r in users.list_all()]}


@router.post('')
def create_user(body: CreateIn):
    try:
        row = users.create(body.username, body.password, role=body.role,
                           display_name=body.display_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {'user': users.public(row)}


@router.patch('/{user_id}')
def update_user(user_id: int, body: UpdateIn):
    try:
        row = users.update(user_id, display_name=body.display_name,
                           role=body.role, is_active=body.is_active)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    # 角色和启用状态是每个请求都要看的东西，缓存里那份得当场丢掉，
    # 不然改完最多还要等 10 秒才生效（见 app/auth.py 顶上那段）
    auth.forget(user_id)
    return {'user': users.public(row)}


@router.put('/{user_id}/password')
def reset_password(user_id: int, body: PasswordIn, admin: dict = Depends(require_admin)):
    if users.by_id(user_id) is None:
        raise HTTPException(404, '用户不存在')
    try:
        users.set_password(user_id, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    # set_password 已经把 token_version +1 了，这个人所有设备上的登录当场作废。
    # 管理员给自己重置口令时，把自己也踢下线是对的——他知道自己在干什么
    auth.forget(user_id)
    return {'ok': True}


@router.post('/{user_id}/logout')
def kick(user_id: int):
    """把这个人所有设备上的登录踢掉。口令不变。"""
    if users.by_id(user_id) is None:
        raise HTTPException(404, '用户不存在')
    users.bump_token(user_id)
    auth.forget(user_id)
    return {'ok': True}


@router.delete('/{user_id}')
def delete_user(user_id: int, admin: dict = Depends(require_admin)):
    """连人带导航一起删（外键 ON DELETE CASCADE），删了就没了。"""
    if user_id == int(admin['id']):
        # 删自己会把当前这个会话也一起删掉，剩下的请求全是 401，
        # 页面上看着像「点了删除之后整个门户坏了」
        raise HTTPException(400, '不能删除自己')
    try:
        users.delete(user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    auth.forget(user_id)
    return {'ok': True}
