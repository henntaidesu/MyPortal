"""集中配置。全部可以用环境变量或 backend/.env 覆盖，不改代码。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
ROOT_DIR = BASE_DIR.parent                                 # Portal/


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取，装 python-dotenv 就为了这十行不值得。"""
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / '.env')


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


HOST = os.environ.get('HOST', '0.0.0.0')
PORT = _int('PORT', 9921)

DB_PATH = Path(os.environ.get('DB_PATH', BASE_DIR / 'data' / 'sso.db'))
CLIENTS_PATH = Path(os.environ.get('CLIENTS_PATH', BASE_DIR / 'clients.json'))
DIST_DIR = Path(os.environ.get('DIST_DIR', ROOT_DIR / 'webside' / 'dist'))

# 会话 Cookie
SESSION_COOKIE = os.environ.get('SESSION_COOKIE', 'sso_session')
SESSION_TTL_HOURS = _int('SESSION_TTL_HOURS', 12)
# 走 https 时置 true；http 下置 true 浏览器会直接丢掉 Cookie，所以默认 false
COOKIE_SECURE = _bool('COOKIE_SECURE', False)

# 一次性票据有效期。只够浏览器跳一次 + 业务系统后端换一次，不要调大
TICKET_TTL_SECONDS = _int('TICKET_TTL_SECONDS', 60)

# 登录失败限流：同一用户名 WINDOW 秒内失败超过 MAX 次就锁一会儿
LOGIN_MAX_FAILS = _int('LOGIN_MAX_FAILS', 5)
LOGIN_FAIL_WINDOW = _int('LOGIN_FAIL_WINDOW', 300)

# 业务系统登出通知的超时
LOGOUT_NOTIFY_TIMEOUT = _int('LOGOUT_NOTIFY_TIMEOUT', 3)
