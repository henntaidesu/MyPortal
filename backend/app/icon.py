"""站点图标代理。

浏览器直接读跨域图片会被 CORS 挡住，所以由后端代取一次。
原来这段在 vite.config.js 里，只有 dev 时才在；搬到后端后打包部署也能用。

两种调法，前端两种都会发：
    /api/icon?url=<图片地址>    直接代取这张图
    /api/icon?site=<站点根地址>  由后端解析 <link rel="icon">，解析不到再退 /favicon.ico

取到的图（和「取不到」这件事）都落在磁盘上，见 app/iconcache.py。
所以同一个站点全公司只去外网抓一次，前端不用再自己缓存。
"""
import re
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from . import iconcache
from .deps import current_session

router = APIRouter(prefix='/api', tags=['icon'])

_MAGIC = (
    b'\x00\x00\x01\x00',      # ico
    b'\x89PNG',               # png
    b'GIF8',                  # gif
    b'\xff\xd8\xff',          # jpeg
    b'RIFF',                  # webp
)
_MAX_BYTES = 512 * 1024
_TIMEOUT = 6

_LINK_TAG = re.compile(r'<link\b[^>]*>', re.I)
_REL = re.compile(r'\brel\s*=\s*["\']?([^"\'>]+)', re.I)
_HREF = re.compile(r'\bhref\s*=\s*["\']([^"\']+)', re.I)
_SIZES = re.compile(r'\bsizes\s*=\s*["\']?(\d+)', re.I)


def _looks_like_image(buf: bytes) -> bool:
    if any(buf.startswith(m) for m in _MAGIC):
        return True
    head = buf[:200].decode('utf-8', 'ignore').strip().lower()
    return head.startswith('<svg') or head.startswith('<?xml')


def _check_url(url: str) -> str:
    parts = urlparse(url)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise HTTPException(400, 'bad url')
    return url


async def _fetch_image(http: httpx.AsyncClient, url: str) -> tuple[bytes, str] | None:
    try:
        resp = await http.get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    data, ctype = resp.content, resp.headers.get('content-type', '')
    if not data or len(data) > _MAX_BYTES:
        return None
    if not ctype.lower().startswith('image/') and not _looks_like_image(data):
        return None
    return data, ctype if ctype.lower().startswith('image/') else 'image/x-icon'


def _icon_links(html: str, base: str) -> list[str]:
    """从页面里挑 <link rel="icon">，大的排前面。"""
    found: list[tuple[int, str]] = []
    for tag in _LINK_TAG.findall(html[:200_000]):
        rel_match = _REL.search(tag)
        rel = rel_match.group(1).lower() if rel_match else ''
        # shortcut icon / apple-touch-icon / mask-icon 都算
        if not any('icon' in token for token in rel.split()):
            continue
        href = _HREF.search(tag)
        if not href:
            continue
        sizes = _SIZES.search(tag)
        found.append((int(sizes.group(1)) if sizes else 0,
                      urljoin(base, href.group(1).strip())))
    found.sort(key=lambda pair: -pair[0])
    return [url for _, url in found]


async def _resolve_site(http: httpx.AsyncClient, origin: str) -> tuple[bytes, str] | None:
    candidates: list[str] = []
    try:
        page = await http.get(origin, headers={'accept': 'text/html'})
        if page.status_code == 200 and 'html' in page.headers.get('content-type', ''):
            candidates = _icon_links(page.text, str(page.url))
    except httpx.HTTPError:
        pass

    candidates += [urljoin(origin + '/', 'favicon.ico'), urljoin(origin + '/', 'favicon.png')]

    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        got = await _fetch_image(http, url)
        if got:
            return got
    return None


def _image(data: bytes, ctype: str) -> Response:
    return Response(data, media_type=ctype,
                    headers={'cache-control': 'public, max-age=86400'})


@router.get('/icon')
async def icon(request: Request, url: str = '', site: str = ''):
    # 要求登录：这个接口会替调用方发请求，不该对匿名访问者开放
    if current_session(request) is None:
        raise HTTPException(401, '未登录')
    if not url and not site:
        raise HTTPException(400, 'url 或 site 至少给一个')

    kind = 'url' if url else 'site'
    target = _check_url(url or site)

    cached = iconcache.load(kind, target)
    if cached is iconcache.MISS:
        raise HTTPException(404, 'not found')       # 上次就没抓到，别再去外网白跑
    if cached is not None:
        return _image(*cached)

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, max_redirects=3,
                                 headers={'user-agent': 'Portal-IconProxy/1.0'}) as http:
        got = await _fetch_image(http, target) if url else await _resolve_site(http, target)

    if got is None:
        iconcache.save_miss(kind, target)
        raise HTTPException(404, 'not found')

    data, ctype = got
    iconcache.save(kind, target, data, ctype)
    return _image(data, ctype)
