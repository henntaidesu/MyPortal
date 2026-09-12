"""MySQL 存储：用户、会话、一次性票据、会话登过的业务系统、业务系统注册表。

对外的函数签名和原来那版 SQLite 完全一致，所以路由层不用跟着改。
两处和 SQLite 不一样、值得记住的地方：

1. PyMySQL 不带连接池，这里放了一个很小的 LIFO 池。取连接时如果它闲置超过
   ping_interval 就先 ping 一下：MySQL 默认 wait_timeout 8 小时会掐掉闲置连接，
   不 ping 的话夜里没人访问、第二天第一个请求必报 MySQL server has gone away。
2. 占位符是 %s 不是 ?；用户名大小写不敏感靠的是 utf8mb4_general_ci 这个排序规则
   （原来 SQLite 那版靠 COLLATE NOCASE），建表语句里别顺手改成 _bin 或 _cs。
"""
import queue
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

import pymysql
import pymysql.cursors

from . import settings
from .config import (CONF_PATH, DB_CHARSET, DB_HOST, DB_NAME, DB_PASSWORD,
                     DB_PING_INTERVAL, DB_POOL_SIZE, DB_PORT, DB_USER)
from .security import hash_password, new_token, token_fingerprint, verify_password

# manage.py 靠这个判断「用户名重复」，省得它自己去 import pymysql
IntegrityError = pymysql.err.IntegrityError


class DatabaseUnavailable(RuntimeError):
    """连不上 MySQL。单独一个类型，好让启动时给一段人话提示而不是甩 traceback。"""


# 建库和建表用同一组字符集/排序规则。别改成 _bin 或 _cs，
# 用户名的大小写不敏感就是靠这个 _ci 排序规则来的
_CHARSET_DDL = 'utf8mb4'
_COLLATION_DDL = 'utf8mb4_general_ci'
_TABLE_SUFFIX = f'ENGINE=InnoDB DEFAULT CHARSET={_CHARSET_DDL} COLLATE={_COLLATION_DDL}'

# 库名要拼进 CREATE DATABASE（标识符不能用占位符参数化），只放行最保守的字符，
# 挡住用反引号逃出来注入任意 DDL 的写法
_DB_NAME_RE = re.compile(r'^[A-Za-z0-9_]{1,64}$')

SCHEMA = (
    f"""
    CREATE TABLE IF NOT EXISTS users (
        id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        username     VARCHAR(64)     NOT NULL,
        password     VARCHAR(255)    NOT NULL,
        display_name VARCHAR(128)    NOT NULL DEFAULT '',
        email        VARCHAR(191)    NOT NULL DEFAULT '',
        roles        VARCHAR(255)    NOT NULL DEFAULT '',
        disabled     TINYINT(1)      NOT NULL DEFAULT 0,
        created_at   DOUBLE          NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uk_users_username (username)
    ) {_TABLE_SUFFIX}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS sessions (
        fingerprint VARCHAR(64)     NOT NULL,
        sid         VARCHAR(64)     NOT NULL,
        user_id     BIGINT UNSIGNED NOT NULL,
        created_at  DOUBLE          NOT NULL,
        expires_at  DOUBLE          NOT NULL,
        user_agent  VARCHAR(300)    NOT NULL DEFAULT '',
        ip          VARCHAR(64)     NOT NULL DEFAULT '',
        PRIMARY KEY (fingerprint),
        UNIQUE KEY uk_sessions_sid (sid),
        KEY idx_sessions_user (user_id),
        KEY idx_sessions_expires (expires_at),
        CONSTRAINT fk_sessions_user FOREIGN KEY (user_id)
            REFERENCES users (id) ON DELETE CASCADE
    ) {_TABLE_SUFFIX}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS tickets (
        fingerprint  VARCHAR(64)     NOT NULL,
        client_id    VARCHAR(64)     NOT NULL,
        redirect_uri VARCHAR(500)    NOT NULL,
        user_id      BIGINT UNSIGNED NOT NULL,
        session_sid  VARCHAR(64)     NOT NULL,
        expires_at   DOUBLE          NOT NULL,
        PRIMARY KEY (fingerprint),
        KEY idx_tickets_session (session_sid),
        KEY idx_tickets_expires (expires_at)
    ) {_TABLE_SUFFIX}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS session_clients (
        session_sid VARCHAR(64) NOT NULL,
        client_id   VARCHAR(64) NOT NULL,
        granted_at  DOUBLE      NOT NULL,
        PRIMARY KEY (session_sid, client_id)
    ) {_TABLE_SUFFIX}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS settings (
        name       VARCHAR(64)  NOT NULL,
        value      VARCHAR(255) NOT NULL,
        note       VARCHAR(255) NOT NULL DEFAULT '',
        updated_at DOUBLE       NOT NULL,
        PRIMARY KEY (name)
    ) {_TABLE_SUFFIX}
    """,
    f"""
    CREATE TABLE IF NOT EXISTS clients (
        client_id     VARCHAR(64)  NOT NULL,
        name          VARCHAR(128) NOT NULL DEFAULT '',
        secret        VARCHAR(128) NOT NULL DEFAULT '',
        redirect_uris TEXT         NOT NULL,
        logout_uri    VARCHAR(500) NOT NULL DEFAULT '',
        home_url      VARCHAR(500) NOT NULL DEFAULT '',
        created_at    DOUBLE       NOT NULL,
        updated_at    DOUBLE       NOT NULL,
        PRIMARY KEY (client_id)
    ) {_TABLE_SUFFIX}
    """,
)

# 首次初始化自动建出来的账号。装完就能登，但它是公开可猜的，
# 所以只要口令还没改，命令行和服务启动都会一直警告
DEFAULT_ADMIN = 'admin'
DEFAULT_PASSWORD = 'admin'

# 用户不存在时也跑一遍同样开销的哈希，免得能用响应时间探测账号是否存在
_DUMMY_HASH = hash_password('__no_such_user__')


# ---------------------------------------------------------------- 连接池

_pool: queue.LifoQueue = queue.LifoQueue(maxsize=DB_POOL_SIZE)
_pool_lock = threading.Lock()
_opened = 0
_WAIT_SECONDS = 30


def describe() -> str:
    """给日志和报错用的连接描述，不含密码。"""
    return f'mysql://{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}'


def _check_password() -> None:
    """MySQL 协议按 latin-1 传密码。中文密码（比如没改的配置模板占位符）会在
    PyMySQL 里炸成 UnicodeEncodeError，报错完全看不出是密码的问题，先拦一道。
    """
    try:
        DB_PASSWORD.encode('latin1')
    except UnicodeEncodeError:
        raise DatabaseUnavailable(
            f'{CONF_PATH} 里 [database] 的 password 含中文等非 latin-1 字符。\n'
            '       MySQL 按 latin-1 传密码，这种密码用不了；'
            '配置模板里的占位符没改也会是这个报错。') from None


def _connect_hint(exc: BaseException) -> str:
    return (f'连不上数据库 {describe()}：{exc}\n'
            '  1. MySQL 起了吗，端口对不对\n'
            f'  2. {CONF_PATH} 里 [database] 的用户名密码对不对\n'
            '  3. 这个账号能连上 MySQL 吗（库本身会自动建，不用手工建）')


def ensure_database() -> None:
    """库不存在就建出来，所以第一次启动不用先去 MySQL 里手工建库。

    账号没有建库权限不算失败：生产上常见的是 DBA 预先建好库、只给应用有限权限。
    这种情况下只要库已经在就照常用下去，真的不在才报错，并把该执行的 SQL 直接给出来。
    """
    if not _DB_NAME_RE.match(DB_NAME or ''):
        raise DatabaseUnavailable(
            f'{CONF_PATH} 里 [database] 的 database = {DB_NAME!r} 不是合法库名，\n'
            '       只允许字母、数字、下划线，长度 1~64。')
    _check_password()
    try:
        # 注意这里不带 database=：库还不存在的时候，带上它连不进去
        conn = pymysql.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
            charset=DB_CHARSET, autocommit=True, connect_timeout=10,
        )
    except Exception as exc:
        raise DatabaseUnavailable(_connect_hint(exc)) from exc
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(f'CREATE DATABASE IF NOT EXISTS `{DB_NAME}`'
                            f' DEFAULT CHARACTER SET {_CHARSET_DDL}'
                            f' COLLATE {_COLLATION_DDL}')
            except pymysql.Error as create_err:
                cur.execute('SELECT 1 FROM information_schema.schemata'
                            ' WHERE schema_name = %s', (DB_NAME,))
                if not cur.fetchone():
                    raise DatabaseUnavailable(
                        f'库 `{DB_NAME}` 不存在，而账号 `{DB_USER}` 没有建库权限：{create_err}\n'
                        '       让数据库管理员执行这两句：\n'
                        f'         CREATE DATABASE `{DB_NAME}` DEFAULT CHARACTER SET '
                        f'{_CHARSET_DDL} COLLATE {_COLLATION_DDL};\n'
                        f"         GRANT ALL PRIVILEGES ON `{DB_NAME}`.* TO '{DB_USER}'@'%';"
                    ) from create_err
    finally:
        conn.close()


def _new_conn() -> pymysql.connections.Connection:
    _check_password()
    try:
        return pymysql.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, charset=DB_CHARSET, autocommit=False,
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=10,
        )
    except Exception as exc:
        raise DatabaseUnavailable(_connect_hint(exc)) from exc


def _discard(conn: pymysql.connections.Connection) -> None:
    global _opened
    try:
        conn.close()
    except pymysql.Error:
        pass
    with _pool_lock:
        _opened -= 1


def _acquire() -> pymysql.connections.Connection:
    """池里有就拿，没有就新建，到上限了就等别人还回来。"""
    global _opened
    while True:
        try:
            conn, idle_since = _pool.get_nowait()
        except queue.Empty:
            with _pool_lock:
                room = _opened < DB_POOL_SIZE
                if room:
                    _opened += 1
            if room:
                try:
                    return _new_conn()
                except BaseException:
                    with _pool_lock:
                        _opened -= 1
                    raise
            try:
                conn, idle_since = _pool.get(timeout=_WAIT_SECONDS)
            except queue.Empty:
                raise DatabaseUnavailable(
                    f'等了 {_WAIT_SECONDS} 秒也没拿到数据库连接，'
                    '可能是 pool_size 太小或者有请求卡住了。')
        if time.time() - idle_since < DB_PING_INTERVAL:
            return conn
        try:
            conn.ping(reconnect=True)      # 闲得有点久，先确认它还活着
            return conn
        except pymysql.Error:
            _discard(conn)                 # 已经被 MySQL 掐了，丢掉再取下一个


def _release(conn: pymysql.connections.Connection, broken: bool) -> None:
    if broken:
        _discard(conn)
        return
    try:
        _pool.put_nowait((conn, time.time()))
    except queue.Full:
        _discard(conn)


class _Result:
    """一次取完的查询结果，让调用方还能用 .fetchone() / .rowcount 这些老写法。"""
    __slots__ = ('rows', 'rowcount', 'lastrowid')

    def __init__(self, rows: list[dict], rowcount: int, lastrowid: int) -> None:
        self.rows, self.rowcount, self.lastrowid = rows, rowcount, lastrowid

    def fetchone(self) -> Optional[dict]:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict]:
        return self.rows


class _Conn:
    """保留 conn.execute(sql, args) 的写法。

    结果一次取完就关游标：这里的查询都很小，省得游标漏在池化的连接上。
    """
    __slots__ = ('_raw',)

    def __init__(self, raw: pymysql.connections.Connection) -> None:
        self._raw = raw

    def execute(self, sql: str, args: Sequence[Any] = ()) -> _Result:
        with self._raw.cursor() as cur:
            cur.execute(sql, args)
            rows = list(cur.fetchall()) if cur.description else []
            return _Result(rows, cur.rowcount, cur.lastrowid)


@contextmanager
def connect() -> Iterator[_Conn]:
    conn = _acquire()
    broken = False
    try:
        yield _Conn(conn)
        conn.commit()
    except BaseException as exc:
        broken = isinstance(exc, (pymysql.OperationalError, pymysql.InterfaceError))
        if not broken:
            try:
                conn.rollback()
            except pymysql.Error:
                broken = True
        raise
    finally:
        _release(conn, broken)


def init_db() -> None:
    """建库 + 建表 + 补默认配置。每次启动都跑，已经有了就是几条空转的 DDL。"""
    ensure_database()
    now = time.time()
    with connect() as conn:
        for statement in SCHEMA:
            conn.execute(statement)
        # 把配置清单补进 settings 表：值只在第一次写，说明每次都刷新，
        # 这样 manage.py settings 看到的是完整清单而不是一张空表
        for name, spec in settings.DEFAULTS.items():
            conn.execute(
                'INSERT INTO settings (name, value, note, updated_at) VALUES (%s, %s, %s, %s)'
                ' ON DUPLICATE KEY UPDATE note = %s',
                (name, settings.as_text(spec.default), spec.note, now, spec.note),
            )


def _row_to_user(row: Optional[dict]) -> Optional[dict[str, Any]]:
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
            ' VALUES (%s, %s, %s, %s, %s, %s)',
            (username.strip(), hash_password(password), display_name.strip(),
             email.strip(), roles.strip(), time.time()),
        )
        row = conn.execute('SELECT * FROM users WHERE id = %s', (cur.lastrowid,)).fetchone()
    return _row_to_user(row)


def get_user(username: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row_to_user(
            conn.execute('SELECT * FROM users WHERE username = %s', (username.strip(),)).fetchone()
        )


def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute('SELECT * FROM users ORDER BY id').fetchall()
    return [_row_to_user(r) for r in rows]


def set_password(username: str, password: str) -> bool:
    with connect() as conn:
        cur = conn.execute('UPDATE users SET password = %s WHERE username = %s',
                           (hash_password(password), username.strip()))
        return cur.rowcount > 0


def set_disabled(username: str, disabled: bool) -> bool:
    with connect() as conn:
        cur = conn.execute('UPDATE users SET disabled = %s WHERE username = %s',
                           (1 if disabled else 0, username.strip()))
        if cur.rowcount and disabled:
            conn.execute('DELETE FROM sessions WHERE user_id IN'
                         ' (SELECT id FROM users WHERE username = %s)', (username.strip(),))
        return cur.rowcount > 0


def delete_user(username: str) -> bool:
    with connect() as conn:
        cur = conn.execute('DELETE FROM users WHERE username = %s', (username.strip(),))
        return cur.rowcount > 0


def uses_default_password(username: str = DEFAULT_ADMIN,
                          password: str = DEFAULT_PASSWORD) -> bool:
    """这个账号是不是还在用默认口令。给启动警告用。"""
    with connect() as conn:
        row = conn.execute('SELECT password FROM users WHERE username = %s',
                           (username,)).fetchone()
    return row is not None and verify_password(password, row['password'])


def check_login(username: str, password: str) -> Optional[dict[str, Any]]:
    """用户名错、口令错、账号停用都返回 None，不区分，免得被拿来枚举账号。"""
    with connect() as conn:
        row = conn.execute('SELECT * FROM users WHERE username = %s',
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
    # 先取配置再开连接：settings 取值自己要占一条池化连接
    expires_at = now + settings.session_ttl_hours() * 3600
    with connect() as conn:
        conn.execute(
            'INSERT INTO sessions (fingerprint, sid, user_id, created_at, expires_at, user_agent, ip)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
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
            ' JOIN users u ON u.id = s.user_id WHERE s.fingerprint = %s', (fp,)).fetchone()
        if row is None:
            return None
        if row['expires_at'] < time.time() or row['disabled']:
            conn.execute('DELETE FROM sessions WHERE fingerprint = %s', (fp,))
            return None
    return {'sid': row['sid'], 'expires_at': row['expires_at'], 'user': _row_to_user(row)}


def drop_session(token: str) -> Optional[str]:
    """删会话，返回它的 sid，交给上层去通知各业务系统。"""
    if not token:
        return None
    fp = token_fingerprint(token)
    with connect() as conn:
        row = conn.execute('SELECT sid FROM sessions WHERE fingerprint = %s', (fp,)).fetchone()
        if row is None:
            return None
        conn.execute('DELETE FROM sessions WHERE fingerprint = %s', (fp,))
        conn.execute('DELETE FROM tickets WHERE session_sid = %s', (row['sid'],))
    return row['sid']


# ---------------------------------------------------------------- 一次性票据

def issue_ticket(client_id: str, redirect_uri: str, user_id: int, session_sid: str) -> str:
    ticket = 'ST-' + new_token()
    # 同上：先把有效期取出来，别在持有连接的时候再去读 settings
    expires_at = time.time() + settings.ticket_ttl_seconds()
    with connect() as conn:
        conn.execute(
            'INSERT INTO tickets (fingerprint, client_id, redirect_uri, user_id, session_sid, expires_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s)',
            (token_fingerprint(ticket), client_id, redirect_uri, user_id, session_sid, expires_at),
        )
    return ticket


def consume_ticket(ticket: str, client_id: str) -> Optional[dict[str, Any]]:
    """一次性：不管校验过不过都先删票，重放拿不到第二次。"""
    if not ticket:
        return None
    fp = token_fingerprint(ticket)
    with connect() as conn:
        row = conn.execute('SELECT * FROM tickets WHERE fingerprint = %s', (fp,)).fetchone()
        conn.execute('DELETE FROM tickets WHERE fingerprint = %s', (fp,))
        if row is None or row['client_id'] != client_id or row['expires_at'] < time.time():
            return None
        user = _row_to_user(
            conn.execute('SELECT * FROM users WHERE id = %s', (row['user_id'],)).fetchone())
        if user is None or user['disabled']:
            return None
        session = conn.execute('SELECT expires_at FROM sessions WHERE sid = %s',
                               (row['session_sid'],)).fetchone()
        if session is None or session['expires_at'] < time.time():
            return None
        now = time.time()
        conn.execute('INSERT INTO session_clients (session_sid, client_id, granted_at)'
                     ' VALUES (%s, %s, %s)'
                     ' ON DUPLICATE KEY UPDATE granted_at = %s',
                     (row['session_sid'], client_id, now, now))
    return {'user': user, 'session_sid': row['session_sid'],
            'session_expires_at': session['expires_at'], 'redirect_uri': row['redirect_uri']}


def clients_of_session(sid: str) -> list[str]:
    """取出并清空这个会话登过的业务系统列表。"""
    with connect() as conn:
        rows = conn.execute('SELECT client_id FROM session_clients WHERE session_sid = %s',
                            (sid,)).fetchall()
        conn.execute('DELETE FROM session_clients WHERE session_sid = %s', (sid,))
    return [r['client_id'] for r in rows]


def purge_expired() -> None:
    now = time.time()
    with connect() as conn:
        conn.execute('DELETE FROM tickets WHERE expires_at < %s', (now,))
        conn.execute('DELETE FROM sessions WHERE expires_at < %s', (now,))
        conn.execute(
            'DELETE FROM session_clients WHERE session_sid NOT IN (SELECT sid FROM sessions)')
