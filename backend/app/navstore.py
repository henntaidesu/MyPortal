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

## 分享过来的卡片不进这棵树

管理员可以把自己的任意一张卡片分享给别人（`card_grants` 表，`shared` / `set_shares`）。
被分享的人看到的是**那张卡片本身**，不是一份拷贝：不进他的 nav_groups / nav_items，
他存导航时也不会把它写进去，管理员改了配置他那边跟着变，取消分享就没了。

**默认分享的是配置，不是登录。** 卡片上的地址、门户代理、额外域名、Cookie 代理、单点登录
这些照管理员那张走；但 Cookie 代理的罐子钉在 (被分享的人, 卡片) 上，单点登录认的是
他自己登着的门户账号——他用的是自己的登录，管理员的登录态一条都不会交给他。

管理员可以逐人选「共享登录」（`card_grants.share_login`）：选了的那个人在这张卡片上
用的是**管理员的**登录——罐子用管理员那份、单点登录以管理员的身份发码（app/idp.py 的
`login_as`）、浏览器里的 Cookie 标记和本地存储前缀也按管理员算（app/proxy.py 的 `Target.login`）。
"""
import json
import time
from typing import Any, Optional

from . import config, cookiejar, db, idp

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

# 全局的那一份：谁存了导航、谁改了分享都 +1。分享过来的卡片在**别人**名下，
# 管理员改了它的地址，被分享那个人的代理目标表也得跟着失效（app/proxy.py）
_shared_revision = 0


def revision(user_id: int) -> int:
    return _revision.get(user_id, 0)


def shared_revision() -> int:
    return _shared_revision


def _bump_shared() -> None:
    global _shared_revision
    _shared_revision += 1


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
    # 别人分享过来的卡片不在这棵树里，但罐子钉在 (这个人, 那张卡片) 上。
    # 不算进来的话他每存一次导航，那几张卡片的登录态就被清一次
    with_jar |= {i['id'] for i in shared(user_id) if i.get('cookieJar')}
    # 开着「单点登录」的卡片。它们登记的 IdP 客户端才算数（见 idp.sync）
    with_sso = {str(i.get('id') or '') for g in groups
                for i in (g.get('items') or []) if i.get('sso')}
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
        idp.sync(cur, user_id, with_sso)

    _revision[user_id] = _revision.get(user_id, 0) + 1
    _bump_shared()
    return int(time.time())


# ---------------------------------------------------------------- 分享给别人的卡片

def shared(user_id: int) -> list[dict]:
    """管理员分享给这个人的卡片：完整的卡片字典，外加 `owner`（分享人的显示名）。

    两条都成立才算：分享人**现在**还是启用中的管理员（同 idp.client 的路数，被降级的
    管理员发出去的分享当场作废）、那张卡片还在。卡片删了时分享行留着不删，
    见 card_grants 表上的说明。

    两处用它：/api/nav 带回去画在页面上；app/proxy.py 拿它补进这个人的代理目标表——
    开着门户代理的卡片点开就是 /api/proxy/<那张卡片 id>/，单点登录入口多半也在底下，
    不补的话被分享的人一点开就是 404。

    带回去的卡片上多两项：`shareLogin`（这个人是不是连登录一起被分享的）、
    `ownerId`（分享人的用户 id，app/proxy.py 拿它定这张卡片用谁的登录；不下发给浏览器）。
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT i.group_id, i.client_id, i.name, i.url, i.descr, i.icon, i.proxy, i.extra, "
            "u.username, u.display_name, g.owner_id, g.share_login "
            "FROM card_grants g "
            "JOIN users u ON u.id = g.owner_id AND u.is_active = 1 AND u.role = 'admin' "
            "JOIN nav_items i ON i.user_id = g.owner_id AND i.client_id = g.item_id "
            "WHERE g.user_id = %s AND g.owner_id <> %s ORDER BY i.name, i.id",
            (user_id, user_id))
        rows = cur.fetchall()
    out = []
    for row in rows:
        item = _row_to_item(row)
        item['owner'] = row['display_name'] or row['username']
        item['ownerId'] = int(row['owner_id'])
        item['shareLogin'] = bool(row['share_login'])
        out.append(item)
    return out


def shares(owner_id: int, item_id: str) -> dict:
    """这张卡片分享给了谁、其中哪几个连登录一起（都是用户 id）。卡片编辑框读回来用。"""
    with db.cursor() as cur:
        cur.execute('SELECT user_id, share_login FROM card_grants '
                    'WHERE owner_id = %s AND item_id = %s', (owner_id, item_id))
        rows = cur.fetchall()
    return {'users': sorted(int(r['user_id']) for r in rows),
            'login': sorted(int(r['user_id']) for r in rows if r['share_login'])}


def set_shares(owner_id: int, item_id: str, user_ids: list[int],
               login_ids: list[int] = ()) -> dict:
    """整份换掉这张卡片的分享名单，返回换完的（形状同 `shares`）。
    不存在的用户 id 和分享人自己悄悄丢掉；`login_ids` 是其中连登录一起分享的，不在名单里的忽略。

    被取消的人在这张卡片上的 Cookie 罐子一并收掉：取消分享的本意就是「别再让他用」，
    罐子留着的话，哪天再分享回来他直接就是登录状态。他自己名下恰好有张同 id 的
    卡片时不动——那个罐子是他自己那张卡片的。
    """
    wanted = {int(u) for u in user_ids or []} - {owner_id}
    with db.cursor(write=True) as cur:
        if wanted:
            marks = ', '.join(['%s'] * len(wanted))
            cur.execute(f'SELECT id FROM users WHERE id IN ({marks})', tuple(wanted))
            wanted = {int(r['id']) for r in cur.fetchall()}
        login = {int(u) for u in login_ids or []} & wanted
        cur.execute('SELECT user_id FROM card_grants WHERE owner_id = %s AND item_id = %s',
                    (owner_id, item_id))
        before = {int(r['user_id']) for r in cur.fetchall()}
        for uid in before - wanted:
            cur.execute('DELETE FROM card_grants WHERE owner_id = %s AND item_id = %s '
                        'AND user_id = %s', (owner_id, item_id, uid))
            cur.execute('DELETE FROM proxy_cookies WHERE user_id = %s AND item_id = %s '
                        'AND NOT EXISTS (SELECT 1 FROM nav_items '
                        'WHERE user_id = %s AND client_id = %s)', (uid, item_id, uid, item_id))
        if wanted - before:
            cur.executemany('INSERT INTO card_grants (owner_id, item_id, user_id, share_login) '
                            'VALUES (%s, %s, %s, %s)',
                            [(owner_id, item_id, uid, int(uid in login)) for uid in wanted - before])
        for uid in wanted & before:
            cur.execute('UPDATE card_grants SET share_login = %s '
                        'WHERE owner_id = %s AND item_id = %s AND user_id = %s',
                        (int(uid in login), owner_id, item_id, uid))
    _bump_shared()
    return {'users': sorted(wanted), 'login': sorted(login)}


# ---------------------------------------------------------------- 老数据

SHARES_FLAG = 'card_shares_v2'


def migrate_shares() -> None:
    """上一版只有单点登录卡片能授权，关掉单点登录时授权行留着、卡片从对方页面上消失。
    这一版任何卡片都能分享，那些行原样留着的话，关过单点登录的卡片会**悄悄重新出现**
    在对方页面上——管理员当初关它多半就是不想让人再用。所以升上来时收一次：
    卡片还开着单点登录的照旧，关了的、卡片已经删了的，分享行连同对方的罐子一起收掉。
    收过就不再收（`portal_meta` 里记一笔），之后关单点登录和分享就没关系了。
    """
    if db.meta_get(SHARES_FLAG):
        return
    with db.cursor() as cur:
        # 一张卡片分享给几个人就有几行，去重放在下面做：extra 是 JSON 列，不拿它 DISTINCT
        cur.execute('SELECT g.owner_id, g.item_id, i.extra FROM card_grants g '
                    'LEFT JOIN nav_items i ON i.user_id = g.owner_id AND i.client_id = g.item_id')
        rows = cur.fetchall()
    stale = set()
    for row in rows:
        extra = db.loads(row.get('extra'))
        if not (isinstance(extra, dict) and extra.get('sso')):
            stale.add((int(row['owner_id']), row['item_id']))
    for owner_id, item_id in stale:
        set_shares(owner_id, item_id, [])
    dropped = len(stale)
    db.meta_set(SHARES_FLAG, str(int(time.time())))
    if dropped:
        print(f'[导航] {dropped} 张卡片的老授权已收掉（单点登录关着或卡片已删，'
              f'升级前对方本来就看不到它们）')


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
