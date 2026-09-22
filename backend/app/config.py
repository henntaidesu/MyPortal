"""conf.json：门户的**配置**，从这一版起里面只有配置，没有数据。

    {
      "server":   {"host": "0.0.0.0", "port": 9921},
      "database": {"host": "127.0.0.1", "port": 3306, "user": "portal",
                   "password": "", "name": "portal", "pool_size": 8},
      "auth":     {"session_hours": 168, "cookie_secure": false,
                   "allow_local_login": true,
                   "bootstrap_admin": {"username": "admin", "password": "admin"}},
      "oidc":     {"enabled": false, "issuer": "...", "client_id": "...", ...}
    }

**用户、导航、OIDC 绑定关系全在 MySQL 里**（见 app/db.py）。老版本把导航塞在这个
文件的 `nav` 段里，首次建库时会被搬进第一个管理员名下（见 app/store/navstore.py
的 `import_legacy`），**搬完不删**——删用户的数据不该由程序替人决定。

整个文件只在启动时读一次，改完要重启。这一版没有「跑着的时候改 conf.json」这回事了：
会变的东西（口令、导航、用户）都在库里，改库即刻生效。

文件里有数据库口令和 OIDC 的 client_secret，已经在 .gitignore 里。
"""
import json
import os
import sys
from pathlib import Path
from typing import Any

FROZEN = getattr(sys, 'frozen', False)                     # PyInstaller 打出来的 exe

if FROZEN:
    # 打包后一律以 exe 所在目录为准。别用 sys._MEIPASS——那是每次启动现解压的临时
    # 目录，conf.json 写进去下次启动就没了，改了连接串全白改
    BASE_DIR = ROOT_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent      # backend/
    ROOT_DIR = BASE_DIR.parent                             # Portal/

CONF_PATH = BASE_DIR / 'conf.json'

# 老版本留下的两个文件，只在生成 conf.json 那一次读，读完不删
_OLD_CONF = BASE_DIR / 'conf.ini'
_OLD_NAV = BASE_DIR / 'nav.json'

# 站点图标的磁盘缓存目录，启动时自动建（app/iconcache.py 的 ensure）。
# 不放进数据库：是一堆几十 KB 的二进制，而且随时可以重抓，整个删掉也没事。
ICON_DIR = BASE_DIR / 'icons'

DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'admin'

# 自动生成的 conf.json 内容。database 那一段是必须有人填的，填不对就起不来——
# 这一版没有「退回单机模式」的余地。
_DEFAULT: dict[str, Any] = {
    'server': {'host': '0.0.0.0', 'port': 9921},
    'database': {
        'host': '127.0.0.1',
        'port': 3306,
        'user': 'root',
        'password': '',
        'name': 'portal',
        'charset': 'utf8mb4',
        # 连接池大小。FastAPI 的同步路由跑在 anyio 的线程池里（默认 40 条线程），
        # 池子取小了会让请求排队等连接，取大了 MySQL 那头 max_connections 顶不住
        'pool_size': 8,
    },
    'auth': {
        'session_hours': 168,
        # 只有 https 部署才能开。http 下开了浏览器会直接丢掉登录 Cookie，
        # 表现成「登录成功后立刻又变回未登录」
        'cookie_secure': False,
        # 关掉之后只能走 OIDC 登录。接了单点登录的部署通常要关，
        # 不然本地口令就是绕开 IdP 的一道后门
        'allow_local_login': True,
        # 库里一个用户都没有时按这个建第一个管理员。建完这一段就没用了，
        # 可以删掉——留着也不会重复建（只在 users 表为空时才看）
        'bootstrap_admin': {'username': DEFAULT_USERNAME, 'password': DEFAULT_PASSWORD},
    },
    'oidc': {
        'enabled': False,
        # IdP 的 issuer，末尾不用带 /.well-known/openid-configuration，
        # 发现文档由 app/oidc.py 自己拼
        'issuer': '',
        'client_id': '',
        'client_secret': '',
        'scopes': 'openid profile email',
        # 留空就按请求的 Host 现拼 <scheme>://<host>/api/auth/oidc/callback。
        # 门户挂在反代后面、或者 IdP 那边登记的回调地址和这里算出来的不一样时，
        # 必须写死一个——地址对不上 IdP 会直接拒绝授权请求
        'redirect_url': '',
        # IdP 里有、门户库里还没有的人，第一次登录时自动建账号。
        # 关掉的话得由管理员先在门户里建好同名用户，OIDC 只负责认人
        'auto_create_user': True,
        # 拿哪个 claim 当门户用户名。取不到就退回 email，再取不到用 sub
        'username_claim': 'preferred_username',
        # 这个 claim 里出现下面那个值的人自动是管理员。admin_claim_value 留空
        # 就不看，谁是管理员完全由门户自己的用户表说了算
        'admin_claim': 'groups',
        'admin_claim_value': '',
    },
}


def _write(doc: dict) -> None:
    """整份原子写。先写 .tmp 再 os.replace，中途断电不会留下半份 JSON。"""
    text = json.dumps(doc, ensure_ascii=False, indent=2) + '\n'
    tmp = CONF_PATH.with_suffix('.json.tmp')
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, CONF_PATH)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _coerce(raw: str, sample: Any) -> Any:
    """把 conf.ini 里的字符串转成模板里那一项的类型；转不动就原样留着。"""
    text = raw.strip()
    if isinstance(sample, bool):
        return text.lower() in ('1', 'true', 'yes', 'on')
    if isinstance(sample, int):
        try:
            return int(text)
        except ValueError:
            return sample
    return text


def _migrate() -> dict:
    """老版本的 conf.ini + nav.json 折成一份 conf.json。没有老文件就返回默认内容。

    这里只负责把老内容搬进 conf.json；导航那一段接着会被搬进数据库
    （app/store/navstore.py 的 `import_legacy`），这个文件里留着的那份不再有人读。
    """
    doc = json.loads(json.dumps(_DEFAULT))      # 深拷贝，别让迁移改到模板
    moved = []

    if _OLD_CONF.is_file():
        import configparser                      # 只有这一条路用得上，不放模块顶上
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(_OLD_CONF, encoding='utf-8')
            for section in ('server', 'auth'):
                if not parser.has_section(section):
                    continue
                for key, raw in parser[section].items():
                    # configparser 读出来一律是字符串，照搬的话生成的 conf.json 里
                    # 会是 "port": "9921"，读得出来但照着改会以为布尔值该写成字符串
                    doc[section][key] = _coerce(raw, doc[section].get(key))
            # 老 auth 段里那对用户名口令是单账号时代的东西，现在它的去处是
            # 「库里没人时建的第一个管理员」
            old_user = str(doc['auth'].pop('username', '') or '').strip()
            old_pass = str(doc['auth'].pop('password', '') or '').strip()
            if old_user and old_pass:
                doc['auth']['bootstrap_admin'] = {'username': old_user, 'password': old_pass}
            moved.append(_OLD_CONF.name)
        except configparser.Error as exc:
            print(f'[配置] 老的 {_OLD_CONF.name} 读不出来，跳过: {exc}')

    if _OLD_NAV.is_file():
        try:
            data = json.loads(_OLD_NAV.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                doc['nav'] = data
                moved.append(_OLD_NAV.name)
        except (OSError, json.JSONDecodeError) as exc:
            print(f'[配置] 老的 {_OLD_NAV.name} 读不出来，跳过: {exc}')

    if moved:
        print(f'[配置] 已把 {" 和 ".join(moved)} 的内容折进 {CONF_PATH.name}，'
              '确认没问题之后老文件可以删掉')
    return doc


def _load() -> dict:
    """读 conf.json。没有就生成一份（或从老文件迁过来），然后照常启动。"""
    if not CONF_PATH.exists():
        doc = _migrate()
        try:
            _write(doc)
            print(f'[配置] 已生成 {CONF_PATH}，请把 database 那一段填成你自己的 MySQL')
        except OSError as exc:
            print(f'[配置] 生成 {CONF_PATH} 失败，先用默认值: {exc}')
        return doc

    try:
        doc = json.loads(CONF_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        # **绝不在这里重新生成一份**：里面有数据库口令和 OIDC 的 client_secret，
        # 覆盖一次就得全部重配。停下来让人自己看一眼。
        raise SystemExit(f'[配置] {CONF_PATH} 读不出来: {exc}\n'
                         '       这个文件里装着数据库口令和 OIDC 密钥，程序不会替你覆盖它。\n'
                         '       修好里面的 JSON 再启动。')
    if not isinstance(doc, dict):
        raise SystemExit(f'[配置] {CONF_PATH} 的顶层得是一个对象')
    return doc


_doc = _load()


def _section(name: str) -> dict:
    value = _doc.get(name)
    return value if isinstance(value, dict) else {}


def _str(section: str, key: str, default: str = '') -> str:
    value = _section(section).get(key, default)
    return str(value).strip() if value is not None else ''


def _int(section: str, key: str, default: int) -> int:
    try:
        return int(_section(section).get(key, default))
    except (TypeError, ValueError):
        return default


def _bool(section: str, key: str, default: bool) -> bool:
    value = _section(section).get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


# ---------------------------------------------------------------- 服务
HOST = _str('server', 'host', '0.0.0.0') or '0.0.0.0'
PORT = _int('server', 'port', 9921)

# ---------------------------------------------------------------- 数据库
DB_HOST = _str('database', 'host', '127.0.0.1') or '127.0.0.1'
DB_PORT = _int('database', 'port', 3306)
DB_USER = _str('database', 'user', 'root') or 'root'
DB_PASSWORD = _section('database').get('password') or ''
DB_NAME = _str('database', 'name', 'portal') or 'portal'
# utf8mb4 不是可选项：三字节的 utf8 存不下 emoji，而卡片名字里很容易出现一个
DB_CHARSET = _str('database', 'charset', 'utf8mb4') or 'utf8mb4'
DB_POOL_SIZE = max(1, _int('database', 'pool_size', 8))

# ---------------------------------------------------------------- 登录
SESSION_HOURS = max(1, _int('auth', 'session_hours', 168))
COOKIE_SECURE = _bool('auth', 'cookie_secure', False)
ALLOW_LOCAL_LOGIN = _bool('auth', 'allow_local_login', True)

_bootstrap = _section('auth').get('bootstrap_admin')
_bootstrap = _bootstrap if isinstance(_bootstrap, dict) else {}
BOOTSTRAP_USERNAME = str(_bootstrap.get('username') or '').strip()
BOOTSTRAP_PASSWORD = str(_bootstrap.get('password') or '')

if not (BOOTSTRAP_USERNAME and BOOTSTRAP_PASSWORD):
    # 单账号时代那对 auth.username / auth.password。**这条兼容不能去掉**：
    # 原地升级上来的 conf.json 里只有那两项，不认的话库里一个用户都建不出来，
    # 表现成「升完级谁都登不进去」，而人手里那份 conf.json 看着完全正常。
    BOOTSTRAP_USERNAME = _str('auth', 'username')
    BOOTSTRAP_PASSWORD = str(_section('auth').get('password') or '')


def uses_default_password() -> bool:
    """第一个管理员还配着默认口令。启动时据此打一条警告（app/main.py）。"""
    return (BOOTSTRAP_USERNAME == DEFAULT_USERNAME
            and BOOTSTRAP_PASSWORD == DEFAULT_PASSWORD)


# ---------------------------------------------------------------- OIDC
OIDC_ENABLED = _bool('oidc', 'enabled', False)
OIDC_ISSUER = _str('oidc', 'issuer').rstrip('/')
OIDC_CLIENT_ID = _str('oidc', 'client_id')
OIDC_CLIENT_SECRET = _section('oidc').get('client_secret') or ''
OIDC_SCOPES = _str('oidc', 'scopes', 'openid profile email') or 'openid profile email'
OIDC_REDIRECT_URL = _str('oidc', 'redirect_url')
OIDC_AUTO_CREATE = _bool('oidc', 'auto_create_user', True)
OIDC_USERNAME_CLAIM = _str('oidc', 'username_claim', 'preferred_username') or 'preferred_username'
OIDC_ADMIN_CLAIM = _str('oidc', 'admin_claim', 'groups')
OIDC_ADMIN_CLAIM_VALUE = _str('oidc', 'admin_claim_value')

if OIDC_ENABLED and not (OIDC_ISSUER and OIDC_CLIENT_ID):
    # 开着却没填全，表现会是「点了单点登录跳过去是个 500」。启动时就说清楚
    raise SystemExit(f'[配置] {CONF_PATH} 里 oidc.enabled 是 true，'
                     '但 issuer / client_id 没填全。\n'
                     '       填好再启动，或者先把 oidc.enabled 改成 false。')

if not ALLOW_LOCAL_LOGIN and not OIDC_ENABLED:
    # 两条登录路都关了 = 谁都进不来，而且登录页上一个按钮都不会有
    raise SystemExit(f'[配置] {CONF_PATH} 里本地登录和 OIDC 都是关的，没人能登进来。\n'
                     '       打开 auth.allow_local_login，或者配好 oidc 那一段。')


def legacy_nav() -> Any:
    """老版本塞在 conf.json 里的那份导航，首次建库时搬进第一个管理员名下。

    搬完不删：这个文件是人家自己的，程序只管读。搬没搬过记在库里
    （portal_meta 表的 legacy_nav_imported），所以不会搬第二次。
    """
    return _doc.get('nav')


def db_dsn() -> str:
    """给日志用的一句话，**不带口令**。"""
    return f'{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}'


# ---------------------------------------------------------------- 前端产物
if FROZEN:
    # 前端已经打进 exe（解压在 _MEIPASS/webside）。exe 同级放一个 webside 目录
    # 就能盖掉它，换前端不用重新打包
    _external = BASE_DIR / 'webside'
    DIST_DIR = _external if _external.is_dir() else Path(getattr(sys, '_MEIPASS', BASE_DIR)) / 'webside'
else:
    DIST_DIR = ROOT_DIR / 'webside' / 'dist'
