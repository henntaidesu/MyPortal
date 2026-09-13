"""conf.json：门户的全部配置和全部数据，就这一个文件。

（开发时在 backend/ 下，打包成 exe 后在 exe 同级目录。）

    {
      "server": {"host": "0.0.0.0", "port": 9921},
      "auth":   {"username": "admin", "password": "admin",
                 "session_hours": 168, "cookie_secure": false},
      "nav":    {"title": "我的门户", "theme": "auto", "items": [...]}
    }

`server` 和 `auth` 启动时读一次，改完要重启；`nav` 是页面上随手在改的，
由 app/navstore.py 走这里的 `replace_section` 写回去。**写的时候是先重读整份文件、
只换掉 nav 那一段、再整份原子写回**，所以服务跑着的时候手改 auth 也不会被存导航
那一下盖掉（改完仍然要重启才生效）。

没有这个文件时按默认内容生成一份**然后接着跑**（默认口令 admin/admin，启动时会一直警告）。
老版本留下的 conf.ini / nav.json 会被自动折进来，不用手工搬。

文件里有明文口令，已经在 .gitignore 里。
"""
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

FROZEN = getattr(sys, 'frozen', False)                     # PyInstaller 打出来的 exe

if FROZEN:
    # 打包后一律以 exe 所在目录为准。别用 sys._MEIPASS——那是每次启动现解压的临时
    # 目录，conf.json 写进去下次启动就没了，改了口令、加了卡片全白改
    BASE_DIR = ROOT_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent      # backend/
    ROOT_DIR = BASE_DIR.parent                             # Portal/

CONF_PATH = BASE_DIR / 'conf.json'

# 老版本的两个文件，只在生成 conf.json 那一次读，读完不删——删用户的数据这种事
# 不该由程序替人决定，启动日志里会说一句「可以删了」
_OLD_CONF = BASE_DIR / 'conf.ini'
_OLD_NAV = BASE_DIR / 'nav.json'

# 站点图标的磁盘缓存目录，启动时自动建（app/iconcache.py 的 ensure）。
# 不放进 conf.json：那是一堆几十 KB 的二进制，而且随时可以重抓。
# 绝不能放 sys._MEIPASS——那是每次启动现解压的临时目录，缓存进去等于没缓存。
ICON_DIR = BASE_DIR / 'icons'

DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'admin'

# 自动生成的 conf.json 内容。nav 是 null 而不是一份空导航：
# 前端靠「nav 为 null」判断这是台还没配过的机器，会把本机缓存那份推上来。
# 给成 {"items": []} 的话，老用户第一次打开会看到一片空，而且缓存再也推不上去。
_DEFAULT: dict[str, Any] = {
    'server': {'host': '0.0.0.0', 'port': 9921},
    'auth': {
        'username': DEFAULT_USERNAME,
        'password': DEFAULT_PASSWORD,
        'session_hours': 168,
        # 只有 https 部署才能开。http 下开了浏览器会直接丢掉登录 Cookie，
        # 表现成「登录成功后立刻又变回未登录」
        'cookie_secure': False,
    },
    'nav': None,
}

# 整份文件的写锁。存导航是「读-改-写」整份文件，两个请求同时进来会互相盖掉半截。
# 单进程跑（见 app/main.py），一把进程内的锁就够。
_lock = threading.Lock()


def _write(doc: dict) -> None:
    """整份原子写。先写 .tmp 再 os.replace——中途断电不会留下半份 JSON，
    而这个文件里连口令带导航全在，写坏了就是整个门户起不来。"""
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
    """老版本的 conf.ini + nav.json 折成一份 conf.json。没有老文件就返回默认内容。"""
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
                    # configparser 读出来一律是字符串。照搬的话生成的 conf.json 里会是
                    # "port": "9921"、"cookie_secure": "false"，读得出来但看着就像填错了，
                    # 而且照着改会以为布尔值该写成字符串。按模板里的类型转回去。
                    doc[section][key] = _coerce(raw, doc[section].get(key))
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
            print(f'[配置] 已生成 {CONF_PATH}')
        except OSError as exc:
            # 目录只读之类。配置读得出来就还能跑，只是存不下导航
            print(f'[配置] 生成 {CONF_PATH} 失败，先用默认值: {exc}')
        return doc

    try:
        doc = json.loads(CONF_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        # **绝不在这里重新生成一份**：这个文件里装着人家全部的导航，
        # 冒然覆盖等于把数据删了。停下来让人自己看一眼。
        raise SystemExit(f'[配置] {CONF_PATH} 读不出来: {exc}\n'
                         '       这个文件里装着口令和全部导航数据，程序不会替你覆盖它。\n'
                         '       修好里面的 JSON 再启动；真不要了就先改名备份，再删掉重启。')
    if not isinstance(doc, dict):
        raise SystemExit(f'[配置] {CONF_PATH} 的顶层得是一个对象 {{...}}')
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

# ---------------------------------------------------------------- 登录
AUTH_USERNAME = _str('auth', 'username', DEFAULT_USERNAME) or DEFAULT_USERNAME
AUTH_PASSWORD = _str('auth', 'password', DEFAULT_PASSWORD)
SESSION_HOURS = max(1, _int('auth', 'session_hours', 168))
COOKIE_SECURE = _bool('auth', 'cookie_secure', False)

if not AUTH_PASSWORD:
    # 空口令等于没有登录，而人多半是想设一个却写漏了。直接停下来问，别默默放行
    raise SystemExit(f'[配置] {CONF_PATH} 里 auth.password 是空的。\n'
                     '       填一个口令再启动；真不想要登录就把这个门户挡在内网里。')


def uses_default_password() -> bool:
    """还在用默认口令。启动时据此打警告，页面上也会提示一句。"""
    return AUTH_PASSWORD == DEFAULT_PASSWORD


# ---------------------------------------------------------------- 给 navstore 用

def read_section(name: str) -> Any:
    """从**磁盘上那份**读一个顶层键。

    不返回内存里 `_doc` 的那份：nav 会被改，而 `_doc` 是启动时的快照，
    拿它当数据源的话，改完刷新页面会看到旧的。
    """
    try:
        doc = json.loads(CONF_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return _doc.get(name)
    except (OSError, json.JSONDecodeError) as exc:
        print(f'[配置] 读 {CONF_PATH} 失败，退回启动时那份: {exc}')
        return _doc.get(name)
    return doc.get(name) if isinstance(doc, dict) else None


def replace_section(name: str, value: Any) -> None:
    """只换掉一个顶层键，整份原子写回。

    每次都重读一遍磁盘上那份再改：服务跑着的时候有人手改了 auth，
    存一次导航不该把人家的修改抹掉（改动仍然要重启才生效）。
    """
    with _lock:
        try:
            doc = json.loads(CONF_PATH.read_text(encoding='utf-8'))
            if not isinstance(doc, dict):
                doc = dict(_doc)
        except (OSError, json.JSONDecodeError):
            doc = dict(_doc)        # 文件没了或坏了，拿启动时那份兜底，别把这次改动丢掉
        doc[name] = value
        _write(doc)


# ---------------------------------------------------------------- 前端产物
# 跟着仓库结构走，没必要做成可配置项——它在挂载静态站点时就要用上
if FROZEN:
    # 前端已经打进 exe（解压在 _MEIPASS/webside）。exe 同级放一个 webside 目录
    # 就能盖掉它，换前端不用重新打包
    _external = BASE_DIR / 'webside'
    DIST_DIR = _external if _external.is_dir() else Path(getattr(sys, '_MEIPASS', BASE_DIR)) / 'webside'
else:
    DIST_DIR = ROOT_DIR / 'webside' / 'dist'
