"""会话 Cookie 的读写，以及登录失败限流。"""
import threading
import time
from typing import Any, Optional

from fastapi import HTTPException, Request, Response

from . import db, settings


def current_session(request: Request) -> Optional[dict[str, Any]]:
    return db.get_session(request.cookies.get(settings.session_cookie(), ''))


def require_admin(request: Request) -> dict[str, Any]:
    """管理端接口的唯一入口。roles 里有 admin 才放行。

    manage.py init 建的第一个账号就带 admin；后来用 adduser 建的默认没有，
    要显式 --roles admin。万一 admin 全没了，命令行那套始终还能用。
    """
    session = current_session(request)
    if session is None:
        raise HTTPException(401, '未登录')
    if 'admin' not in session['user']['roles']:
        raise HTTPException(403, '需要管理员权限')
    return session


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie(), token,
        max_age=settings.session_ttl_hours() * 3600,
        httponly=True,                     # JS 读不到，XSS 也偷不走
        secure=settings.cookie_secure(),   # https 部署时把 cookie_secure 打开
        samesite='lax',                    # 够用：跳业务系统是顶级导航，Lax 会带上
        path='/',
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.session_cookie(), path='/')


def client_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else ''


# ---------------------------------------------------------------- 登录限流

_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _recent(key: str, now: float, window: int) -> list[float]:
    return [t for t in _fails.get(key, []) if now - t < window]


def login_blocked(key: str) -> int:
    """返回还要等几秒；0 表示可以试。"""
    now = time.time()
    window, limit = settings.login_fail_window(), settings.login_max_fails()
    with _fails_lock:
        hits = _recent(key, now, window)
        _fails[key] = hits
        if len(hits) < limit:
            return 0
        return max(1, int(window - (now - hits[0])))


def note_login_fail(key: str) -> None:
    now = time.time()
    window = settings.login_fail_window()
    with _fails_lock:
        _fails[key] = _recent(key, now, window) + [now]


def clear_login_fail(key: str) -> None:
    with _fails_lock:
        _fails.pop(key, None)
