"""业务系统注册表。

一个 client 就是一个「我自己写的系统」。改完 clients.json 不用重启，
下次请求进来发现文件改了会自动重新加载。
"""
import json
import threading
from typing import Any, Optional

from .config import CLIENTS_PATH
from .security import new_token

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}
_mtime: float = -1.0

TEMPLATE = {
    'demo': {
        'name': '示例系统',
        'secret': '请换成随机长字符串',
        'redirect_uris': ['http://127.0.0.1:8801/sso/callback'],
        'logout_uri': 'http://127.0.0.1:8801/sso/logout-notify',
    }
}


def _normalize(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for client_id, cfg in (raw or {}).items():
        if not isinstance(cfg, dict):
            continue
        uris = cfg.get('redirect_uris') or ([cfg['redirect_uri']] if cfg.get('redirect_uri') else [])
        out[str(client_id)] = {
            'client_id': str(client_id),
            'name': cfg.get('name') or str(client_id),
            'secret': str(cfg.get('secret') or ''),
            'redirect_uris': [str(u).strip() for u in uris if str(u).strip()],
            'logout_uri': str(cfg.get('logout_uri') or '').strip(),
            'home_url': str(cfg.get('home_url') or '').strip(),
        }
    return out


def load(force: bool = False) -> dict[str, dict[str, Any]]:
    global _cache, _mtime
    with _lock:
        try:
            mtime = CLIENTS_PATH.stat().st_mtime
        except OSError:
            _cache, _mtime = {}, -1.0
            return _cache
        if force or mtime != _mtime:
            try:
                _cache = _normalize(json.loads(CLIENTS_PATH.read_text(encoding='utf-8')))
                _mtime = mtime
            except (json.JSONDecodeError, OSError) as exc:
                # 配置写坏了不要让已经在跑的服务崩掉，继续用上一版
                print(f'[clients] {CLIENTS_PATH} 解析失败，沿用上一次的配置: {exc}')
        return _cache


def get(client_id: str) -> Optional[dict[str, Any]]:
    return load().get((client_id or '').strip())


def public_list() -> list[dict[str, str]]:
    """给前端下拉框用：只给 id 和名字，绝不带 secret。"""
    return [{'client_id': c['client_id'], 'name': c['name']}
            for c in sorted(load().values(), key=lambda c: c['name'])]


def check_redirect_uri(client: dict[str, Any], redirect_uri: str) -> Optional[str]:
    """精确匹配白名单。没传就用第一条注册地址。

    这是整套流程的安全底线：放松成前缀匹配或允许任意地址，
    票据就能被诱导送到别人的服务器上。
    """
    if not redirect_uri:
        return client['redirect_uris'][0] if client['redirect_uris'] else None
    return redirect_uri if redirect_uri in client['redirect_uris'] else None


def write_template() -> None:
    if CLIENTS_PATH.exists():
        return
    CLIENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(json.dumps(TEMPLATE))
    data['demo']['secret'] = new_token()
    CLIENTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def save(clients: dict[str, dict[str, Any]]) -> None:
    """manage.py 用。写回时把内部字段 client_id 去掉，保持文件干净。"""
    out = {}
    for client_id, cfg in clients.items():
        cfg = dict(cfg)
        cfg.pop('client_id', None)
        out[client_id] = {k: v for k, v in cfg.items() if v not in ('', [], None)}
    CLIENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLIENTS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    load(force=True)
