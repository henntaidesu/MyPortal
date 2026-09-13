"""站点图标的磁盘缓存。

原来这份缓存在浏览器的 localStorage 里：一人一台机器各存一份，换台机器要重抓一遍，
还白占浏览器那点配额。搬到后端之后所有人共用一份——favicon 本来就不是谁的私有数据。

目录是自动建的，源码态在 backend/icons，打包后在 exe 同级目录。
**整个目录随时可以删**：下次访问自己会重建、重抓，没有任何副作用。

存两种东西：
    <指纹>.png / .ico / .svg …   抓到的图标，7 天
    <指纹>.miss                  抓不到（站点没图标、超时、被拒），6 小时

「抓不到」也要存。不存的话，一个没有 favicon 的站点会让每次开门户都去外网白跑一趟，
超时还得等 6 秒——这是原来前端那份缓存里最值钱的一半。
"""
import hashlib
import os
import time
from pathlib import Path
from typing import Any, Optional

from .config import ICON_DIR

HIT_TTL = 7 * 24 * 3600        # 抓到的存 7 天
MISS_TTL = 6 * 3600            # 抓不到的只存 6 小时，站点临时挂掉不至于长期没图标

# 缓存里明确记着「这个站没有图标」，和「没这条记录」不是一回事
MISS: Any = object()

_MISS_SUFFIX = '.miss'
_EXT = {
    'image/x-icon': '.ico',
    'image/vnd.microsoft.icon': '.ico',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/jpeg': '.jpg',
    'image/webp': '.webp',
    'image/svg+xml': '.svg',
}
_CTYPE = {
    '.ico': 'image/x-icon',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.jpg': 'image/jpeg',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
}
_SUFFIXES = tuple(_CTYPE) + (_MISS_SUFFIX,)


def ensure() -> None:
    """建目录。启动时调一次，每次写之前也再调一次——目录可能被人手工删掉。"""
    ICON_DIR.mkdir(parents=True, exist_ok=True)


def _key(kind: str, target: str) -> str:
    """kind 是 url / site：同一个地址当图片取和当站点解析，结果不一样，不能共用一格。"""
    return hashlib.sha256(f'{kind}:{target}'.encode('utf-8')).hexdigest()


def _ttl(suffix: str) -> int:
    return MISS_TTL if suffix == _MISS_SUFFIX else HIT_TTL


def _find(key: str) -> Optional[Path]:
    """后缀就那么几个，一个个试比 glob 稳：文件多了 glob 要扫整个目录。"""
    for suffix in _SUFFIXES:
        path = ICON_DIR / (key + suffix)
        if path.exists():
            return path
    return None


def _write(path: Path, data: bytes) -> None:
    """先写临时文件再改名。中途断电也不会留下半张图被当成好的发出去。"""
    ensure()
    tmp = path.with_suffix(path.suffix + '.tmp')
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError as exc:
        print(f'[icon] 写缓存失败（不影响这次请求）: {exc}')
        tmp.unlink(missing_ok=True)


def load(kind: str, target: str) -> Any:
    """命中返回 (字节, content-type)；命中「抓不到」返回 MISS；没有记录或已过期返回 None。"""
    path = _find(_key(kind, target))
    if path is None:
        return None
    try:
        if time.time() - path.stat().st_mtime > _ttl(path.suffix):
            path.unlink(missing_ok=True)
            return None
        if path.suffix == _MISS_SUFFIX:
            return MISS
        return path.read_bytes(), _CTYPE.get(path.suffix, 'image/x-icon')
    except OSError:
        return None            # 正好被人删了 / 读不了，当没缓存，重抓一次就是


def save(kind: str, target: str, data: bytes, ctype: str) -> None:
    key = _key(kind, target)
    _drop(key)                 # 上次可能记的是 .miss，或者换了图片格式，先清干净
    _write(ICON_DIR / (key + _EXT.get(ctype.split(';')[0].strip().lower(), '.ico')), data)


def save_miss(kind: str, target: str) -> None:
    key = _key(kind, target)
    _drop(key)
    _write(ICON_DIR / (key + _MISS_SUFFIX), b'')


def _drop(key: str) -> None:
    for suffix in _SUFFIXES:
        (ICON_DIR / (key + suffix)).unlink(missing_ok=True)


def purge() -> int:
    """删过期文件，返回删掉几个。挂在 main.py 每小时那趟清理里。"""
    if not ICON_DIR.is_dir():
        return 0
    now = time.time()
    removed = 0
    for path in ICON_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            # .tmp 是写到一半留下的残骸，过一小时还在就说明那次写崩了
            ttl = 3600 if path.suffix == '.tmp' else _ttl(path.suffix)
            if now - path.stat().st_mtime > ttl:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed


def describe() -> str:
    """给启动日志用的一句话。"""
    if not ICON_DIR.is_dir():
        return f'{ICON_DIR}（还没建）'
    files = [p for p in ICON_DIR.iterdir() if p.is_file()]
    total = sum(p.stat().st_size for p in files) if files else 0
    return f'{ICON_DIR}（{len(files)} 个，{total // 1024} KB）'
