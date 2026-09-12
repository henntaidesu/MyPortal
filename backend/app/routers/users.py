"""用户管理接口。整组都要管理员身份，鉴权统一走 deps.require_admin。

这里挡着三条「别把所有人锁在门外」的底线，改之前先想清楚为什么有它们：

  - 不能停用、删除自己；
  - 不能把最后一个还启用着的管理员停用、删掉或者摘掉 admin 角色；
  - 用户名的格式由 db.validate_username 把关，唯一性由 users 表的唯一索引兜底。

万一还是被锁在门外了，命令行那套（manage.py users / adduser / rename …）始终能用，
它不看会话也不看角色。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db
from ..deps import require_admin
from ..notify import broadcast_logout

router = APIRouter(prefix='/api/users', tags=['users'])


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=256)
    display_name: str = Field('', max_length=128)
    email: str = Field('', max_length=191)
    roles: str = Field('', max_length=255)


class UserPatch(BaseModel):
    """全是可选：只有显式传上来的字段才会被改。"""
    username: Optional[str] = Field(None, min_length=2, max_length=64)
    display_name: Optional[str] = Field(None, max_length=128)
    email: Optional[str] = Field(None, max_length=191)
    roles: Optional[str] = Field(None, max_length=255)
    disabled: Optional[bool] = None


class ResetIn(BaseModel):
    new_password: str = Field(min_length=6, max_length=256)


def _out(user: dict) -> dict:
    """给管理端看的用户字段。password 那一列从来不出这个函数。"""
    return {k: user[k] for k in ('username', 'display_name', 'email', 'roles', 'disabled')}


def _clean_roles(raw: str) -> str:
    """逗号分隔，去空去重、保持原顺序。这串会原样传给业务系统。"""
    seen: list[str] = []
    for role in (raw or '').replace('，', ',').split(','):
        role = role.strip()
        if role and role not in seen:
            seen.append(role)
    return ','.join(seen)


def _find(username: str) -> dict:
    user = db.get_user(username)
    if user is None:
        raise HTTPException(404, f'没有用户 {username}')
    return user


def _is_me(session: dict, username: str) -> bool:
    # 用户名大小写不敏感（utf8mb4_general_ci），比较时也得跟着不敏感
    return session['user']['username'].lower() == username.lower()


def _guard_last_admin(username: str, action: str) -> None:
    if db.admin_usernames() == [username]:
        raise HTTPException(400, f'{username} 是最后一个启用的管理员，不能{action}；'
                                 f'先给别人加上 admin 角色')


@router.get('')
def list_users(request: Request):
    require_admin(request)
    return {'users': [_out(u) for u in db.list_users()]}


@router.post('', status_code=201)
def create_user(body: UserIn, request: Request):
    require_admin(request)
    try:
        user = db.create_user(body.username, body.password,
                              body.display_name.strip() or body.username.strip(),
                              body.email, _clean_roles(body.roles))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except db.IntegrityError:
        raise HTTPException(400, f'用户名 {body.username} 已经有人用了') from None
    return _out(user)


@router.patch('/{username}')
async def update_user(username: str, body: UserPatch, request: Request):
    session = require_admin(request)
    target = _find(username)
    username = target['username']          # 以库里那份为准，大小写照它来

    turning_off = body.disabled is True and not target['disabled']
    dropping_admin = (body.roles is not None and 'admin' in target['roles']
                      and 'admin' not in _clean_roles(body.roles).split(','))
    if turning_off:
        if _is_me(session, username):
            raise HTTPException(400, '不能停用自己')
        _guard_last_admin(username, '停用')
    if dropping_admin and not target['disabled']:
        _guard_last_admin(username, '摘掉 admin 角色')

    try:
        user = db.update_user(
            username,
            new_username=body.username,
            display_name=body.display_name,
            email=body.email,
            roles=None if body.roles is None else _clean_roles(body.roles),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except db.IntegrityError:
        raise HTTPException(400, f'用户名 {body.username} 已经有人用了') from None
    if user is None:
        raise HTTPException(404, f'没有用户 {username}')

    sids: list[str] = []
    if body.disabled is not None and body.disabled != target['disabled']:
        if body.disabled:
            sids = db.sessions_of_user(user['username'])
        db.set_disabled(user['username'], body.disabled)   # 停用时顺手清掉会话
        user = db.get_user(user['username'])

    for sid in sids:                       # 被停用的人在各业务系统里也跟着退出去
        await broadcast_logout(sid)
    return _out(user)


@router.put('/{username}/password', status_code=204)
async def reset_password(username: str, body: ResetIn, request: Request):
    """管理员重置别人的口令。不用验原密码——管理员本来就有这个权力。

    重置多半是因为账号出了状况（忘了密码、疑似泄露），所以顺手把这个人已经
    在线的会话全踢掉，各业务系统也跟着退，免得拿旧口令换来的会话还在用。
    """
    require_admin(request)
    target = _find(username)
    db.set_password(target['username'], body.new_password)
    for sid in db.drop_sessions_of_user(target['username']):
        await broadcast_logout(sid)


@router.delete('/{username}', status_code=204)
async def delete_user(username: str, request: Request):
    session = require_admin(request)
    target = _find(username)
    if _is_me(session, target['username']):
        raise HTTPException(400, '不能删除自己')
    _guard_last_admin(target['username'], '删除')

    # 会话会跟着外键级联删掉，所以 sid 要先取出来再删人
    sids = db.sessions_of_user(target['username'])
    db.delete_user(target['username'])
    for sid in sids:
        await broadcast_logout(sid)
