"""启动配置，全部来自 conf.ini（开发时在 backend/ 下，打包成 exe 后在 exe 同级目录）。

这里只放「连上数据库之前就必须知道」的东西：数据库连接信息和监听端口。
其余所有配置都在数据库的 settings 表里，见 app/settings.py——那些改完不用重启。

conf.ini 不存在时会自动生成一份，然后停下来让人去填数据库密码。
这个文件里有密码，已经在 .gitignore 里。
"""
import configparser
import sys
from pathlib import Path

FROZEN = getattr(sys, 'frozen', False)                     # PyInstaller 打出来的 exe

if FROZEN:
    # 打包后一律以 exe 所在目录为准。别用 sys._MEIPASS——那是每次启动现解压的临时
    # 目录，conf.ini 写进去下次启动就没了，表现成「改了配置怎么不生效」
    BASE_DIR = ROOT_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent      # backend/
    ROOT_DIR = BASE_DIR.parent                             # Portal/

CONF_PATH = BASE_DIR / 'conf.ini'

# 站点图标的磁盘缓存目录，启动时自动建（app/iconcache.py 的 ensure）。
# 和 conf.ini 一样挂在 BASE_DIR 下：源码态是 backend/icons，打包后是 exe 同级的 icons。
# 绝不能放 sys._MEIPASS——那是每次启动现解压的临时目录，缓存进去等于没缓存。
# 不做成配置项：它和 DIST_DIR 一样，跟着仓库结构走就够了，多一项就多一处能填错的地方。
ICON_DIR = BASE_DIR / 'icons'

# 自动生成的 conf.ini 内容。不写注释：改这里的默认值同时也改了新装机器拿到的模板
_TEMPLATE = """[database]
host = 127.0.0.1
port = 3306
user = portal
password = CHANGE_ME
database = portal_sso
charset = utf8mb4
pool_size = 5
ping_interval = 60

[server]
host = 0.0.0.0
port = 9921
"""


def _load() -> configparser.ConfigParser:
    """读 conf.ini。没有就生成一份，然后停下来让人去填数据库密码。

    interpolation=None：不然密码里的 % 会被 configparser 当成变量插值语法炸掉。
    不开 inline_comment_prefixes：不然密码里的 # 会被当成行尾注释截断。
    两个坑都很隐蔽，出问题时只会表现成「密码不对」。
    """
    if not CONF_PATH.exists():
        CONF_PATH.write_text(_TEMPLATE, encoding='utf-8')
        sys.exit(f'[配置] 已生成 {CONF_PATH}\n'
                 '       先把 [database] 改成你的 MySQL 连接信息，再重新启动。\n'
                 '       库和表会自动建，不用手工建库。')
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(CONF_PATH, encoding='utf-8')
    except configparser.Error as exc:
        sys.exit(f'[配置] {CONF_PATH} 格式不对: {exc}')
    return parser


_conf = _load()


def _str(section: str, key: str, default: str = '') -> str:
    return (_conf.get(section, key, fallback=default) or '').strip()


def _int(section: str, key: str, default: int) -> int:
    try:
        return int(_str(section, key, str(default)) or default)
    except ValueError:
        return default


# ---------------------------------------------------------------- 数据库
DB_HOST = _str('database', 'host', '127.0.0.1')
DB_PORT = _int('database', 'port', 3306)
DB_USER = _str('database', 'user', 'root')
DB_PASSWORD = _conf.get('database', 'password', fallback='')   # 密码不 strip，首尾空格可能是真的
DB_NAME = _str('database', 'database', 'portal_sso')
DB_CHARSET = _str('database', 'charset', 'utf8mb4')
DB_POOL_SIZE = max(1, _int('database', 'pool_size', 5))
DB_PING_INTERVAL = _int('database', 'ping_interval', 60)

# ---------------------------------------------------------------- 服务
HOST = _str('server', 'host', '0.0.0.0')
PORT = _int('server', 'port', 9921)

# 前端打包产物。跟着仓库结构走，没必要做成可配置项——
# 它在挂载静态站点时就要用上，那会儿还不该去碰数据库
if FROZEN:
    # 前端已经打进 exe（解压在 _MEIPASS/webside）。exe 同级放一个 webside 目录
    # 就能盖掉它，换前端不用重新打包
    _external = BASE_DIR / 'webside'
    DIST_DIR = _external if _external.is_dir() else Path(getattr(sys, '_MEIPASS', BASE_DIR)) / 'webside'
else:
    DIST_DIR = ROOT_DIR / 'webside' / 'dist'
