"""门户导航数据：**每人一份**，整棵树读、整棵树写。

上一版全站共用一份，所以路由里没有「取当前用户」这一步。这一版每一条查询都
带着 user_id，而这个 id **只从 `require_login` 拿**——不从请求体里读，
那等于让调用方自己说他是谁。

冲突策略还是「后写的盖先写的」：两个浏览器同时改，后按下的那边赢。
自己给自己看的一页东西，为它做合并不值当。

    GET  /api/nav/shares/<卡片 id>   卡片编辑框：这张卡片分享给了谁、哪几个共享登录（管理员）
    PUT  /api/nav/shares/<卡片 id>   卡片编辑框：整份换掉分享名单（管理员）

分享名单挂在 (当前这个管理员, 卡片 id) 上，卡片 id 只认他自己名下的——
被分享的人就算也是管理员，调这两条改到的也只是他自己那张同 id 的卡片，
动不了别人分享给他的那一张。
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import navstore
from ..auth import require_admin, require_login

router = APIRouter(prefix='/api/nav', tags=['nav'])


class NavIn(BaseModel):
    data: dict[str, Any]


# 分享过来的卡片只下发画卡片用得上的这几项。proxyHosts、cookieJar 这些配置照样生效——
# 代理那边（app/proxy.py）拿的是管理员那张卡片的全份——只是浏览器用不着它们。
# shareLogin 要下发：连登录一起分享时点进去是分享人的账号，卡片上得看得出来
_SHARED_FIELDS = ('id', 'name', 'url', 'desc', 'icon', 'proxy', 'sso', 'ssoLogin', 'owner',
                  'shareLogin')


@router.get('')
def get_nav(user: dict = Depends(require_login)):
    """这个账号还没配过就返回 data = null，前端会把本机缓存那份推上来。

    `shared` 是管理员分享给他的卡片（navstore.shared），**不在 data 里**：
    放进 data 的话前端存导航时会把它当成自己的卡片写回去，变成一份拷贝。
    """
    user_id = int(user['id'])
    data, updated_at = navstore.load(user_id)
    shared = [{k: item.get(k) for k in _SHARED_FIELDS} for item in navstore.shared(user_id)]
    return {'data': data, 'updated_at': updated_at, 'shared': shared}


@router.put('')
def put_nav(body: NavIn, user: dict = Depends(require_login)):
    try:
        cleaned = navstore.clean(body.data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        return {'updated_at': navstore.save(int(user['id']), cleaned)}
    except Exception as exc:
        # 数据库连不上、磁盘满之类。前端会退回「改动暂时只存在本机」
        raise HTTPException(500, f'存导航失败: {exc}') from None


class SharesIn(BaseModel):
    users: list[int]
    login: list[int] = []            # 其中连登录一起分享的，默认一个都没有


@router.get('/shares/{item_id}')
def get_shares(item_id: str, admin: dict = Depends(require_admin)):
    return navstore.shares(int(admin['id']), item_id[:64])


@router.put('/shares/{item_id}')
def put_shares(item_id: str, body: SharesIn, admin: dict = Depends(require_admin)):
    saved = navstore.set_shares(int(admin['id']), item_id[:64], body.users, body.login)
    print(f'[导航] {admin["username"]} 把卡片 {item_id[:64]} 分享给了 {len(saved["users"])} 个人，'
          f'其中 {len(saved["login"])} 个共享登录')
    return saved
