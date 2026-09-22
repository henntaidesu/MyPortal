"""Cookie 代理：把上游站点种的 Cookie 存在服务器上，替浏览器记住外部站点的登录态。

卡片上把「Cookie 代理」打开之后，走这张卡片转发的请求就有了一个**服务器端的
Cookie 罐子**：

    上游 Set-Cookie ──> 存进 proxy_cookies（加密）──> 也照常交给浏览器
    发给上游的请求  <── 从罐子里按 RFC 6265 挑出该带的那几枚

这解决的是浏览器那份靠不住：清一次浏览器数据就全没了、换台设备要重登、
Safari 那类浏览器还会把脚本种的 Cookie 压到 7 天。罐子在服务器上，
所以**换设备、换浏览器、清缓存都不掉登录**。

## 一张卡片一个罐子，一个人一份

主键是 `(用户, 卡片)`。两张卡片指同一个站算两个罐子，各自登一次——
按域名合并的话，「我的号」和「公司的号」两张卡片会互相把对方顶掉，
而这正是有人开两张卡片的原因。

## 服务器那份权威，但仍然下发给浏览器

发给上游的 `Cookie` 头以罐子为准（[proxy.py](proxy.py) 的 `_request_headers`）；
上游的 `Set-Cookie` 既存进罐子，也照常改写后交给浏览器。都给的原因是页面里有不少
脚本要读 `document.cookie` 判断自己登没登——不给的话它们会一直以为没登录。
两边对不上时**以罐子为准**：浏览器那份可能是上次清数据前的陈货。

## 存的是凭证，不是缓存

值一律加密（[secretbox.py](secretbox.py)，AES-GCM，密钥从库里那把签名密钥派生）。
解不开的当作没有，不报错——换过签名密钥、或者库被改过时，最坏也就是让人重登一次。

**但加密挡不住「门户进程自己」**：它必须能解开才能发给上游。所以这个功能的
安全边界仍然是「谁能登进这个门户账号，谁就能以你的身份用那些外部站点」。
开之前想清楚这一条。
"""
import hashlib
import time
from email.utils import parsedate_to_datetime
from typing import NamedTuple, Optional

from . import db, secretbox

# 一张卡片最多存这么多枚。浏览器对单个域的上限一般是 180 上下，取 200 够用。
# 不设上限的话，一个每次响应都换 Cookie 名的站点能把这张表撑爆
MAX_PER_CARD = 200

# 没有过期时间的（会话 Cookie）在罐子里能放多久。浏览器关掉就丢，而罐子的意义
# 正是替它记住，所以不能跟着关页面清掉；但也不能永远留着，不然这张表只增不减。
# 从最后一次被上游更新算起
SESSION_TTL = 90 * 86400

_EXPIRED = -1            # 上游用一条过去的过期时间来删 Cookie，内部用这个值表示


class Cookie(NamedTuple):
    name: str
    value: str
    domain: str          # 一律不带前导点，小写
    host_only: bool      # Set-Cookie 里没写 Domain = 只发给这一台主机
    path: str
    expires: Optional[int]   # None = 会话 Cookie
    secure: bool
    http_only: bool
    same_site: str

    @property
    def scope(self) -> bytes:
        """(域, 路径, 名字) 的指纹，当唯一键用。

        不直接把三样拼进唯一索引：utf8mb4 下 `域(255) + 路径(255) + 名字(255)`
        要 3060 字节，加上前面两列就超过 InnoDB 那 3072 字节的索引上限了。
        """
        raw = f'{self.domain}\0{self.path}\0{self.name}'.encode('utf-8')
        return hashlib.sha256(raw).digest()


# ---------------------------------------------------------------- 解析

def _default_path(request_path: str) -> str:
    """RFC 6265 5.1.4。Set-Cookie 没写 Path 时用的那个默认值。

    是「请求路径去掉最后一段」，不是请求路径本身：`/a/b` 上种的 Cookie
    默认作用于 `/a/`，照搬 `/a/b` 的话 `/a/c` 就带不上了。
    """
    if not request_path.startswith('/'):
        return '/'
    cut = request_path.rfind('/')
    return request_path[:cut] if cut > 0 else '/'


def _expiry(attrs: dict) -> Optional[int]:
    """Max-Age 优先于 Expires（RFC 6265 5.2.2 明说了）。"""
    raw_age = attrs.get('max-age')
    if raw_age is not None:
        try:
            age = int(raw_age.strip())
        except ValueError:
            return None
        return _EXPIRED if age <= 0 else int(time.time()) + age

    raw_exp = attrs.get('expires')
    if raw_exp:
        try:
            when = parsedate_to_datetime(raw_exp.strip())
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        ts = int(when.timestamp()) if when.tzinfo else int(when.timestamp())
        return ts if ts > time.time() else _EXPIRED
    return None


def parse_set_cookie(header: str, request_host: str, request_path: str) -> Optional[Cookie]:
    """一条 `Set-Cookie` → 一枚 Cookie。不合法返回 None。

    `expires` 是 `_EXPIRED` 时表示「上游要删掉这枚」，由 `store` 翻译成一条 DELETE。
    """
    head, _, tail = header.partition(';')
    if '=' not in head:
        # `Set-Cookie: flag` 这种没有等号的，浏览器也是直接忽略
        return None
    name, _, value = head.partition('=')
    name, value = name.strip(), value.strip()
    if not name:
        return None

    attrs: dict[str, str] = {}
    flags: set[str] = set()
    for part in tail.split(';'):
        part = part.strip()
        if not part:
            continue
        key, sep, val = part.partition('=')
        key = key.strip().lower()
        if sep:
            attrs[key] = val
        else:
            flags.add(key)

    host = (request_host.split(':', 1)[0] or '').lower().rstrip('.')

    raw_domain = (attrs.get('domain') or '').strip().lower().lstrip('.').rstrip('.')
    if raw_domain:
        # RFC 6265 5.3 第 6 步：Domain 必须是请求主机的后缀，否则整枚丢掉。
        # 不拦的话，代理底下任意一个上游都能给同族的别的站种 Cookie
        if host != raw_domain and not host.endswith('.' + raw_domain):
            return None
        # 单标签的域（`Domain=com`、`Domain=jp`）一律拒。没有公共后缀表可查，
        # 这一条挡掉的是最粗暴的那种「给整个 TLD 种 Cookie」
        if '.' not in raw_domain:
            return None
        domain, host_only = raw_domain, False
    else:
        domain, host_only = host, True
    if not domain:
        return None

    path = (attrs.get('path') or '').strip()
    if not path.startswith('/'):
        path = _default_path(request_path)

    same = (attrs.get('samesite') or '').strip().lower()
    return Cookie(
        name=name[:255], value=value, domain=domain[:255], host_only=host_only,
        path=path[:255], expires=_expiry(attrs),
        secure='secure' in flags, http_only='httponly' in flags,
        same_site=same[:8] if same in ('lax', 'strict', 'none') else '',
    )


# ---------------------------------------------------------------- 匹配

def _domain_match(cookie: Cookie, host: str) -> bool:
    if cookie.host_only:
        return host == cookie.domain
    return host == cookie.domain or host.endswith('.' + cookie.domain)


def _path_match(cookie_path: str, request_path: str) -> bool:
    """RFC 6265 5.1.4。`/a` 匹配 `/a`、`/a/b`，但**不匹配** `/ab`。"""
    if cookie_path == request_path:
        return True
    if not request_path.startswith(cookie_path):
        return False
    return cookie_path.endswith('/') or request_path[len(cookie_path):].startswith('/')


def header_for(cookies: list[Cookie], scheme: str, host: str, path: str) -> str:
    """挑出该发给 `scheme://host/path` 的那几枚，拼成一个 `Cookie` 头。

    排序按 RFC 6265 5.4：**路径长的在前**。上游解析时一般取同名的第一枚，
    顺序反了的话 `/` 上那枚泛用的会把 `/admin` 上那枚精确的顶掉。
    """
    now = time.time()
    bare = (host.split(':', 1)[0] or '').lower().rstrip('.')
    request_path = path if path.startswith('/') else '/' + path

    hit = [c for c in cookies
           if (c.expires is None or c.expires > now)
           and (scheme == 'https' or not c.secure)
           and _domain_match(c, bare)
           and _path_match(c.path, request_path)]
    hit.sort(key=lambda c: len(c.path), reverse=True)
    return '; '.join(f'{c.name}={c.value}' for c in hit)


# ---------------------------------------------------------------- 落库

def _aad(user_id: int, item_id: str, name: str) -> bytes:
    """把密文钉在 (用户, 卡片, Cookie 名) 上，见 secretbox.py 顶上那段。"""
    return f'{user_id}:{item_id}:{name}'.encode('utf-8')


def load(user_id: int, item_id: str) -> list[Cookie]:
    """这张卡片罐子里还没过期的那些。解不开的直接跳过。"""
    if not secretbox.available():
        return []
    now = int(time.time())
    with db.cursor() as cur:
        cur.execute(
            'SELECT name, value, domain, host_only, path, expires_at, '
            'secure, http_only, same_site FROM proxy_cookies '
            'WHERE user_id = %s AND item_id = %s '
            'AND (expires_at IS NULL OR expires_at > %s)',
            (user_id, item_id, now))
        rows = cur.fetchall()

    out = []
    for row in rows:
        value = secretbox.open_box(row['value'], _aad(user_id, item_id, row['name']))
        if value is None:
            continue           # 换过密钥、或者被改过。当作没有，让人重登一次
        out.append(Cookie(
            name=row['name'], value=value, domain=row['domain'],
            host_only=bool(row['host_only']), path=row['path'],
            expires=row['expires_at'], secure=bool(row['secure']),
            http_only=bool(row['http_only']), same_site=row['same_site'] or ''))
    return out


def store(user_id: int, item_id: str, headers: list[str],
          request_host: str, request_path: str) -> int:
    """把一次响应里的 `Set-Cookie` 收进罐子。返回落下的条数。

    上游用「过期时间在过去」来删 Cookie，这里翻译成一条 DELETE——不翻译的话
    退出登录之后罐子里还留着旧会话，下次进来会带着一枚已经作废的票，
    有些站点会因此卡在一个半登录的状态里。
    """
    if not secretbox.available() or not headers:
        return 0

    saved, dropped = [], []
    for raw in headers:
        cookie = parse_set_cookie(raw, request_host, request_path)
        if cookie is None:
            continue
        (dropped if cookie.expires == _EXPIRED else saved).append(cookie)
    if not saved and not dropped:
        return 0

    with db.cursor(write=True) as cur:
        for cookie in dropped:
            cur.execute('DELETE FROM proxy_cookies WHERE user_id = %s AND item_id = %s '
                        'AND scope_hash = %s', (user_id, item_id, cookie.scope))
        for cookie in saved:
            cur.execute(
                'INSERT INTO proxy_cookies (user_id, item_id, scope_hash, name, value, '
                'domain, host_only, path, expires_at, secure, http_only, same_site) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
                'ON DUPLICATE KEY UPDATE value = VALUES(value), '
                'expires_at = VALUES(expires_at), secure = VALUES(secure), '
                'http_only = VALUES(http_only), same_site = VALUES(same_site), '
                'updated_at = CURRENT_TIMESTAMP',
                (user_id, item_id, cookie.scope, cookie.name,
                 secretbox.seal(cookie.value, _aad(user_id, item_id, cookie.name)),
                 cookie.domain, int(cookie.host_only), cookie.path,
                 cookie.expires, int(cookie.secure), int(cookie.http_only),
                 cookie.same_site))

        # 超了上限就把最久没更新的削掉。不删的话，一个每次响应都换 Cookie 名的
        # 站点能把这张表撑到几十万行
        cur.execute('SELECT COUNT(*) AS n FROM proxy_cookies '
                    'WHERE user_id = %s AND item_id = %s', (user_id, item_id))
        total = int(cur.fetchone()['n'])
        if total > MAX_PER_CARD:
            cur.execute(
                'DELETE FROM proxy_cookies WHERE user_id = %s AND item_id = %s '
                'ORDER BY updated_at ASC LIMIT %s',
                (user_id, item_id, total - MAX_PER_CARD))
    return len(saved)


def clear(user_id: int, item_id: str) -> int:
    """把这张卡片的罐子倒空 = 退出那个外部站点的登录。"""
    with db.cursor(write=True) as cur:
        cur.execute('DELETE FROM proxy_cookies WHERE user_id = %s AND item_id = %s',
                    (user_id, item_id))
        return cur.rowcount


def retain(cur, user_id: int, keep: set) -> int:
    """只留下 `keep` 里那些卡片的罐子，其余删掉。**必须在存导航那个事务里调**
    （所以接的是调用方的游标，不是自己开一个）。

    不做这一步的话有两个泄漏：

      - 卡片删了，它的登录数据还躺在库里。`item_id` 存的是前端那个 client_id，
        没法挂外键指向 nav_items（存一次导航是「整棵树删掉重插」，自增主键每次都变，
        挂外键就等于每存一次导航清一次登录态），所以只能在这儿自己收。
      - 把卡片上的「Cookie 代理」关掉，本意就是「别再替我记着那边的登录」，
        结果罐子还在，重新打开时又直接是登录状态——那个开关就成了摆设。

    所以 `keep` 传的是「**开着 Cookie 代理的**卡片」，不是「所有卡片」。
    """
    if keep:
        marks = ', '.join(['%s'] * len(keep))
        cur.execute(f'DELETE FROM proxy_cookies WHERE user_id = %s '
                    f'AND item_id NOT IN ({marks})', (user_id, *keep))
    else:
        cur.execute('DELETE FROM proxy_cookies WHERE user_id = %s', (user_id,))
    return cur.rowcount


def clear_user(user_id: int) -> int:
    with db.cursor(write=True) as cur:
        cur.execute('DELETE FROM proxy_cookies WHERE user_id = %s', (user_id,))
        return cur.rowcount


def stats(user_id: int, item_id: str) -> dict:
    """给卡片编辑框显示「已保存 N 条」用。**不返回任何 Cookie 的值。**"""
    now = int(time.time())
    with db.cursor() as cur:
        cur.execute(
            'SELECT COUNT(*) AS n, MAX(UNIX_TIMESTAMP(updated_at)) AS ts '
            'FROM proxy_cookies WHERE user_id = %s AND item_id = %s '
            'AND (expires_at IS NULL OR expires_at > %s)',
            (user_id, item_id, now))
        row = cur.fetchone() or {}
        cur.execute(
            'SELECT DISTINCT domain FROM proxy_cookies WHERE user_id = %s AND item_id = %s '
            'AND (expires_at IS NULL OR expires_at > %s) ORDER BY domain LIMIT 20',
            (user_id, item_id, now))
        domains = [r['domain'] for r in cur.fetchall()]
    return {'count': int(row.get('n') or 0),
            'updated_at': int(row.get('ts') or 0),
            'domains': domains}


def purge() -> int:
    """过期的、以及太久没动过的会话 Cookie。和图标缓存一样每小时跑一趟。"""
    now = int(time.time())
    with db.cursor(write=True) as cur:
        cur.execute('DELETE FROM proxy_cookies WHERE expires_at IS NOT NULL '
                    'AND expires_at < %s', (now,))
        gone = cur.rowcount
        cur.execute('DELETE FROM proxy_cookies WHERE expires_at IS NULL '
                    'AND updated_at < FROM_UNIXTIME(%s)', (now - SESSION_TTL,))
        return gone + cur.rowcount


def describe() -> str:
    """给启动日志用的一句话。"""
    if not secretbox.available():
        return secretbox.why_unavailable()
    with db.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS n, COUNT(DISTINCT CONCAT(user_id, %s, item_id)) AS c '
                    'FROM proxy_cookies', ('\0',))
        row = cur.fetchone()
    if not row or not row['n']:
        return '没有卡片存过登录数据（卡片编辑框里「Cookie 代理」那一栏选「开启」）'
    return f'{row["c"]} 张卡片存了 {row["n"]} 条登录数据'
