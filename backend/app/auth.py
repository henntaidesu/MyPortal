"""极简登录：一个账号，用户名和口令写在 conf.json 的 `auth` 那一段里。

**会话是一枚签名 Cookie，服务端什么都不存。** 没有会话表、没有内存字典，
所以进程重启不会把人踢下线，也不存在「会话攒了一堆要定期清」这回事。
Cookie 的内容就是 `<过期时间戳>.<签名>`，签名是 HMAC-SHA256。

签名密钥不是配置项，是**从口令算出来的**（scrypt）。这样有两个好处：

  - conf.json 里少一项要生成、要保管、要解释的东西；
  - 改口令即刻让所有已发出的 Cookie 失效——不然把口令改掉之后，
    拿着旧 Cookie 的人还能接着用，改口令就白改了。

为什么用 scrypt 而不是直接 sha256(口令)：口令是人挑的、熵很低。要是密钥能被快速
枚举，谁拿到一枚 Cookie 就能离线爆破出口令（服务端那道限流管不着离线爆破）。
scrypt 把每次尝试的成本抬到几十毫秒，离线爆破就和在线撞库一样慢了。
它只在进程启动时算一次。

登录失败限流是进程内内存：多 worker 起 uvicorn 会让它失效，目前设计就是单进程。
"""
import hashlib
import hmac
import threading
import time

from fastapi import HTTPException, Request, Response

from .config import AUTH_PASSWORD, AUTH_USERNAME, COOKIE_SECURE, SESSION_HOURS

COOKIE_NAME = 'portal_session'

# 固定盐。这里不需要「每个部署不一样」的盐：它防的是通用彩虹表，而 scrypt
# 的成本参数已经让预计算不划算了；用固定值换来的是「同一份 conf.json 拷到哪台
# 机器行为都一样」，不用再存一份随机盐。
_SALT = b'portal-session-v1'

# n=16384, r=8 需要 128*n*r = 16 MB 内存，在 hashlib.scrypt 默认的 32 MB 上限之内。
# 调大 n 之前先确认这一点，超了会直接抛 ValueError，表现成「服务起不来」。
_KEY = hashlib.scrypt(AUTH_PASSWORD.encode('utf-8'), salt=_SALT,
                      n=16384, r=8, p=1, dklen=32)

_TTL = SESSION_HOURS * 3600

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


# ---------------------------------------------------------------- 口令与票据

def check_credentials(username: str, password: str) -> bool:
    """两项都用 compare_digest 比，而且**不短路**：先算完再取与。

    写成 `a and b` 的话，用户名错时根本不会去比口令，两条路的耗时不一样，
    能被拿来探出用户名对不对。
    """
    ok_user = hmac.compare_digest(username.encode('utf-8'), AUTH_USERNAME.encode('utf-8'))
    ok_pass = hmac.compare_digest(password.encode('utf-8'), AUTH_PASSWORD.encode('utf-8'))
    return ok_user & ok_pass


def _sign(expires: int) -> str:
    return hmac.new(_KEY, str(expires).encode('ascii'), hashlib.sha256).hexdigest()


def make_token() -> str:
    expires = int(time.time()) + _TTL
    return f'{expires}.{_sign(expires)}'


def valid_token(token: str) -> bool:
    """Cookie 认不认。任何一步不对都返回 False，不区分原因。"""
    raw, _, sig = (token or '').partition('.')
    if not raw or not sig:
        return False
    try:
        expires = int(raw)
    except ValueError:
        return False
    if expires < time.time():
        return False
    return hmac.compare_digest(sig, _sign(expires))


# ---------------------------------------------------------------- 给路由用

def current_user(request: Request) -> str | None:
    """登录了返回用户名，没登录返回 None。"""
    return AUTH_USERNAME if valid_token(request.cookies.get(COOKIE_NAME, '')) else None


def require_login(request: Request) -> str:
    """需要登录的接口挂这个做依赖。这是仓库里唯一一处权限校验。"""
    user = current_user(request)
    if user is None:
        raise HTTPException(401, '未登录')
    return user


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=_TTL,
        httponly=True,            # JS 读不到，XSS 也偷不走
        secure=COOKIE_SECURE,     # https 部署时在 conf.json 里打开
        samesite='lax',
        path='/',
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path='/')
