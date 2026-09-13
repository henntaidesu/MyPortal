"""导航数据：conf.json 里的 `nav` 那一段，整份读、整份写。

配置和数据在同一个文件里（见 app/config.py 开头那段），所以这里不直接碰磁盘，
读写都走 `config.read_section('nav')` / `config.replace_section('nav', ...)`——
原子写、写锁、以及「只换 nav 这一段、别把 auth 盖掉」都在那边。

nav 那一段是「分类 → 卡片」两层，和前端 store.js 里的形状一一对应：

    {"title": "我的门户", "theme": "auto",
     "groups": [{"id": "g1", "name": "运维",
                 "items": [{"id": "x1", "name": "监控", "url": "http://...",
                            "desc": "", "icon": ""}]}]}

**卡片和分类里有哪些字段这里不管**（`clean` 只看标题、主题、条数和体积，别的字段原样带过）：
让后端跟着校验的话，卡片上加一个字段就得两头一起改。加字段只改
webside/src/store.js 的 `normalize()`。

只有一个账号，所以没有「谁的导航」这回事：登进来的人看到的是同一份。
"""
import json
import time
from typing import Any, Optional

from . import config

MAX_GROUPS = 50
MAX_ITEMS = 500               # 所有分类加起来
MAX_BYTES = 256 * 1024        # 上限只是别让人拿它当网盘
THEMES = ('auto', 'light', 'dark')
DEFAULT_TITLE = '我的门户'
DEFAULT_GROUP = '我的导航'


def clean(data: dict) -> dict:
    """把前端传上来的那份收一收，返回该落盘的字典；不合法抛 ValueError。"""
    groups = data.get('groups')
    if groups is None and isinstance(data.get('items'), list):
        # 分类是后加的。手里还拿着老结构（一个扁平的 items 列表）时收进一个默认分类，
        # 和前端 store.js 的 normalize() 是同一套说法
        groups = [{'name': DEFAULT_GROUP, 'items': data['items']}]
    if not isinstance(groups, list):
        raise ValueError('groups 必须是数组')
    if len(groups) > MAX_GROUPS:
        raise ValueError(f'分类最多 {MAX_GROUPS} 个，收到 {len(groups)} 个')

    cleaned_groups = []
    total = 0
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError('groups 里每一项都得是对象')
        items = group.get('items')
        if not isinstance(items, list):
            raise ValueError('每个分类的 items 都得是数组')
        if not all(isinstance(i, dict) for i in items):
            raise ValueError('items 里每一项都得是对象')
        total += len(items)
        # 原样带上分类里别的字段：和卡片一样，分类上加个字段不该要后端跟着改
        cleaned_group = dict(group)
        cleaned_group['name'] = str(group.get('name') or DEFAULT_GROUP)[:100]
        cleaned_group['items'] = items
        cleaned_groups.append(cleaned_group)

    if total > MAX_ITEMS:
        raise ValueError(f'导航最多 {MAX_ITEMS} 个（所有分类加起来），收到 {total} 个')

    theme = data.get('theme')
    cleaned = {
        'title': str(data.get('title') or DEFAULT_TITLE)[:200],
        'theme': theme if theme in THEMES else 'auto',
        'groups': cleaned_groups,
    }
    if len(json.dumps(cleaned, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
        raise ValueError(f'数据太大了（上限 {MAX_BYTES // 1024} KB）；'
                         '图标别直接贴 base64，填个图片地址')
    return cleaned


def load() -> tuple[Optional[dict[str, Any]], int]:
    """返回 (导航数据, 最后修改时间戳)。还没配过就返回 (None, 0)。

    返回 None 而不是抛异常：`nav` 那段被人手改坏时，宁可让页面显示成「还没配过」，
    也不能让整个门户打不开——前端那份本机缓存还在，人照样能用。
    （整个 conf.json 坏掉是另一回事，那会在启动时就停下来，见 config.py。）
    """
    data = config.read_section('nav')
    if not isinstance(data, dict):
        return None, 0
    try:
        mtime = int(config.CONF_PATH.stat().st_mtime)
    except OSError:
        mtime = 0
    return data, mtime


def save(data: dict) -> int:
    """把 nav 那一段整个换掉，返回写完的时间戳。data 必须是 clean() 过的。"""
    config.replace_section('nav', data)
    return int(time.time())


def describe() -> str:
    """给启动日志用的一句话。"""
    data, _ = load()
    if data is None:
        return f'{config.CONF_PATH} 的 nav（还是空的，第一次改动时写进去）'
    groups = data.get('groups')
    if not isinstance(groups, list):
        groups = [data] if isinstance(data.get('items'), list) else []   # 还没迁的老结构
    items = sum(len(g.get('items') or []) for g in groups if isinstance(g, dict))
    return f'{config.CONF_PATH} 的 nav（{len(groups)} 个分类，{items} 个导航）'
