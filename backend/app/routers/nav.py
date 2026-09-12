"""门户导航数据：每人一份 JSON，整份读、整份写。

**user_id 一律从会话里取，绝不从请求里取。** 这里没有 `?user=` 也没有 body 里的 id，
放一个进来就等于任何人都能读写别人那份导航。管理员也没有单独的入口——
这份数据是各人自己的，不归管理端管。

冲突策略是「后写的盖先写的」：同一个人在两台机器上同时改，后按下的那边赢。
导航是自己给自己用的一页东西，为它做合并不值当。
"""
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import db
from ..deps import current_session

router = APIRouter(prefix='/api/nav', tags=['nav'])

MAX_ITEMS = 500
MAX_BYTES = 256 * 1024        # 上限只是别让人拿它当网盘，MEDIUMTEXT 本身放得下 16 MB
THEMES = ('auto', 'light', 'dark')
DEFAULT_TITLE = '我的门户'


class NavIn(BaseModel):
    data: dict[str, Any]


def _me(request: Request) -> dict:
    session = current_session(request)
    if session is None:
        raise HTTPException(401, '未登录')
    return session['user']


def _clean(data: dict) -> str:
    """把前端传上来的那份收一收，返回该入库的 JSON 文本；不合法抛 ValueError。

    卡片里有哪些字段不在这里管：那是 webside/src/store.js 的事，后端跟着校验的话，
    卡片上加一个字段就得两头一起改。这里只管住标题、主题，和别把库撑爆。
    """
    items = data.get('items')
    if not isinstance(items, list):
        raise ValueError('items 必须是数组')
    if len(items) > MAX_ITEMS:
        raise ValueError(f'导航最多 {MAX_ITEMS} 个，收到 {len(items)} 个')
    if not all(isinstance(i, dict) for i in items):
        raise ValueError('items 里每一项都得是对象')

    theme = data.get('theme')
    text = json.dumps({
        'title': str(data.get('title') or DEFAULT_TITLE)[:200],
        'theme': theme if theme in THEMES else 'auto',
        'items': items,
    }, ensure_ascii=False)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError(f'数据太大了（上限 {MAX_BYTES // 1024} KB）；'
                         '图标别直接贴 base64，填个图片地址')
    return text


@router.get('')
def get_nav(request: Request):
    """没存过返回 data = null，前端会把本机那份推上来（老用户的数据就是这么搬过去的）。"""
    row = db.get_nav(_me(request)['id'])
    if row is None:
        return {'data': None, 'updated_at': 0}
    text, updated_at = row
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 库里那份被手改坏了。当没存过处理，总比让人打不开门户强
        print('[nav] 库里的导航数据不是合法 JSON，当作空的返回')
        return {'data': None, 'updated_at': 0}
    return {'data': data, 'updated_at': updated_at}


@router.put('')
def put_nav(body: NavIn, request: Request):
    user = _me(request)            # 先认人再干活
    try:
        text = _clean(body.data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {'updated_at': db.set_nav(user['id'], text)}
