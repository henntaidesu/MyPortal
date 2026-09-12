"""运行期配置。存在数据库的 settings 表里，改完不用重启。

conf.ini 里只留了连数据库和起服务必须的那几项——它们要在连上数据库之前就用到，
没法自举。除此之外的配置全在这张表里，三个入口都能改：
`manage.py settings` / `manage.py set`，以及门户页面上管理员可见的「系统设置」。

**取值不要放在 `with db.connect()` 里面。** 这里的读取自己要占一条池化连接，
在已经持有连接的情况下再调，池子满的时候会互相等死。调用方一律先取值再开连接。
"""
import re
import threading
import time
from typing import Any, NamedTuple


class Spec(NamedTuple):
    default: Any
    note: str
    kind: str                 # int / bool / text，决定怎么校验、页面上画什么控件
    low: int = 0              # kind='int' 时的下限（含）
    high: int = 0             # kind='int' 时的上限（含）
    caution: str = ''         # 改动的副作用，设置页会标出来


# 这份清单就是全部可配项。init_db 会把它补进表里，
# 所以 manage.py settings 和设置页看到的是完整清单而不是一张空表。
DEFAULTS: dict[str, Spec] = {
    'session_ttl_hours': Spec(
        12, '门户会话有效期（小时）', 'int', 1, 720),
    'session_cookie_name': Spec(
        'sso_session', '会话 Cookie 的名字', 'text',
        caution='改完所有人当场掉线，得重新登录一次'),
    'cookie_secure': Spec(
        False, '整站走 https 时打开', 'bool',
        caution='http 环境下打开会让浏览器直接丢掉登录 Cookie，表现成谁都登不上'),
    'ticket_ttl_seconds': Spec(
        60, '一次性票据有效期（秒），够浏览器跳一次就行，别调大', 'int', 10, 600),
    'logout_notify_timeout': Spec(
        3, '通知业务系统「用户登出了」的请求超时（秒）', 'int', 1, 30),
    'login_max_fails': Spec(
        5, '同一用户名在计数窗口内最多失败几次', 'int', 1, 100),
    'login_fail_window': Spec(
        300, '登录失败计数窗口（秒）', 'int', 10, 86400),
}

# Cookie 名只能用这些字符，别的会被浏览器直接拒收
_COOKIE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

_TRUE = ('1', 'true', 'yes', 'on')
_FALSE = ('0', 'false', 'no', 'off')

# 每次取值都查库太浪费，加一层很短的缓存；
# 代价是改完最多 5 秒后生效，换来的是不用重启
_CACHE_TTL = 5.0

_lock = threading.Lock()
_cache: dict[str, str] = {}
_loaded_at: float = -1.0


def as_text(value: Any) -> str:
    """入库一律存字符串，布尔值统一成 true/false。"""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def validate(name: str, raw: str) -> str:
    """校验并规范化一项配置的值，返回该入库的字符串；不合法抛 ValueError。

    值是从页面和命令行进来的，这里是唯一的关口。像 session_ttl_hours=0
    或者一个浏览器不收的 Cookie 名，都能把所有人挡在门外。
    """
    spec = DEFAULTS.get(name)
    if spec is None:
        raise ValueError(f'没有这项配置：{name}')
    raw = (raw or '').strip()

    if spec.kind == 'int':
        try:
            number = int(raw)
        except ValueError:
            raise ValueError(f'{name} 要填整数，收到 {raw!r}') from None
        if not (spec.low <= number <= spec.high):
            raise ValueError(f'{name} 要在 {spec.low} 到 {spec.high} 之间，收到 {number}')
        return str(number)

    if spec.kind == 'bool':
        low = raw.lower()
        if low in _TRUE:
            return 'true'
        if low in _FALSE:
            return 'false'
        raise ValueError(f'{name} 只能是 true 或 false，收到 {raw!r}')

    if name == 'session_cookie_name':
        if not _COOKIE_NAME_RE.match(raw):
            raise ValueError('Cookie 名只能用字母、数字、下划线和连字符，长度 1~64')
        return raw

    if not raw:
        raise ValueError(f'{name} 不能留空')
    return raw


def load(force: bool = False) -> dict[str, str]:
    global _cache, _loaded_at
    with _lock:
        if not force and time.time() - _loaded_at < _CACHE_TTL:
            return _cache
        # 延迟导入：db 模块要用这里的取值，写在模块顶上会成环
        from . import db
        try:
            with db.connect() as conn:
                rows = conn.execute('SELECT name, value FROM settings').fetchall()
        except Exception as exc:
            # 数据库临时抽风不要让已经在跑的服务崩掉，继续用上一次的结果
            print(f'[settings] 读配置失败，沿用上一次的: {exc}')
            return _cache
        _cache = {r['name']: r['value'] for r in rows}
        _loaded_at = time.time()
        return _cache


def _raw(name: str) -> str:
    value = load().get(name)
    if value is None:
        return as_text(DEFAULTS[name].default)   # 表里还没这一行，用默认值
    return value


def _int(name: str) -> int:
    try:
        return int(_raw(name))
    except (TypeError, ValueError):
        return int(DEFAULTS[name].default)       # 被人绕过校验改坏了，退回默认值


def _bool(name: str) -> bool:
    return _raw(name).strip().lower() in _TRUE


# ---------------------------------------------------------------- 取值

def session_ttl_hours() -> int:
    return _int('session_ttl_hours')


def session_cookie() -> str:
    return _raw('session_cookie_name') or 'sso_session'


def cookie_secure() -> bool:
    return _bool('cookie_secure')


def ticket_ttl_seconds() -> int:
    return _int('ticket_ttl_seconds')


def logout_notify_timeout() -> int:
    return _int('logout_notify_timeout')


def login_max_fails() -> int:
    return _int('login_max_fails')


def login_fail_window() -> int:
    return _int('login_fail_window')


# ---------------------------------------------------------------- 管理入口

def all_items() -> list[dict[str, Any]]:
    """列出全部配置项，给 manage.py settings 和设置页用。"""
    current = load(force=True)
    return [{'name': name,
             'value': current.get(name, as_text(spec.default)),
             'default': as_text(spec.default),
             'note': spec.note,
             'kind': spec.kind,
             'low': spec.low,
             'high': spec.high,
             'caution': spec.caution,
             'changed': current.get(name, as_text(spec.default)) != as_text(spec.default)}
            for name, spec in DEFAULTS.items()]


def put(name: str, raw: str) -> str:
    """改一项配置，返回规范化后入库的值。名字或值不合法时抛 ValueError。"""
    value = validate(name, raw)
    from . import db
    now = time.time()
    with db.connect() as conn:
        conn.execute(
            'INSERT INTO settings (name, value, note, updated_at) VALUES (%s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE value = %s, updated_at = %s',
            (name, value, DEFAULTS[name].note, now, value, now),
        )
    load(force=True)
    return value
