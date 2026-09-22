"""MySQL：连接池 + 建表。门户的全部数据都在这儿，conf.json 只剩连接串。

为什么是 PyMySQL 直连、手写 SQL，而不是 SQLAlchemy：

  - 整个门户的表就五张，查询都是「按 user_id 取一棵两层树」，ORM 省不下什么；
  - **PyInstaller 靠静态分析决定打包什么**。SQLAlchemy 的方言、连接池实现全是按
    字符串名字动态导入的，打进 exe 要手写一长串 hiddenimports，漏一个的表现是
    「源码跑得好好的，exe 一连库就 ModuleNotFoundError」。PyMySQL 是纯 Python
    单包，import 全是静态的，spec 里一行都不用加。

## 连接池

FastAPI 的同步路由跑在 anyio 的线程池里（默认 40 条线程），所以这里必须是
**线程安全**的池子，而不是一条全局连接——两个请求同时用一条 MySQL 连接会把
协议帧串掉，表现成莫名其妙的 `Packet sequence number wrong`。

池子是一个 `LifoQueue`：后进先出，闲时只有最前面那一两条连接在转，剩下的会被
MySQL 的 `wait_timeout` 自然收走，不用自己做空闲回收。取出来先 `ping(reconnect=True)`，
连接被服务端掐了会当场重连——`wait_timeout` 默认 8 小时，门户半夜没人用，
早上第一个请求撞上「MySQL server has gone away」几乎是必然的。

## 事务

`cursor()` 默认只读（走完 rollback，把隐式开启的事务干净地关掉，免得连接一直
挂着一个老快照，读到的数据越来越旧）。要写的加 `write=True`，正常走完提交，
抛异常回滚。**不用 autocommit**：存一次导航是「删掉整棵树再插一棵新的」，
中间断掉的话用户的导航就只剩半棵。
"""
import json
import queue
import re
import secrets
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import pymysql
from pymysql.cursors import DictCursor

from . import config

# 库名要拼进 CREATE DATABASE，参数化占位符在这个位置不管用，只能自己验一遍。
# 配置文件是自己人写的，但一个手滑的反引号能把这条 DDL 变成任意 SQL
_IDENT = re.compile(r'^[A-Za-z0-9_]+$')

# Cookie 签名密钥存在 portal_meta 里：多个门户实例连同一个库时得用同一把钥匙，
# 各自随机生成的话，A 发的 Cookie 到 B 那儿一律不认，表现成「刷新一下就掉线」
SECRET_KEY = 'session_secret'

_POOL_TIMEOUT = 10        # 等一条空闲连接最多等这么久，超了宁可报错也别把线程挂死

_pool: Optional[queue.LifoQueue] = None
_pool_lock = threading.Lock()
_created = 0              # 已经建出来的连接数，到 pool_size 就不再新建，只等


def _connect(database: Optional[str] = None) -> pymysql.connections.Connection:
    """建一条新连接。`database=None` 用于建库那一步（那会儿库还不存在）。"""
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=database,
        charset=config.DB_CHARSET,
        cursorclass=DictCursor,
        autocommit=False,
        # 连接超时留着，读写超时不设：建表那一下、以及导航整棵树重写在大库上
        # 可能慢一点，卡在超时上只会得到一份写了一半的数据
        connect_timeout=10,
        # 拿 dict 回来时列名区分大小写，和 SQL 里写的一致
        init_command="SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'",
    )


def _get() -> pymysql.connections.Connection:
    global _created
    assert _pool is not None, 'db.init() 还没跑过'
    try:
        return _pool.get_nowait()
    except queue.Empty:
        pass
    with _pool_lock:
        if _created < config.DB_POOL_SIZE:
            conn = _connect(config.DB_NAME)
            _created += 1
            return conn
    # 池子满了，排队等一条还回来的
    try:
        return _pool.get(timeout=_POOL_TIMEOUT)
    except queue.Empty:
        raise RuntimeError(
            f'等数据库连接超过 {_POOL_TIMEOUT} 秒都没等到。'
            f'把 conf.json 里 database.pool_size（现在是 {config.DB_POOL_SIZE}）调大，'
            '或者看看是不是有慢查询把池子占住了') from None


def _put(conn: pymysql.connections.Connection, broken: bool = False) -> None:
    global _created
    if broken:
        # 坏掉的连接别还回池子：下一个人拿到它还是坏的，而 ping 未必救得回来
        with _pool_lock:
            _created = max(0, _created - 1)
        try:
            conn.close()
        except Exception:
            pass
        return
    assert _pool is not None
    _pool.put(conn)


@contextmanager
def cursor(write: bool = False) -> Iterator[DictCursor]:
    """借一条连接、开一个游标。`write=True` 才提交。

    **不管走哪条路，连接都恰好还回去一次**（或者按坏的关掉）。还两次会让池子里
    出现同一条连接的两份，两个请求同时拿到它就把协议帧串掉了；一次都不还则是
    永久少一条，池子会越用越小，最后每个请求都卡在等连接上。
    """
    conn = _get()
    try:
        conn.ping(reconnect=True)       # 半夜被 wait_timeout 掐掉的连接在这儿重连
    except Exception:
        _put(conn, broken=True)
        conn = _get()
        try:
            conn.ping(reconnect=True)
        except Exception:
            _put(conn, broken=True)
            raise

    broken = False
    cur = conn.cursor()
    try:
        yield cur
        if write:
            conn.commit()
        else:
            # 只读也要收尾：InnoDB 在第一条 SELECT 时就开了事务，不关的话这条连接
            # 会一直挂着当时那个快照，被它服务的请求读到的数据会越来越旧
            conn.rollback()
    except Exception:
        # 先当成坏的。回滚得动就说明连接本身还好，只是这次操作失败
        # （调用方自己抛的异常、违反唯一键之类），那就照常还回池子
        broken = True
        try:
            conn.rollback()
            broken = False
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            broken = True
        _put(conn, broken=broken)


# ---------------------------------------------------------------- 表结构
#
# 一条语句一个字符串：PyMySQL 一次只执行一条，`;` 分隔的整段丢进去会报语法错。
# 全是 IF NOT EXISTS，每次启动都跑一遍，已经建好的库不受影响。
#
# 字符集写死 utf8mb4：三字节的 utf8 存不下 emoji，而卡片名字里很容易出现一个，
# 表现成插入时 `Incorrect string value`。
_DDL = [
    """
    CREATE TABLE IF NOT EXISTS portal_meta (
      k          VARCHAR(64)  NOT NULL,
      v          TEXT         NULL,
      updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (k)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='门户自己的杂项：签名密钥、迁移标记'
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      username      VARCHAR(64)     NOT NULL,
      display_name  VARCHAR(128)    NOT NULL DEFAULT '',
      -- 纯 OIDC 用户没有本地口令，这一列是 NULL。
      -- NULL 和空串要分开：空串会被当成「口令是空的」，那等于谁都能登进来
      password_hash VARCHAR(255)    NULL,
      role          VARCHAR(16)     NOT NULL DEFAULT 'user',
      is_active     TINYINT(1)      NOT NULL DEFAULT 1,
      -- 改口令、停用、或者管理员点「踢下线」时 +1。签名 Cookie 里带着这个数，
      -- 对不上就不认——服务端不存会话，这是唯一能让已发出的 Cookie 立刻失效的办法
      token_version INT UNSIGNED    NOT NULL DEFAULT 1,
      created_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (id),
      UNIQUE KEY uk_users_username (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS user_identities (
      id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      user_id    BIGINT UNSIGNED NOT NULL,
      provider   VARCHAR(64)     NOT NULL,
      -- IdP 那边的 sub。**认人只认它**，不认 email 也不认用户名：
      -- 那两个在 IdP 里都是可以改的，跟着它们走会让改过名的人变成另一个人
      subject    VARCHAR(255)    NOT NULL,
      email      VARCHAR(255)    NOT NULL DEFAULT '',
      created_at TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (id),
      UNIQUE KEY uk_identity (provider, subject),
      KEY idx_identity_user (user_id),
      CONSTRAINT fk_identity_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS nav_prefs (
      user_id    BIGINT UNSIGNED NOT NULL,
      title      VARCHAR(200)    NOT NULL DEFAULT '',
      theme      VARCHAR(16)     NOT NULL DEFAULT 'auto',
      updated_at TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (user_id),
      CONSTRAINT fk_prefs_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每人一份：标题和主题'
    """,
    """
    CREATE TABLE IF NOT EXISTS nav_groups (
      id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      user_id   BIGINT UNSIGNED NOT NULL,
      -- 前端生成的那个 id（store.js 的 uid()）。前端整棵树都拿它做 key，
      -- 换成数据库主键的话拖拽、编辑全要跟着改，所以原样存着
      client_id VARCHAR(64)     NOT NULL,
      name      VARCHAR(100)    NOT NULL DEFAULT '',
      `position` INT            NOT NULL DEFAULT 0,
      -- 前端在分类上加的、后端不认识的字段原样存这里。
      -- 后端跟着校验的话，卡片上加一个字段就得两头一起改
      extra     JSON            NULL,
      PRIMARY KEY (id),
      UNIQUE KEY uk_group (user_id, client_id),
      KEY idx_group_order (user_id, `position`),
      CONSTRAINT fk_group_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS nav_items (
      id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      -- group_id 就能推出 user_id，这里仍然冗余一份：门户代理是拿卡片 id
      -- 直接查归属的（app/proxy.py），少一次 join 就少一次犯错的机会
      user_id   BIGINT UNSIGNED NOT NULL,
      group_id  BIGINT UNSIGNED NOT NULL,
      client_id VARCHAR(64)     NOT NULL,
      name      VARCHAR(200)    NOT NULL DEFAULT '',
      url       TEXT            NOT NULL,
      -- desc 是 MySQL 的保留字（ORDER BY ... DESC），列名用 descr
      descr     VARCHAR(500)    NOT NULL DEFAULT '',
      icon      TEXT            NULL,
      -- '' / 'true' / 'site'，和前端那三挡一一对应。存成字符串而不是 tinyint：
      -- 'site' 塞不进布尔，而两列（开关 + 模式）会有「关着但模式是 site」这种说不清的状态
      proxy     VARCHAR(16)     NOT NULL DEFAULT '',
      `position` INT            NOT NULL DEFAULT 0,
      extra     JSON            NULL,
      PRIMARY KEY (id),
      UNIQUE KEY uk_item (user_id, client_id),
      KEY idx_item_order (group_id, `position`),
      CONSTRAINT fk_item_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE CASCADE,
      CONSTRAINT fk_item_group FOREIGN KEY (group_id)
        REFERENCES nav_groups (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def init() -> None:
    """建库、建表、把池子支起来。启动时调一次（app/main.py 的 lifespan）。

    连不上就 `SystemExit`：这一版没有「退回单机模式」的余地，数据全在库里，
    带着一个连不上的库把服务起起来，用户看到的会是每个接口都 500。
    """
    global _pool
    if not _IDENT.match(config.DB_NAME):
        raise SystemExit(f'[数据库] 库名 {config.DB_NAME!r} 只能是字母、数字和下划线')

    try:
        # 先不指定库连上去，库不存在就建一个。少一步「请先手工 CREATE DATABASE」
        conn = _connect(None)
        try:
            with conn.cursor() as cur:
                cur.execute(f'CREATE DATABASE IF NOT EXISTS `{config.DB_NAME}` '
                            f'DEFAULT CHARACTER SET {config.DB_CHARSET}')
            conn.commit()
        finally:
            conn.close()

        conn = _connect(config.DB_NAME)
        try:
            with conn.cursor() as cur:
                for ddl in _DDL:
                    cur.execute(ddl)
            conn.commit()
        finally:
            conn.close()
    except pymysql.Error as exc:
        raise SystemExit(
            f'[数据库] 连不上 {config.db_dsn()}: {exc}\n'
            f'       门户的用户和导航全在这个库里，连不上就没法启动。\n'
            f'       检查 {config.CONF_PATH} 里的 database 那一段，'
            f'确认 MySQL 起着、账号有建库建表的权限。') from None

    _pool = queue.LifoQueue(maxsize=config.DB_POOL_SIZE)


def close() -> None:
    """把池子里的连接都关掉（app/main.py 退出时调）。"""
    global _pool, _created
    if _pool is None:
        return
    while True:
        try:
            conn = _pool.get_nowait()
        except queue.Empty:
            break
        try:
            conn.close()
        except Exception:
            pass
    _pool = None
    _created = 0


# ---------------------------------------------------------------- 杂项键值

def meta_get(key: str) -> Optional[str]:
    with cursor() as cur:
        cur.execute('SELECT v FROM portal_meta WHERE k = %s', (key,))
        row = cur.fetchone()
    return row['v'] if row else None


def meta_set(key: str, value: str) -> None:
    with cursor(write=True) as cur:
        cur.execute('INSERT INTO portal_meta (k, v) VALUES (%s, %s) '
                    'ON DUPLICATE KEY UPDATE v = VALUES(v)', (key, value))


def session_secret() -> bytes:
    """Cookie 的签名密钥。库里没有就现生成一把存进去。

    **不是配置项**：让人往 conf.json 里填一串随机数，多半会被填成 `secret`；
    而且多实例部署时两边填得不一样，表现成刷新一下就掉线。生成一次存库最省事。

    上一版这把钥匙是从口令 scrypt 出来的，改口令即刻让所有 Cookie 失效。多用户之后
    那条路走不通了（一把钥匙对应不了 N 个口令），换成 users.token_version：
    改口令时给那个人 +1，只踢他一个人，不影响别人。
    """
    raw = meta_get(SECRET_KEY)
    if raw:
        return bytes.fromhex(raw)
    key = secrets.token_bytes(32)
    # 并发首启时两个进程可能同时走到这儿。INSERT IGNORE 让后来的那个失败，
    # 然后再读一次——拿到的是先写进去的那把，两边一致
    with cursor(write=True) as cur:
        cur.execute('INSERT IGNORE INTO portal_meta (k, v) VALUES (%s, %s)',
                    (SECRET_KEY, key.hex()))
    raw = meta_get(SECRET_KEY)
    return bytes.fromhex(raw) if raw else key


def loads(raw: Any) -> Any:
    """JSON 列读出来的东西。PyMySQL 在不同版本上给的是 str 或已经解好的对象。"""
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def describe() -> str:
    """给启动日志用的一句话。"""
    with cursor() as cur:
        cur.execute('SELECT COUNT(*) AS n FROM users')
        users = cur.fetchone()['n']
        cur.execute('SELECT COUNT(*) AS n FROM nav_items')
        items = cur.fetchone()['n']
    return f'{config.db_dsn()}（{users} 个用户，{items} 个导航）'
