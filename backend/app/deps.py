"""会话 Cookie 的读写，以及登录失败限流。"""
import threading
import time
from typing import Any, Optional

from fastapi import Request, Response

from . import db
from .config import (COOKIE_SECURE, LOGIN_FAIL_WINDOW, LOGIN_MAX_FAILS,
                     SESSION_COOKIE, SESSION_TTL_HOURS)


def current_session(request: Request) -> Optional[dict[str, Any]]:
    return db.get_session(request.cookies.get(SESSION_COOKIE, ''))


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,          # JS 读不到，XSS 也偷不走
        secure=COOKIE_SECURE,   # https 部署时把 COOKIE_SECURE 打开
        samesite='lax',         # 够用：跳业务系统是顶级导航，Lax 会带上
        path='/',
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path='/')


def client_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else ''


# ---------------------------------------------------------------- 登录限流

_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _recent(key: str, now: float) -> list[float]:
    return [t for t in _fails.get(key, []) if now - t < LOGIN_FAIL_WINDOW]


def login_blocked(key: str) -> int:
    """返回还要等几秒；0 表示可以试。"""
    now = time.time()
    with _fails_lock:
        hits = _recent(key, now)
        _fails[key] = hits
        if len(hits) < LOGIN_MAX_FAILS:
            return 0
        return max(1, int(LOGIN_FAIL_WINDOW - (now - hits[0])))


def note_login_fail(key: str) -> None:
    now = time.time()
    with _fails_lock:
        _fails[key] = _recent(key, now) + [now]


def clear_login_fail(key: str) -> None:
    with _fails_lock:
        _fails.pop(key, None)
