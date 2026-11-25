"""站点图标代理。

浏览器直接读跨域图片会被 CORS 挡住，所以由后端代取一次。
原来这段在 vite.config.js 里，只有 dev 时才在；搬到后端后打包部署也能用。

两种调法，前端两种都会发：
    /api/icon?url=<图片地址>    直接代取这张图
    /api/icon?site=<站点根地址>  由后端解析 <link rel="icon">，解析不到再退 /favicon.ico

取到的图（和「取不到」这件事）都落在磁盘上，见 app/iconcache.py。
所以同一个站点全公司只去外网抓一次，前端不用再自己缓存。

**要求登录**：这个接口会替调用方发一次 HTTP 请求，不该对匿名访问者开放。
内网地址**只有管理员**能让它去抓（和门户代理同一条规矩，app/proxy.py 的 `_check_address`）：
以前谁都能拿 `?url=http://10.x.x.x/...` 让门户替他往内网发 GET。普通用户内网卡片的图标
只能靠管理员抓过一次留下的缓存（缓存按地址存，大家共用）。跳转不交给 httpx 自动跟，
每一跳都重新查一遍地址——不然一个公网地址 302 到内网就绕过去了。
链路本地（169.254.x.x，云元数据）谁都不行。其余限制：只认 http/https、
只收 512 KB 以内且确实是图片的响应、6 秒超时、最多跳 3 次。
"""
import re
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response

from . import iconcache
from .auth import require_login
from .proxy import _addresses, _bare_host, _ip, _ip_forbidden, _ip_public

router = APIRouter(prefix='/api', tags=['icon'],
                   dependencies=[Depends(require_login)])

_MAX_HOPS = 3

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


async def _allowed(url: str, private_ok: bool) -> bool:
    parts = urlparse(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return False
    name = _bare_host(parts.hostname)
    if not private_ok and '.' not in name and _ip(name) is None:
        return False                       # nas、gitlab 这种单标签名字只会是内网
    addrs = await _addresses(parts.hostname)
    if not addrs or any(_ip_forbidden(a) for a in addrs):
        return False
    return private_ok or all(_ip_public(a) for a in addrs)


async def _get(http: httpx.AsyncClient, url: str, private_ok: bool, **kwargs) -> httpx.Response | None:
    """GET，自己跟跳转，每一跳都过 `_allowed`。"""
    for _ in range(_MAX_HOPS + 1):
        if not await _allowed(url, private_ok):
            return None
        resp = await http.get(url, **kwargs)
        if not resp.is_redirect:
            return resp
        url = urljoin(str(resp.url), resp.headers.get('location', ''))
    return None


async def _fetch_image(http: httpx.AsyncClient, url: str, private_ok: bool) -> tuple[bytes, str] | None:
    try:
        resp = await _get(http, url, private_ok)
    except httpx.HTTPError:
        return None
    if resp is None:
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


async def _resolve_site(http: httpx.AsyncClient, origin: str,
                        private_ok: bool) -> tuple[bytes, str] | None:
    candidates: list[str] = []
    try:
        page = await _get(http, origin, private_ok, headers={'accept': 'text/html'})
        if page is not None and page.status_code == 200 and 'html' in page.headers.get('content-type', ''):
            candidates = _icon_links(page.text, str(page.url))
    except httpx.HTTPError:
        pass

    candidates += [urljoin(origin + '/', 'favicon.ico'), urljoin(origin + '/', 'favicon.png')]

    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        got = await _fetch_image(http, url, private_ok)
        if got:
            return got
    return None


def _image(data: bytes, ctype: str) -> Response:
    return Response(data, media_type=ctype,
                    headers={'cache-control': 'public, max-age=86400'})


@router.get('/icon')
async def icon(url: str = '', site: str = '', user: dict = Depends(require_login)):
    if not url and not site:
        raise HTTPException(400, 'url 或 site 至少给一个')

    kind = 'url' if url else 'site'
    target = _check_url(url or site)

    cached = iconcache.load(kind, target)
    if cached is iconcache.MISS:
        raise HTTPException(404, 'not found')       # 上次就没抓到，别再去外网白跑
    if cached is not None:
        return _image(*cached)

    private_ok = (user.get('role') or 'user') == 'admin'
    if not await _allowed(target, private_ok):
        # 不记 miss：管理员随后来抓的话应该抓得到
        raise HTTPException(404, 'not found')
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False,
                                 headers={'user-agent': 'Portal-IconProxy/1.0'}) as http:
        got = (await _fetch_image(http, target, private_ok) if url
               else await _resolve_site(http, target, private_ok))

    if got is None:
        iconcache.save_miss(kind, target)
        raise HTTPException(404, 'not found')

    data, ctype = got
    iconcache.save(kind, target, data, ctype)
    return _image(data, ctype)
