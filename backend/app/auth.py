"""登录：多用户，本地口令 + OIDC 单点登录，会话仍然是一枚签名 Cookie。

**服务端不存会话**，没有会话表、没有内存字典，所以重启不掉线，也不存在
「会话攒一堆要清」这回事。Cookie 的内容是：

    <用户 id>.<token_version>.<过期时间戳>.<HMAC-SHA256 签名>

签名密钥是随机生成的、存在库里（`db.session_secret`），不是配置项——让人往
conf.json 里填一串随机数多半会被填成 `secret`，而多实例部署时两边填得不一样，
表现成「刷新一下就掉线」。

`token_version` 是这一版新加的那一格，**它替掉了上一版「密钥从口令 scrypt 出来」
那个把戏**。上一版只有一个账号，改口令换密钥就能让所有 Cookie 失效；多用户之后
一把密钥对不上 N 个口令，所以改口令 / 停用 / 踢下线时给那个人的 token_version +1，
只作废他一个人的票，别人不受影响（见 app/users.py 的 `set_password`）。

## 每个请求都要查一次库吗

要，但走的是主键。为了让打开一个被代理的页面（一口气几十个子资源请求）不至于
把连接池占满，这里压了一层 10 秒的进程内缓存。代价是「停用某人」最多晚 10 秒
生效——同一个进程里改的会被 `forget()` 当场清掉，所以实际只在多实例部署时
才看得到这个延迟。**别把它调长**：这是权限撤销的生效上限。

登录失败限流是进程内内存：多 worker 起 uvicorn 会让它失效，目前设计就是单进程。
"""
import hmac
import hashlib
import threading
import time
from typing import Optional

from fastapi import HTTPException, Request, Response

from . import db, users
from .config import COOKIE_SECURE, SESSION_HOURS

COOKIE_NAME = 'portal_session'

_TTL = SESSION_HOURS * 3600

# 签名密钥。第一次用到时从库里取（或者生成一把存进去）——不在 import 时取，
# 那会儿 db.init() 还没跑过
_key: Optional[bytes] = None
_key_lock = threading.Lock()


def _secret() -> bytes:
    global _key
    if _key is None:
        with _key_lock:
            if _key is None:
                _key = db.session_secret()
    return _key


# ---------------------------------------------------------------- 用户缓存
_CACHE_TTL = 10.0
_cache: dict[int, tuple[float, Optional[dict]]] = {}
_cache_lock = threading.Lock()


def forget(user_id: Optional[int] = None) -> None:
    """把缓存里那份丢掉。改完口令、停用、改角色之后调一次，别等 10 秒。"""
    with _cache_lock:
        if user_id is None:
            _cache.clear()
        else:
            _cache.pop(int(user_id), None)


def _lookup(user_id: int) -> Optional[dict]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(user_id)
        if hit and hit[0] > now:
            return hit[1]
    row = users.by_id(user_id)
    with _cache_lock:
        _cache[user_id] = (now + _CACHE_TTL, row)
    return row


# ---------------------------------------------------------------- 限流
_MAX_FAILS = 10
_WINDOW = 300                  # 5 分钟内失败 10 次就先歇着

_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else ''


def _recent(key: str, now: float) -> list[float]:
    return [t for t in _fails.get(key, []) if now - t < _WINDOW]


def login_blocked(key: str) -> int:
    """返回还要等几秒；0 表示可以试。"""
    now = time.time()
    with _fails_lock:
        hits = _recent(key, now)
        _fails[key] = hits
        if len(hits) < _MAX_FAILS:
            return 0
        return max(1, int(_WINDOW - (now - hits[0])))


def note_fail(key: str) -> None:
    now = time.time()
    with _fails_lock:
        _fails[key] = _recent(key, now) + [now]


def clear_fails(key: str) -> None:
    with _fails_lock:
        _fails.pop(key, None)


# ---------------------------------------------------------------- 口令

def check_credentials(username: str, password: str) -> Optional[dict]:
    """对上了返回用户行，对不上返回 None。

    **用户不存在时也要走一遍 scrypt**（`users.check_password` 拿假参数照算），
    否则「这个用户名存不存在」能从响应时间上看出来。登录失败也不区分原因。
    """
    row = users.by_username((username or '').strip())
    ok = users.check_password(row.get('password_hash') if row else None, password or '')
    if not ok or row is None:
        return None
    if not row['is_active']:
        return None
    return row


# ---------------------------------------------------------------- 票据

def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode('ascii'), hashlib.sha256).hexdigest()


def make_token(user: dict) -> str:
    expires = int(time.time()) + _TTL
    payload = f'{int(user["id"])}.{int(user["token_version"])}.{expires}'
    return f'{payload}.{_sign(payload)}'


def user_from_token(token: str) -> Optional[dict]:
    """Cookie 认不认。认就返回用户行，不认返回 None，不区分原因。"""
    parts = (token or '').split('.')
    if len(parts) != 4:
        return None
    raw_id, raw_ver, raw_exp, sig = parts
    try:
        user_id, version, expires = int(raw_id), int(raw_ver), int(raw_exp)
    except ValueError:
        return None
    if expires < time.time():
        return None
    if not hmac.compare_digest(sig, _sign(f'{raw_id}.{raw_ver}.{raw_exp}')):
        return None

    row = _lookup(user_id)
    if row is None or not row['is_active']:
        return None
    # 改过口令、被停用过、被踢过下线的，票上那个版本号就对不上了
    if int(row['token_version']) != version:
        return None
    return row


# ---------------------------------------------------------------- 给路由用

def current_user(request: Request) -> Optional[dict]:
    """登录了返回用户行，没登录返回 None。"""
    return user_from_token(request.cookies.get(COOKIE_NAME, ''))


def require_login(request: Request) -> dict:
    """需要登录的接口挂这个做依赖，拿到的是当前用户那一行。

    **每个按用户取数据的地方都从这里拿 user_id**，别从请求体里读——
    读请求体等于让调用方自己说他是谁。
    """
    user = current_user(request)
    if user is None:
        raise HTTPException(401, '未登录')
    return user


def require_admin(request: Request) -> dict:
    """管理员才能进的接口挂这个（用户管理那几条）。"""
    user = require_login(request)
    if (user.get('role') or 'user') != 'admin':
        raise HTTPException(403, '需要管理员权限')
    return user


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=_TTL,
        httponly=True,            # JS 读不到，XSS 也偷不走
        secure=COOKIE_SECURE,     # https 部署时在 conf.json 里打开
        samesite='lax',           # OIDC 回调是从 IdP 跳回来的顶层导航，lax 放得过
        path='/',
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path='/')
