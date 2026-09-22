"""导航数据：**每人一份**，存在 MySQL 的 nav_prefs / nav_groups / nav_items 三张表里。

上一版整份导航是 conf.json 里的一段 JSON，全站共用。这一版每一行都带 user_id，
`load` / `save` 都必须给出是谁的——**没有「当前用户」这种全局状态**，
谁的数据由调用方（路由的 `require_login`、代理的归属校验）说了算，
免得哪天漏传一个参数就变成串号。

前端看到的形状一个字没变，还是那棵两层树：

    {"title": "我的门户", "theme": "auto",
     "groups": [{"id": "g1", "name": "运维",
                 "items": [{"id": "x1", "name": "监控", "url": "http://...",
                            "desc": "", "icon": "", "proxy": false,
                            "proxyHosts": ""}]}]}

树和表之间的对应：

  - 前端那个 `id` 存进 `client_id`，**不换成数据库主键**。前端整棵树拿它做 key，
    拖拽、编辑、`/api/proxy/<id>` 全指着它；换成自增 id 的话这三处都要跟着改，
    而且卡片在「存盘 → 拿回新 id」之间那一拍会失去身份。
  - `desc` 存进 `descr`：`desc` 是 MySQL 的保留字。
  - **后端不认识的字段原样存进 `extra`**，读出来再摊回卡片上。这是上一版
    「卡片上加字段只改 store.js」那条约定的延续——列写死的话，前端加一个字段
    就得改表结构、改 SQL、改两头的代码。

## 存一次 = 把这个人那棵树整个换掉

删光再插一遍，全在一个事务里。不做逐条 diff：这一页是自己给自己看的东西，
一棵树撑死几百行，diff 省下的那点写入量换不回它带来的一堆边界情况
（拖拽同时改名、换分类再改回来……）。冲突仍然是后写的盖先写的。
"""
import json
import time
from typing import Any, Optional

from . import config, cookiejar, db

MAX_GROUPS = 50
MAX_ITEMS = 500               # 所有分类加起来
MAX_BYTES = 256 * 1024        # 上限只是别让人拿它当网盘
THEMES = ('auto', 'light', 'dark')
DEFAULT_TITLE = '我的门户'
DEFAULT_GROUP = '我的导航'

# 摊进表里的那几列。剩下的字段原样进 extra
_ITEM_COLUMNS = ('id', 'name', 'url', 'desc', 'icon', 'proxy', 'proxyHosts', 'cookieJar')
_GROUP_COLUMNS = ('id', 'name', 'items')

# 进程内的改动计数，给 app/proxy.py 的目标表缓存当失效信号用。
# 不落库：它只需要在**这个进程里**「存完导航下一个请求就看得到新的」，
# 跨进程那点延迟由那边的 TTL 兜着
_revision: dict[int, int] = {}


def revision(user_id: int) -> int:
    return _revision.get(user_id, 0)


# ---------------------------------------------------------------- 校验

def clean(data: dict) -> dict:
    """把前端传上来的那份收一收，返回该落库的字典；不合法抛 ValueError。

    校验的仍然只有标题、主题、条数和体积，**卡片和分类里有哪些字段这里不管**。
    """
    groups = data.get('groups')
    if groups is None and isinstance(data.get('items'), list):
        # 分类是后加的。手里还拿着老结构（一个扁平的 items 列表）时收进一个默认分类，
        # 和前端 store.js 的 normalize() 是同一套说法。去掉的话老用户第一次打开是一片空
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
        cleaned_group = dict(group)       # 原样带上分类里别的字段
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


# ---------------------------------------------------------------- 读

def _row_to_item(row: dict) -> dict:
    item = db.loads(row.get('extra')) or {}
    if not isinstance(item, dict):
        item = {}
    item.update({
        'id': row['client_id'],
        'name': row['name'],
        'url': row['url'] or '',
        'desc': row['descr'] or '',
        'icon': row['icon'] or '',
        # 落库时三挡存成 '' / 'true' / 'site'，这里还原成前端那三个值
        'proxy': 'site' if row['proxy'] == 'site' else (row['proxy'] == 'true'),
        'proxyHosts': item.get('proxyHosts', '') if isinstance(item.get('proxyHosts'), str) else '',
        # 开了就把上游的登录态存在服务器上（app/cookiejar.py）。和 proxyHosts 一样
        # 走 extra，不单开一列：它们都是「代理模式的附属参数」，不是独立的一等字段
        'cookieJar': bool(item.get('cookieJar')),
    })
    return item


def load(user_id: int) -> tuple[Optional[dict[str, Any]], int]:
    """返回 (这个人的导航, 最后修改时间戳)。还没配过就返回 (None, 0)。

    **返回 None 而不是一份空导航**：前端靠「data 为 null」判断这是个还没配过的
    账号，会把本机缓存那份推上来。给成 `{"groups": []}` 的话，新用户第一次打开
    会看到一片空，而且本机缓存再也推不上去。
    """
    with db.cursor() as cur:
        cur.execute('SELECT title, theme, UNIX_TIMESTAMP(updated_at) AS ts '
                    'FROM nav_prefs WHERE user_id = %s', (user_id,))
        prefs = cur.fetchone()
        cur.execute('SELECT id, client_id, name, extra FROM nav_groups '
                    'WHERE user_id = %s ORDER BY `position`, id', (user_id,))
        group_rows = cur.fetchall()
        cur.execute('SELECT group_id, client_id, name, url, descr, icon, proxy, extra '
                    'FROM nav_items WHERE user_id = %s ORDER BY `position`, id', (user_id,))
        item_rows = cur.fetchall()

    if not group_rows:
        # 一个分类都没有 = 还没配过。prefs 那一行是建用户时就插好的，不算数
        return None, 0

    by_group: dict[int, list] = {}
    for row in item_rows:
        by_group.setdefault(int(row['group_id']), []).append(_row_to_item(row))

    groups = []
    for row in group_rows:
        group = db.loads(row.get('extra')) or {}
        if not isinstance(group, dict):
            group = {}
        group.update({
            'id': row['client_id'],
            'name': row['name'],
            'items': by_group.get(int(row['id']), []),
        })
        groups.append(group)

    return {
        'title': (prefs or {}).get('title') or DEFAULT_TITLE,
        'theme': (prefs or {}).get('theme') or 'auto',
        'groups': groups,
    }, int((prefs or {}).get('ts') or 0)


def card(user_id: int, client_id: str) -> Optional[dict]:
    """按 (用户, 卡片 id) 取一张卡片。门户代理拿它做归属校验。

    **user_id 是查询条件的一部分，不是查出来之后再比对的。** 比对写法只要哪天
    漏一个 if 就变成「拿到别人的卡片 id 就能借门户往里打」，而这条路正是
    app/proxy.py 最该拦住的东西。
    """
    with db.cursor() as cur:
        cur.execute('SELECT group_id, client_id, name, url, descr, icon, proxy, extra '
                    'FROM nav_items WHERE user_id = %s AND client_id = %s',
                    (user_id, client_id))
        row = cur.fetchone()
    return _row_to_item(row) if row else None


def cards(user_id: int) -> list[dict]:
    """这个人所有开了代理的卡片。给 app/proxy.py 建目标表用。"""
    with db.cursor() as cur:
        cur.execute("SELECT group_id, client_id, name, url, descr, icon, proxy, extra "
                    "FROM nav_items WHERE user_id = %s AND proxy <> ''", (user_id,))
        rows = cur.fetchall()
    return [_row_to_item(r) for r in rows]


def proxy_cards() -> list[dict]:
    """全库开了代理的卡片，**只给启动日志那句话用**。

    不带 user_id，所以不能拿它去转发：那样两个人撞了同一个卡片 id 就串号了。
    """
    with db.cursor() as cur:
        cur.execute("SELECT group_id, client_id, name, url, descr, icon, proxy, extra "
                    "FROM nav_items WHERE proxy <> ''")
        rows = cur.fetchall()
    return [_row_to_item(r) for r in rows]


# ---------------------------------------------------------------- 写

def _proxy_column(value: Any) -> str:
    if value == 'site' or str(value).lower() == 'site':
        return 'site'
    return 'true' if value else ''


def _extra(source: dict, known: tuple[str, ...]) -> Optional[str]:
    """后端不认识的字段，原样收进一个 JSON 串。一个都没有就存 NULL。"""
    rest = {k: v for k, v in source.items() if k not in known}
    return json.dumps(rest, ensure_ascii=False) if rest else None


def save(user_id: int, data: dict) -> int:
    """把这个人那棵树整个换掉，返回写完的时间戳。data 必须是 clean() 过的。

    删光再插一遍，一个事务里做完。中途失败会整个回滚——半棵树比旧的那棵更糟。
    """
    groups = data.get('groups') or []
    # 开着 Cookie 代理的卡片。存完之后除了这些，别的罐子一律收掉（见 cookiejar.retain）
    with_jar = {str(i.get('id') or '') for g in groups
                for i in (g.get('items') or []) if i.get('cookieJar')}
    with db.cursor(write=True) as cur:
        # 先落 prefs：`updated_at` 是 ON UPDATE CURRENT_TIMESTAMP，但值没变的
        # UPDATE 不会触发它，所以显式写一次时间，不然只动卡片时这个戳不走
        cur.execute(
            'INSERT INTO nav_prefs (user_id, title, theme) VALUES (%s, %s, %s) '
            'ON DUPLICATE KEY UPDATE title = VALUES(title), theme = VALUES(theme), '
            'updated_at = CURRENT_TIMESTAMP',
            (user_id, data.get('title') or DEFAULT_TITLE, data.get('theme') or 'auto'))

        # 卡片有指向分类的外键，删分类会级联带走卡片；显式先删卡片只是让顺序一目了然
        cur.execute('DELETE FROM nav_items WHERE user_id = %s', (user_id,))
        cur.execute('DELETE FROM nav_groups WHERE user_id = %s', (user_id,))

        for gi, group in enumerate(groups):
            cur.execute(
                'INSERT INTO nav_groups (user_id, client_id, name, `position`, extra) '
                'VALUES (%s, %s, %s, %s, %s)',
                (user_id, str(group.get('id') or '')[:64] or f'g{gi}',
                 str(group.get('name') or DEFAULT_GROUP)[:100], gi,
                 _extra(group, _GROUP_COLUMNS)))
            group_id = cur.lastrowid

            rows = []
            for ii, item in enumerate(group.get('items') or []):
                extra = {k: v for k, v in item.items() if k not in _ITEM_COLUMNS}
                # proxyHosts 既要进 extra（读回来时摊在卡片上），也不值得单开一列：
                # 它只在整站模式下有意义，而且是逗号分隔的自由文本
                hosts = item.get('proxyHosts')
                if isinstance(hosts, str) and hosts.strip():
                    extra['proxyHosts'] = hosts.strip()
                if item.get('cookieJar'):
                    extra['cookieJar'] = True
                rows.append((
                    user_id, group_id, str(item.get('id') or '')[:64] or f'g{gi}i{ii}',
                    str(item.get('name') or '')[:200], str(item.get('url') or ''),
                    str(item.get('desc') or '')[:500], str(item.get('icon') or ''),
                    _proxy_column(item.get('proxy')), ii,
                    json.dumps(extra, ensure_ascii=False) if extra else None))
            if rows:
                cur.executemany(
                    'INSERT INTO nav_items (user_id, group_id, client_id, name, url, '
                    'descr, icon, proxy, `position`, extra) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)', rows)

        # 同一个事务里收掉「卡片删了」和「开关关了」留下的登录数据
        cookiejar.retain(cur, user_id, with_jar)

    _revision[user_id] = _revision.get(user_id, 0) + 1
    return int(time.time())


# ---------------------------------------------------------------- 老数据

LEGACY_FLAG = 'legacy_nav_imported'


def import_legacy(user_id: int) -> bool:
    """把老版本 conf.json 里那段 `nav` 搬进这个人名下。搬过就不再搬。

    只在「这个人名下一个分类都没有」时搬：已经在门户里建过导航的人，
    不该被一份老数据盖掉。搬完在 portal_meta 里打个标记，conf.json 里那段
    **不删**——那是人家自己的文件，程序只管读。
    """
    if db.meta_get(LEGACY_FLAG):
        return False
    data = config.legacy_nav()
    if not isinstance(data, dict):
        db.meta_set(LEGACY_FLAG, 'none')
        return False

    existing, _ = load(user_id)
    if existing is not None:
        db.meta_set(LEGACY_FLAG, 'skipped')
        return False

    try:
        save(user_id, clean(data))
    except ValueError as exc:
        print(f'[导航] conf.json 里那段老导航搬不过来，跳过: {exc}')
        db.meta_set(LEGACY_FLAG, 'failed')
        return False
    db.meta_set(LEGACY_FLAG, str(int(time.time())))
    print(f'[导航] 已把 conf.json 里那段老导航搬到用户 #{user_id} 名下'
          f'（{config.CONF_PATH} 里那一段留着没动，确认没问题可以自己删）')
    return True


def describe() -> str:
    """给启动日志用的一句话。"""
    with db.cursor() as cur:
        cur.execute('SELECT COUNT(DISTINCT user_id) AS u, COUNT(*) AS n FROM nav_groups')
        groups = cur.fetchone()
        cur.execute('SELECT COUNT(*) AS n FROM nav_items')
        items = cur.fetchone()['n']
    return (f'{groups["u"]} 个人配了导航，共 {groups["n"]} 个分类、{items} 个卡片')
