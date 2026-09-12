"""SQLite 存储：用户、会话、一次性票据、会话参与过的业务系统。"""
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .config import DB_PATH, SESSION_TTL_HOURS, TICKET_TTL_SECONDS
from .security import hash_password, new_token, token_fingerprint, verify_password

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    password     TEXT    NOT NULL,
    display_name TEXT    NOT NULL DEFAULT '',
    email        TEXT    NOT NULL DEFAULT '',
    roles        TEXT    NOT NULL DEFAULT '',
    disabled     INTEGER NOT NULL DEFAULT 0,
    created_at   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    fingerprint TEXT PRIMARY KEY,
    sid         TEXT NOT NULL UNIQUE,
    user_id     INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    user_agent  TEXT NOT NULL DEFAULT '',
    ip          TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);

CREATE TABLE IF NOT EXISTS tickets (
    fingerprint  TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    user_id      INTEGER NOT NULL,
    session_sid  TEXT NOT NULL,
    expires_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS session_clients (
    session_sid TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    granted_at  REAL NOT NULL,
    PRIMARY KEY (session_sid, client_id)
);
"""

# 用户不存在时也跑一遍同样开销的哈希，免得能用响应时间探测账号是否存在
_DUMMY_HASH = hash_password('__no_such_user__')


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def _row_to_user(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {
        'id': row['id'],
        'username': row['username'],
        'display_name': row['display_name'] or row['username'],
        'email': row['email'],
        'roles': [r for r in (row['roles'] or '').split(',') if r],
        'disabled': bool(row['disabled']),
    }


# ---------------------------------------------------------------- 用户

def create_user(username: str, password: str, display_name: str = '',
                email: str = '', roles: str = '') -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            'INSERT INTO users (username, password, display_name, email, roles, created_at)'
            ' VALUES (?, ?, ?, ?, ?, ?)',
            (username.strip(), hash_password(password), display_name.strip(),
             email.strip(), roles.strip(), time.time()),
        )
        row = conn.execute('SELECT * FROM users WHERE id = ?', (cur.lastrowid,)).fetchone()
    return _row_to_user(row)


def get_user(username: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_user(
            conn.execute('SELECT * FROM users WHERE username = ?', (username.strip(),)).fetchone()
        )


def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute('SELECT * FROM users ORDER BY id').fetchall()
    return [_row_to_user(r) for r in rows]


def set_password(username: str, password: str) -> bool:
    with connect() as conn:
        cur = conn.execute('UPDATE users SET password = ? WHERE username = ?',
                           (hash_password(password), username.strip()))
        return cur.rowcount > 0


def set_disabled(username: str, disabled: bool) -> bool:
    with connect() as conn:
        cur = conn.execute('UPDATE users SET disabled = ? WHERE username = ?',
                           (1 if disabled else 0, username.strip()))
        if cur.rowcount and disabled:
            conn.execute('DELETE FROM sessions WHERE user_id IN'
                         ' (SELECT id FROM users WHERE username = ?)', (username.strip(),))
        return cur.rowcount > 0


def delete_user(username: str) -> bool:
    with connect() as conn:
        cur = conn.execute('DELETE FROM users WHERE username = ?', (username.strip(),))
        return cur.rowcount > 0


def check_login(username: str, password: str) -> Optional[dict[str, Any]]:
    """用户名错、口令错、账号停用都返回 None，不区分，免得被拿来枚举账号。"""
    with connect() as conn:
        row = conn.execute('SELECT * FROM users WHERE username = ?',
                           (username.strip(),)).fetchone()
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if row['disabled'] or not verify_password(password, row['password']):
        return None
    return _row_to_user(row)


# ---------------------------------------------------------------- 会话

def create_session(user_id: int, user_agent: str = '', ip: str = '') -> tuple[str, str, float]:
    """返回 (放进 Cookie 的令牌, 对外会话号 sid, 过期时间戳)。"""
    token, sid = new_token(), 's-' + new_token(18)
    now = time.time()
    expires_at = now + SESSION_TTL_HOURS * 3600
    with connect() as conn:
        conn.execute(
            'INSERT INTO sessions (fingerprint, sid, user_id, created_at, expires_at, user_agent, ip)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?)',
            (token_fingerprint(token), sid, user_id, now, expires_at, user_agent[:300], ip[:64]),
        )
    return token, sid, expires_at


def get_session(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    fp = token_fingerprint(token)
    with connect() as conn:
        row = conn.execute(
            'SELECT s.sid AS sid, s.expires_at AS expires_at, u.* FROM sessions s'
            ' JOIN users u ON u.id = s.user_id WHERE s.fingerprint = ?', (fp,)).fetchone()
        if row is None:
            return None
        if row['expires_at'] < time.time() or row['disabled']:
            conn.execute('DELETE FROM sessions WHERE fingerprint = ?', (fp,))
            return None
    return {'sid': row['sid'], 'expires_at': row['expires_at'], 'user': _row_to_user(row)}


def drop_session(token: str) -> Optional[str]:
    """删会话，返回它的 sid，交给上层去通知各业务系统。"""
    if not token:
        return None
    fp = token_fingerprint(token)
    with connect() as conn:
        row = conn.execute('SELECT sid FROM sessions WHERE fingerprint = ?', (fp,)).fetchone()
        if row is None:
            return None
        conn.execute('DELETE FROM sessions WHERE fingerprint = ?', (fp,))
        conn.execute('DELETE FROM tickets WHERE session_sid = ?', (row['sid'],))
    return row['sid']


# ---------------------------------------------------------------- 一次性票据

def issue_ticket(client_id: str, redirect_uri: str, user_id: int, session_sid: str) -> str:
    ticket = 'ST-' + new_token()
    with connect() as conn:
        conn.execute(
            'INSERT INTO tickets (fingerprint, client_id, redirect_uri, user_id, session_sid, expires_at)'
            ' VALUES (?, ?, ?, ?, ?, ?)',
            (token_fingerprint(ticket), client_id, redirect_uri, user_id, session_sid,
             time.time() + TICKET_TTL_SECONDS),
        )
    return ticket


def consume_ticket(ticket: str, client_id: str) -> Optional[dict[str, Any]]:
    """一次性：不管校验过不过都先删票，重放拿不到第二次。"""
    if not ticket:
        return None
    fp = token_fingerprint(ticket)
    with connect() as conn:
        row = conn.execute('SELECT * FROM tickets WHERE fingerprint = ?', (fp,)).fetchone()
        conn.execute('DELETE FROM tickets WHERE fingerprint = ?', (fp,))
        if row is None or row['client_id'] != client_id or row['expires_at'] < time.time():
            return None
        user = _row_to_user(
            conn.execute('SELECT * FROM users WHERE id = ?', (row['user_id'],)).fetchone())
        if user is None or user['disabled']:
            return None
        session = conn.execute('SELECT expires_at FROM sessions WHERE sid = ?',
                               (row['session_sid'],)).fetchone()
        if session is None or session['expires_at'] < time.time():
            return None
        conn.execute('INSERT OR REPLACE INTO session_clients (session_sid, client_id, granted_at)'
                     ' VALUES (?, ?, ?)', (row['session_sid'], client_id, time.time()))
    return {'user': user, 'session_sid': row['session_sid'],
            'session_expires_at': session['expires_at'], 'redirect_uri': row['redirect_uri']}


def clients_of_session(sid: str) -> list[str]:
    """取出并清空这个会话登过的业务系统列表。"""
    with connect() as conn:
        rows = conn.execute('SELECT client_id FROM session_clients WHERE session_sid = ?',
                            (sid,)).fetchall()
        conn.execute('DELETE FROM session_clients WHERE session_sid = ?', (sid,))
    return [r['client_id'] for r in rows]


def purge_expired() -> None:
    now = time.time()
    with connect() as conn:
        conn.execute('DELETE FROM tickets WHERE expires_at < ?', (now,))
        conn.execute('DELETE FROM sessions WHERE expires_at < ?', (now,))
        conn.execute(
            'DELETE FROM session_clients WHERE session_sid NOT IN (SELECT sid FROM sessions)')
