"""业务系统注册表。

一个 client 就是一个「我自己写的系统」。注册表原来是 clients.json，现在在 MySQL 的
clients 表里，用 manage.py addclient / delclient 维护。

表里的 redirect_uris 是一行一个地址的纯文本，方便直接用客户端工具改；
改完不用重启认证中心，下面这个 5 秒的缓存到期就会重新读。
"""
import threading
import time
from typing import Any, Optional

from . import db
from .security import new_token

# 每个请求都要查 client，加一层很短的缓存挡住数据库；
# 代价是 manage.py 改完注册表，最多 5 秒后才在认证中心生效
_CACHE_TTL = 5.0

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}
_loaded_at: float = -1.0


def _split_uris(raw: str) -> list[str]:
    return [line.strip() for line in (raw or '').splitlines() if line.strip()]


def _row_to_client(row: dict) -> dict[str, Any]:
    return {
        'client_id': row['client_id'],
        'name': row['name'] or row['client_id'],
        'secret': row['secret'] or '',
        'redirect_uris': _split_uris(row['redirect_uris']),
        'logout_uri': (row['logout_uri'] or '').strip(),
        'home_url': (row['home_url'] or '').strip(),
    }


def load(force: bool = False) -> dict[str, dict[str, Any]]:
    global _cache, _loaded_at
    with _lock:
        if not force and time.time() - _loaded_at < _CACHE_TTL:
            return _cache
        try:
            with db.connect() as conn:
                rows = conn.execute('SELECT * FROM clients').fetchall()
        except Exception as exc:
            # 数据库临时抽风不要让已经在跑的服务崩掉，继续用上一次的结果
            print(f'[clients] 读注册表失败，沿用上一次的: {exc}')
            return _cache
        _cache = {r['client_id']: _row_to_client(r) for r in rows}
        _loaded_at = time.time()
        return _cache


def get(client_id: str) -> Optional[dict[str, Any]]:
    return load().get((client_id or '').strip())


def public_list() -> list[dict[str, str]]:
    """给前端下拉框用：只给 id 和名字，绝不带 secret。"""
    return [{'client_id': c['client_id'], 'name': c['name']}
            for c in sorted(load().values(), key=lambda c: c['name'])]


def check_redirect_uri(client: dict[str, Any], redirect_uri: str) -> Optional[str]:
    """精确匹配白名单。没传就用第一条注册地址。

    这是整套流程的安全底线：放松成前缀匹配或允许任意地址，
    票据就能被诱导送到别人的服务器上。
    """
    if not redirect_uri:
        return client['redirect_uris'][0] if client['redirect_uris'] else None
    return redirect_uri if redirect_uri in client['redirect_uris'] else None


# ---------------------------------------------------------------- manage.py 用

def upsert(client_id: str, name: str = '', secret: str = '', redirect_uris: list[str] = (),
           logout_uri: str = '', home_url: str = '') -> dict[str, Any]:
    """注册或覆盖一个业务系统，返回落库后的结果。secret 留空就随机生成一个。"""
    client_id = client_id.strip()
    secret = secret or new_token()
    uris = '\n'.join(u.strip() for u in redirect_uris if u.strip())
    now = time.time()
    with db.connect() as conn:
        conn.execute(
            'INSERT INTO clients'
            ' (client_id, name, secret, redirect_uris, logout_uri, home_url, created_at, updated_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE name = %s, secret = %s, redirect_uris = %s,'
            ' logout_uri = %s, home_url = %s, updated_at = %s',
            (client_id, name or client_id, secret, uris, logout_uri.strip(), home_url.strip(),
             now, now,
             name or client_id, secret, uris, logout_uri.strip(), home_url.strip(), now),
        )
    load(force=True)
    return get(client_id)


def delete(client_id: str) -> bool:
    with db.connect() as conn:
        cur = conn.execute('DELETE FROM clients WHERE client_id = %s', (client_id.strip(),))
    load(force=True)
    return cur.rowcount > 0


def exists(client_id: str) -> bool:
    return (client_id or '').strip() in load(force=True)
