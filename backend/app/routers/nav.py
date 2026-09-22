"""门户导航数据：**每人一份**，整棵树读、整棵树写。

上一版全站共用一份，所以路由里没有「取当前用户」这一步。这一版每一条查询都
带着 user_id，而这个 id **只从 `require_login` 拿**——不从请求体里读，
那等于让调用方自己说他是谁。

冲突策略还是「后写的盖先写的」：两个浏览器同时改，后按下的那边赢。
自己给自己看的一页东西，为它做合并不值当。
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import navstore
from ..auth import require_login

router = APIRouter(prefix='/api/nav', tags=['nav'])


class NavIn(BaseModel):
    data: dict[str, Any]


@router.get('')
def get_nav(user: dict = Depends(require_login)):
    """这个账号还没配过就返回 data = null，前端会把本机缓存那份推上来。"""
    data, updated_at = navstore.load(int(user['id']))
    return {'data': data, 'updated_at': updated_at}


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
