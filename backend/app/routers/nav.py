"""门户导航数据：整份读、整份写，存成一个 JSON 文件。

要登录才能读写，但**只有一个账号**，所以这里没有「取当前用户」这一步——
登进来的人看到的是同一份导航。校验和落盘都在 app/navstore.py 里。

冲突策略是「后写的盖先写的」：两个浏览器同时改，后按下的那边赢。
自己给自己看的一页东西，为它做合并不值当。
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import navstore
from ..auth import require_login

router = APIRouter(prefix='/api/nav', tags=['nav'],
                   dependencies=[Depends(require_login)])


class NavIn(BaseModel):
    data: dict[str, Any]


@router.get('')
def get_nav():
    """还没存过就返回 data = null，前端会把本机缓存那份推上来。"""
    data, updated_at = navstore.load()
    return {'data': data, 'updated_at': updated_at}


@router.put('')
def put_nav(body: NavIn):
    try:
        cleaned = navstore.clean(body.data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        return {'updated_at': navstore.save(cleaned)}
    except OSError as exc:
        # 磁盘满、目录只读之类。前端会退回「改动暂时只存在本机」
        raise HTTPException(500, f'写 conf.json 失败: {exc}') from None
