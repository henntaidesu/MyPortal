"""用户表：本地口令、角色、OIDC 绑定关系。

口令用 scrypt 存，**每人一把随机盐**，落库的形状是：

    scrypt$<n>$<r>$<p>$<盐 hex>$<派生结果 hex>

参数写在串里而不是写死在代码里：以后调大 n，老口令照样验得动（按串里那份参数算），
下次改口令时自然换成新参数。写死的话调参那天所有人都得重置口令。

`n=16384, r=8` 要 16 MB 内存，在 `hashlib.scrypt` 默认 32 MB 上限内，调大之前
先确认这一点——超了会直接抛 ValueError，表现成「登录接口 500」。

**纯 OIDC 用户的 password_hash 是 NULL，不是空串。** 空串会走进「比一比口令」
那条路，而空口令和任意输入比都可能被人试出规律；NULL 在 `check_password` 里
直接返回 False，本地登录这条路对这个人是关着的。
"""
import hashlib
import hmac
import re
import secrets
from typing import Any, Optional

from . import config, db

ROLES = ('admin', 'user')

# 用户名的形状。限死是为了 OIDC 自动建号那条路：IdP 给的 preferred_username
# 什么字符都可能有，直接拿来当用户名会出现带空格、带斜杠的名字，
# 而这个名字会出现在 URL 和日志里
_USERNAME = re.compile(r'^[A-Za-z0-9._@\-]{2,64}$')

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1


# ---------------------------------------------------------------- 口令

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode('utf-8'), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f'scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}'


def check_password(stored: Optional[str], password: str) -> bool:
    """存的那串对不对。任何一步不对都返回 False，不区分原因。

    **这里没有「提前返回」的捷径**：stored 是 NULL 时也要走一遍 scrypt 再返回 False，
    否则「这个人有没有本地口令」能从响应时间上看出来——而没有本地口令
    多半意味着他是 OIDC 用户，等于白送一条用户枚举的路。
    """
    parts = (stored or '').split('$')
    if len(parts) == 6 and parts[0] == 'scrypt':
        try:
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            salt = bytes.fromhex(parts[4])
            expect = bytes.fromhex(parts[5])
        except ValueError:
            n, r, p, salt, expect = _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, b'x' * 16, b'y' * 32
    else:
        # 假的参数，照样算一遍，耗时和真的那条路一样
        n, r, p, salt, expect = _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, b'x' * 16, b'y' * 32

    try:
        dk = hashlib.scrypt(password.encode('utf-8'), salt=salt,
                            n=n, r=r, p=p, dklen=len(expect))
    except ValueError:
        return False
    ok = hmac.compare_digest(dk, expect)
    return ok and bool(stored)


# ---------------------------------------------------------------- 读

_COLUMNS = ('id, username, display_name, password_hash, role, is_active, '
            'token_version, created_at, updated_at')


def by_id(user_id: int) -> Optional[dict]:
    with db.cursor() as cur:
        cur.execute(f'SELECT {_COLUMNS} FROM users WHERE id = %s', (user_id,))
        return cur.fetchone()


def by_username(username: str) -> Optional[dict]:
    with db.cursor() as cur:
        cur.execute(f'SELECT {_COLUMNS} FROM users WHERE username = %s', (username,))
        return cur.fetchone()


def by_identity(provider: str, subject: str) -> Optional[dict]:
    """按 IdP 的 sub 找人。认人只认 sub，不认 email 也不认用户名。"""
    with db.cursor() as cur:
        cur.execute(
            f'SELECT u.id, u.username, u.display_name, u.password_hash, u.role, '
            f'u.is_active, u.token_version, u.created_at, u.updated_at '
            f'FROM users u JOIN user_identities i ON i.user_id = u.id '
            f'WHERE i.provider = %s AND i.subject = %s',
            (provider, subject))
        return cur.fetchone()


def list_all() -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            'SELECT u.id, u.username, u.display_name, u.role, u.is_active, '
            'u.created_at, u.updated_at, '
            '(u.password_hash IS NOT NULL) AS has_password, '
            '(SELECT COUNT(*) FROM user_identities i WHERE i.user_id = u.id) AS identities, '
            '(SELECT COUNT(*) FROM nav_items n WHERE n.user_id = u.id) AS items '
            'FROM users u ORDER BY u.id')
        return cur.fetchall()


def count() -> int:
    with db.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS n FROM users')
        return int(cur.fetchone()['n'])


def admin_count(exclude_id: Optional[int] = None) -> int:
    """还在岗的管理员有几个。`exclude_id` 用来问「把这个人拿掉之后还剩几个」。"""
    sql = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
    args: tuple = ()
    if exclude_id is not None:
        sql += ' AND id <> %s'
        args = (exclude_id,)
    with db.cursor() as cur:
        cur.execute(sql, args)
        return int(cur.fetchone()['n'])


def public(row: Optional[dict]) -> Optional[dict]:
    """下发给前端的身份信息。**口令哈希绝不能进这里。**"""
    if not row:
        return None
    return {
        'id': int(row['id']),
        'username': row['username'],
        'display_name': row.get('display_name') or row['username'],
        'role': row.get('role') or 'user',
        'is_admin': (row.get('role') or 'user') == 'admin',
    }


# ---------------------------------------------------------------- 写

def normalize_username(raw: str) -> str:
    name = (raw or '').strip()
    if not _USERNAME.match(name):
        raise ValueError('用户名只能用字母、数字和 . _ @ - ，长度 2 到 64')
    return name


def create(username: str, password: Optional[str] = None, *,
           role: str = 'user', display_name: str = '',
           is_active: bool = True) -> dict:
    """建一个用户。`password=None` 就是纯 OIDC 用户（本地登录对他关着）。"""
    name = normalize_username(username)
    if role not in ROLES:
        raise ValueError(f'角色只能是 {" / ".join(ROLES)}')
    if password is not None and len(password) < 6:
        raise ValueError('口令至少 6 位')
    pw_hash = hash_password(password) if password is not None else None

    with db.cursor(write=True) as cur:
        cur.execute('SELECT id FROM users WHERE username = %s', (name,))
        if cur.fetchone():
            raise ValueError(f'用户名 {name} 已经有人用了')
        cur.execute(
            'INSERT INTO users (username, display_name, password_hash, role, is_active) '
            'VALUES (%s, %s, %s, %s, %s)',
            (name, (display_name or name)[:128], pw_hash, role, 1 if is_active else 0))
        user_id = cur.lastrowid
        # 每人一份标题和主题。这里就建好，省得后面到处判断「有没有这一行」
        cur.execute('INSERT IGNORE INTO nav_prefs (user_id, title) VALUES (%s, %s)',
                    (user_id, '我的门户'))
    created = by_id(user_id)
    assert created is not None
    return created


def update(user_id: int, *, display_name: Optional[str] = None,
           role: Optional[str] = None, is_active: Optional[bool] = None) -> dict:
    sets, args = [], []
    if display_name is not None:
        sets.append('display_name = %s')
        args.append(display_name.strip()[:128])
    if role is not None:
        if role not in ROLES:
            raise ValueError(f'角色只能是 {" / ".join(ROLES)}')
        sets.append('role = %s')
        args.append(role)
    if is_active is not None:
        sets.append('is_active = %s')
        args.append(1 if is_active else 0)
        if not is_active:
            # 停用要立刻生效：不 +1 的话，人已经被停用了，手里那枚 Cookie 还能用到过期
            sets.append('token_version = token_version + 1')
    if not sets:
        row = by_id(user_id)
        if row is None:
            raise ValueError('用户不存在')
        return row

    # 把最后一个在岗管理员降级或停用 = 从此没人能管用户。拦在这里，不是在路由里，
    # 免得以后多一条改用户的路就漏一次
    if role == 'user' or is_active is False:
        current = by_id(user_id)
        if current and current['role'] == 'admin' and current['is_active']:
            if admin_count(exclude_id=user_id) == 0:
                raise ValueError('这是最后一个管理员，不能降级或停用')

    args.append(user_id)
    with db.cursor(write=True) as cur:
        cur.execute(f'UPDATE users SET {", ".join(sets)} WHERE id = %s', args)
    row = by_id(user_id)
    if row is None:
        raise ValueError('用户不存在')
    return row


def set_password(user_id: int, password: Optional[str]) -> None:
    """改口令。顺手 token_version +1，**把这个人已经发出去的 Cookie 全作废**。

    不作废的话，口令被人拿到之后改口令这一步就白做了：对方手里那枚 Cookie
    还能接着用到过期（默认 7 天）。只 +1 他自己那一行，不影响别人。

    `password=None` 是「取消本地口令」，这个人以后只能走 OIDC。
    """
    if password is not None and len(password) < 6:
        raise ValueError('口令至少 6 位')
    pw_hash = hash_password(password) if password is not None else None
    with db.cursor(write=True) as cur:
        cur.execute('UPDATE users SET password_hash = %s, '
                    'token_version = token_version + 1 WHERE id = %s',
                    (pw_hash, user_id))


def bump_token(user_id: int) -> None:
    """把这个人所有设备上的登录踢掉。"""
    with db.cursor(write=True) as cur:
        cur.execute('UPDATE users SET token_version = token_version + 1 WHERE id = %s',
                    (user_id,))


def delete(user_id: int) -> None:
    """连人带导航一起删（外键 ON DELETE CASCADE）。"""
    current = by_id(user_id)
    if current is None:
        raise ValueError('用户不存在')
    if current['role'] == 'admin' and admin_count(exclude_id=user_id) == 0:
        raise ValueError('这是最后一个管理员，不能删除')
    with db.cursor(write=True) as cur:
        cur.execute('DELETE FROM users WHERE id = %s', (user_id,))


def link_identity(user_id: int, provider: str, subject: str, email: str = '') -> None:
    with db.cursor(write=True) as cur:
        cur.execute(
            'INSERT INTO user_identities (user_id, provider, subject, email) '
            'VALUES (%s, %s, %s, %s) '
            'ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), email = VALUES(email)',
            (user_id, provider, subject, email[:255]))


def identities(user_id: int) -> list[dict]:
    with db.cursor() as cur:
        cur.execute('SELECT provider, subject, email, created_at '
                    'FROM user_identities WHERE user_id = %s ORDER BY id', (user_id,))
        return cur.fetchall()


def unlink_identity(user_id: int, provider: str) -> None:
    with db.cursor(write=True) as cur:
        cur.execute('DELETE FROM user_identities WHERE user_id = %s AND provider = %s',
                    (user_id, provider))


# ---------------------------------------------------------------- 第一个管理员

def ensure_bootstrap() -> Optional[dict]:
    """库里一个用户都没有时，按 conf.json 的 auth.bootstrap_admin 建第一个管理员。

    **只在表为空时看这一段**：已经有人了就完全不管它，所以配置里留着那对用户名
    口令也不会把人家改过的口令改回去，更不会重复建号。

    没配这一段、又一个用户都没有的话，返回 None——启动日志里会喊，
    但服务照常起来（不然改配置都没机会）。
    """
    if count() > 0:
        return None
    if not (config.BOOTSTRAP_USERNAME and config.BOOTSTRAP_PASSWORD):
        return None
    try:
        row = create(config.BOOTSTRAP_USERNAME, config.BOOTSTRAP_PASSWORD,
                     role='admin', display_name=config.BOOTSTRAP_USERNAME)
    except ValueError as exc:
        print(f'[用户] 建第一个管理员失败: {exc}')
        return None
    print(f'[用户] 库里没有用户，已按 conf.json 建了管理员 {row["username"]}')
    return row


def describe() -> str:
    """给启动日志用的一句话。"""
    rows = list_all()
    admins = sum(1 for r in rows if r['role'] == 'admin' and r['is_active'])
    return f'{len(rows)} 个用户（{admins} 个管理员）'
