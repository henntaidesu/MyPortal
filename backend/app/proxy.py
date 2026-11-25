"""门户代理：把卡片指向的那个站借门户这个口子转出去。

门户进程跑在**能打开那个站**的网络里（内网机器、或者能直连目标站点的那台机器），
人在外面只连得上门户。卡片上把「门户代理」打开之后，卡片的链接就从
`http://192.168.1.10:8080` 换成 `/api/proxy/<卡片 id>`，请求先到门户，
由门户转给那台机器，响应原样带回来。

    浏览器 ──同源──> 门户(9921) ──> 192.168.1.10:8080 / jp.mercari.com

**要登录**，而且这是仓库里最该要登录的一个接口：它把门户的网络可达性借给了调用方。
目标地址不是调用方给的，是拿卡片 id 去**当前登录这个人自己的**卡片里查出来的
（`navstore.cards(user_id)`）；整站模式下地址里虽然带了主机名，但那个主机必须落在
这张卡片的白名单内（见 app/proxyrewrite.py 的 `allow_for`），
**或者**解析出来全是公网地址（`_public`）——后一条是给「从页面里点链接跳到外站」用的，
外站那一页也要走代理，不然又从用户自己的出口 IP 直连了。
**这两道是安全边界，别去掉**——不拦的话这个代理对登录者（以及被代理页面里的脚本）
就是「想连哪台连哪台」的开放中继，而门户多半正站在内网里。

## 两种模式，卡片上选

`proxy: true`（**直接转发**）——只转卡片那一台机器，正文一个字不改。
内网后台、路由器管理页、NAS 这些自己用相对地址的系统，这样就够了，也最不容易出事。

    /api/proxy/<id>          → 307 到下面那条，把卡片地址里的路径和查询串补上
    /api/proxy/<id>/<路径>   → <卡片地址的 origin>/<路径>

`proxy: 'site'`（**整站**）——一张卡片转一整族主机，并且改写响应正文里的地址。
公网站点（メルカリ、ヤフオク、PayPayフリマ）必须用这个：它们的页面、脚本、图片
散在好几个域名上，写的又都是完整地址，不改写的话浏览器会绕过门户直连，
从用户自己的出口 IP 打过去。

    /api/proxy/<id>/__portal__/<http|https>/<主机[:端口]>/<路径>

正文改写在 app/proxyrewrite.py，运行时那层在 app/proxyhook.py（注入页面的脚本），
各个站自己的规矩在 app/proxy_webside/。卡片地址的主机名要是被
`app/proxy_webside/` 里某个站点模块认领了，`proxy: true` 会**自动按整站办**——
那几个站直接转发必然是坏的，没必要让人再去卡片上改一次。

## Cookie 代理（卡片上另一个开关）

卡片上把「Cookie 代理」也打开之后，上游的登录态**存在服务器上**，而不是只存在
浏览器里：发给上游的 `Cookie` 头以服务器那个罐子为准，上游的 `Set-Cookie`
既进罐子也照常交给浏览器。换设备、换浏览器、清缓存都不掉登录。
罐子和挑选规则在 app/cookiejar.py，加密在 app/secretbox.py。

这里只有三个接点：`_cookie_out()`（请求侧，合并罐子和浏览器那两份）、
`_save_cookies()`（响应侧）、以及 WebSocket 握手那一处。

## 浏览器里的登录也按门户用户分开

卡片分享出去之后，几个门户用户用的是**同一个**卡片 id、同一条 `/api/proxy/<id>/` 路径。
同一个浏览器里换个门户账号打开它，浏览器照样把上一个人在上游的登录 Cookie 带过来，
等于连登录一起分享了——而分享的本意只是配置。所以这条路径底下另种一枚 HttpOnly 的
`portal_proxy_owner`（门户用户 id），见 `_cookie_owner` / `_owner_cookies`：
对得上才把浏览器那份 Cookie 转给上游，对不上（或者没有）就一条都不转，
顺手请浏览器把它们删掉、把标记换成现在这个人。localStorage 那一半在 app/proxyhook.py。

「这个人」指的是 `Target.login`：通常就是登着门户的人；分享人给他勾了「共享登录」时
是分享人。罐子、Cookie 标记、本地存储前缀三处都按它算，所以共享登录的人用的是分享人那份。

还有浏览器缓存这一道：注入过脚本的 HTML 是按人的（脚本里带着本地存储前缀），地址却人人一样，
所以它一律 `no-store`（`_no_store`），导航请求的条件头也摘掉（`_CONDITIONAL`）。
门户登录 / 退出时整个源的缓存清一次（app/auth.py 的 `set_cookie` / `clear_cookie`）。

## 这里不做

- **不缓存**：转发的是人家系统的动态页面，缓存策略原样带回去，由浏览器认。
- **不做「谁能访问哪台机器」这种细粒度授权**：卡片是谁的，谁就能通过它转发；
  管理员把卡片分享给了谁（app/navstore.py 的 `shared`），谁也能，仅此而已。归属校验在 `_target()` 那一句里——查的是这个人自己的目标表，
  不是先查全局表再比对 user_id（比对写法漏一次，就是拿到别人的卡片 id
  就能借门户往那台机器上打）。
- **不改请求体**：GET 的查询串里那种「返回地址」会还原成上游的真实地址，
  POST 表单里同样的东西不管——那得把请求体整个读进内存，而这条路也要走大文件上传。

## 同源带来的那个代价

被代理的页面在浏览器眼里和门户同源，所以它里面的脚本能拿门户的会话去调
`/api/nav`。内网后台是自己人，公网站点可就不是了（页面里还挂着第三方广告脚本）。
末尾那个 `Fallback` 顺手挡了一道：带 Referer 的 `/api/xxx` 请求会被转回代理底下，
所以那些脚本按同源去摸门户接口时，摸到的是它自己那台上游。绕是绕得开的
（脚本可以把 Referer 关掉），真要紧的话别把要紧的站和门户放在一起。
"""
import asyncio
import contextlib
import inspect
import ipaddress
import re
import time
from typing import Any, NamedTuple, Optional
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import diagnostics, cookiejar, navstore, proxy_webside as webside, proxyrewrite, secretbox
from .auth import COOKIE_NAME, client_ip, require_login, user_from_token
from .config import COOKIE_SECURE
from .proxyrewrite import MARK, Rewriter

PREFIX = '/api/proxy'

router = APIRouter(prefix=PREFIX, tags=['proxy'])

# 转发时原样带过去的方法。没有 TRACE：它会把请求首部回显出来，代理后面不该留这个
_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']

# 逐跳首部（RFC 9110 7.6.1）：这些讲的是「浏览器↔门户」这一跳自己的事，
# 既不能原样转给上游，也不能原样带回给浏览器。
# 尤其是 transfer-encoding：httpx 交给我们的已经是拆好块的字节了，
# 把 chunked 这个头也带回去，浏览器会照着再拆一遍，拿到的是一堆长度前缀。
_HOP = frozenset({
    'connection', 'keep-alive', 'transfer-encoding', 'upgrade', 'te', 'trailer',
    'proxy-authenticate', 'proxy-authorization', 'proxy-connection',
})

# 来访者信息由门户自己填（直接转发模式下）。顺手把调用方带上来的同名首部丢掉——
# 这几个头谁都能伪造，转给上游等于让上游的 IP 白名单形同虚设
_FORWARDED = frozenset({'x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host',
                        'forwarded', 'x-real-ip',
                        # 门户前面挂着 CDN（Cloudflare 那类）时它们塞进来的。**只在走公网域名时
                        # 才有**，所以表现成「内网地址访问好好的，走域名就坏」：
                        # 上游自己多半也在 Cloudflare 后面，见到 cf-connecting-ip 直接 403
                        # （メルカリ 就是这样，接口一片 403，页面换成「エラーが発生しました」），
                        # 而且这几个头里装着用户的真实 IP 和国家，整站模式本来就是要藏它们的
                        'cdn-loop', 'true-client-ip', 'x-client-ip', 'via'})


def _dropped(low: str) -> bool:
    """转给上游前要丢掉的请求首部（逐跳 + 来访者信息 + CDN 塞进来的那些）。

    `cf-` 开头的整族都丢：cf-connecting-ip / cf-ipcountry / cf-ray / cf-visitor 之外，
    开了 Cloudflare Access 的还会带 `cf-access-jwt-assertion`——那是登录**门户**的凭据，
    和 portal_session 一个性质，更不能交给上游。
    """
    return low in _HOP or low in _FORWARDED or low == 'host' or low.startswith('cf-')

# 响应里要摘掉的几个首部。它们讲的都是「这个源站」的规矩，而现在页面顶着的是
# 门户这个域名，照搬过来只会拦到自己：
#   strict-transport-security  最要命的一个。浏览器会把**门户**这个域名记成
#                              「只许 https」，而且一记就是一年。http 部署的门户
#                              从此打不开，清都不好清（要进浏览器设置删 HSTS）。
#   content-security-policy    是照着上游那个域名写的。表单 action、脚本来源、
#                              连接目标现在全在门户这个域名下，一条都对不上。
#   referrer-policy            上游要是关掉 Referer，末尾那条兜根绝对地址的路就断了。
#   x-frame-options / CO*P     讲的是跨源怎么用这份响应，代理底下全是同源，留着只会添乱。
#   clear-site-data            上游一句 `"cookies"` 能把门户自己的登录 Cookie 清掉。
#   alt-svc                    指的是上游的 h3 端口，浏览器却会记在门户这个域名上。
_STRIP = frozenset({
    'strict-transport-security', 'public-key-pins', 'public-key-pins-report-only',
    'content-security-policy', 'content-security-policy-report-only',
    'referrer-policy', 'x-frame-options', 'clear-site-data', 'alt-svc',
    'cross-origin-opener-policy', 'cross-origin-embedder-policy', 'cross-origin-resource-policy',
    'report-to', 'reporting-endpoints', 'nel', 'expect-ct', 'origin-agent-cluster',
})

# 主机名段的形状。整站模式下主机名是从 URL 里来的，只有这里和白名单两道关：
# 放行 `a/b`、`user@host`、空值这类的话，后面拼出来的地址会指到别处去
_HOSTNAME = re.compile(r'^(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9][A-Za-z0-9.\-]*)(?::\d{1,5})?$')

# `__Host-` 开头的 Cookie 要求 `Path=/` 且带 `Secure`，而代理底下每张卡片的 Cookie
# 都钉在 `/api/proxy/<id>/` 上（不钉的话几个站的同名 Cookie 会互相顶掉），
# 条件对不上，浏览器会**直接丢掉**这枚 Cookie，表现成「登录页转一圈又回到登录页」。
# 所以下发时改个名字、回传时改回去。名字里 `-` 和 `_` 都是合法字符
_COOKIE_MANGLE = 'pxy'
_COOKIE_PREFIXES = ('__Host-', '__Secure-')

# 这条卡片路径底下的浏览器 Cookie 是哪个门户用户的（见模块头）。
# 要活得比上游那些 Cookie 久：它先过期的话，下次进来会被当成别人的，把自己的登录删掉。
# 所以给浏览器允许的上限（Chrome 是 400 天），而且上游每种一次 Cookie 就续一次
_OWNER_COOKIE = 'portal_proxy_owner'
_OWNER_TTL = 400 * 86400

# 注入过脚本的 HTML 是「按人」的：脚本里带着这个门户用户的本地存储前缀（app/proxyhook.py），
# 而同一张分享卡片大家的地址一模一样。浏览器缓存只认地址——上游没给 Cache-Control 时
# （Starlette 的 StaticFiles 就不给，只给 ETag / Last-Modified）浏览器按启发式规则自己缓存，
# 换个门户账号再打开，**请求都不发**，直接把上一个人那份端出来，进去就是上一个人的账号。
# 二手系统（FreeMarket）实际这么串过号。所以这类响应一律 no-store，下面这几个头摘掉：
# 带着 ETag / Last-Modified 的话，浏览器会拿旧的那份来问「变了没有」，上游一句 304 又把它端回来
_CACHE_HEADERS = frozenset({b'cache-control', b'expires', b'etag', b'last-modified', b'age', b'pragma'})

# 打开页面的那种请求（导航）上，浏览器拿着自己缓存的那份来问的条件头。
# 改成 no-store 之前缓存下来的旧页面（里面是别人的前缀，或者老版本根本没分开的脚本）
# 会这样来问，上游回 304 的话浏览器接着用旧的。摘掉之后上游只能回整份，由这边重新注入
_CONDITIONAL = ('if-none-match', 'if-modified-since')
_DOCUMENT_DESTS = ('document', 'iframe', 'frame')

# 请求发给上游时声明的压缩方式。整站模式要把正文读出来改写，所以只能要
# httpx 解得开的那几种——上游给了一个我们解不开的编码，改写那一步会当场抛异常。
try:
    from httpx._decoders import SUPPORTED_DECODERS as _DECODERS
    _ACCEPT_ENCODING = ', '.join(k for k in ('gzip', 'deflate', 'br', 'zstd') if k in _DECODERS)
except Exception:                                     # noqa: BLE001  httpx 内部结构变了
    _ACCEPT_ENCODING = 'gzip, deflate'

# 解 br 时每次最多要这么多输出，见 `_patch_brotli`
_BR_STEP = 64 * 1024


def _patch_brotli() -> None:
    """绕开 brotlicffi 1.2.0.0 / 1.2.0.1 边收边解 br 时的一个 bug。

    表现：整站代理 メルカリ 时 mercdn.net 上的 CSS/JS 一律 500，日志里是
    `httpx.DecodingError: brotli: decoder process called with data when
    'can_accept_more_data()' is False`。数据没坏，整份一次解开是好的。

    毛病在它的 `Decompressor._decompress`：一次调用里输出缓冲（按输入的 5 倍给）
    不够、要转第二圈时，第一圈没吃完的输入被存进 `_unconsumed_data`；第二圈把输入
    吃完了它却不清，下一块数据进来，开头那句检查看它非空就抛。所以只有**某一块的
    压缩比超过 5 倍**才炸（CSS 很容易），而 httpx 正是边收边解的。1.2.0.2 修掉了。

    不靠「升级一下」：它不是门户的直接依赖，httpx 见到环境里有就用，conda 的 base
    环境里它是顺带装的，打 exe 时环境里是哪一版就打进去哪一版。所以在这儿绕——
    换成 1.2 新加的 `output_buffer_limit`：每次最多要那么多，缓冲一满就返回，
    一次调用只转一圈，没吃完的输入下一次开头就被取走，留不成陈货。
    吐出来的顶到上限 = 后面还有，喂空串接着要，直到有一次不满上限为止。

    **别改成拿 `can_accept_more_data()` 判断吐没吐完**：Google 的 C 版 `brotli`
    （httpx 有它就优先用它）把输入整个收进内部缓冲，那个方法的意思只是
    「可以再喂输入了」，它说 True 的时候输出可能还剩一大截，照它停就丢数据。
    没有这套接口的老版本（也没这个 bug）照旧走 httpx 自己那条。

    改的是 httpx 进程级的那张解码表，icon.py 抓图标那条也一并用上。
    """
    try:
        from httpx import _decoders
        base = _decoders.SUPPORTED_DECODERS['br']
    except Exception:                                 # noqa: BLE001  没装 brotli，或者 httpx 内部结构变了
        return

    class Brotli(base):
        def decode(self, data: bytes) -> bytes:
            # 有 can_accept_more_data 就是 1.2 那套（同一版加的 output_buffer_limit），只拿它认版本
            if not data or not hasattr(self.decompressor, 'can_accept_more_data'):
                return super().decode(data)
            self.seen_data = True
            try:
                piece = self._decompress(data, output_buffer_limit=_BR_STEP)
                out = [piece]
                while len(piece) >= _BR_STEP:         # 顶到上限了，后面还有
                    piece = self._decompress(b'', output_buffer_limit=_BR_STEP)
                    out.append(piece)
            except _decoders.brotli.error as exc:
                raise httpx.DecodingError(str(exc)) from exc
            return b''.join(out)

    _decoders.SUPPORTED_DECODERS['br'] = Brotli


_patch_brotli()


# ---------------------------------------------------------------- 目标地址

class Target(NamedTuple):
    """一张开了代理的卡片。"""
    item_id: str
    url: str                        # 卡片上填的完整地址
    scheme: str                     # http / https
    host: str                       # 主机名，带端口
    origin: str                     # scheme://host
    site: bool                      # 整站模式
    allow: tuple[str, ...]          # 整站模式下允许转发的域名后缀
    module: Any                     # app/proxy_webside/ 里认领它的模块，没有就是 None
    jar: bool                       # Cookie 代理：上游的登录态存服务器（app/cookiejar.py）
    # 这张卡片上用谁的登录态（门户用户 id）：自己的卡片和普通分享是本人，
    # 共享登录的分享是分享人。由 `_targets` 填，0 = 没填，按当前用户算（见 `_login_of`）
    login: int = 0
    # 能不能转到内网 / 本机地址。管理员自己的卡片、管理员分享出来的卡片才行（`_targets` 填）；
    # 普通用户自己加的卡片只许公网——不然任何一个账号都能拿门户当跳板进内网（见 `_check_address`）
    private_ok: bool = False

    @property
    def mount(self) -> str:
        return f'{PREFIX}/{self.item_id}/'

    @property
    def base(self) -> str:
        return f'{PREFIX}/{self.item_id}/{MARK}/'


# 每个用户一张目标表。多用户之后卡片 id 不再是全局唯一的，两个人各自的卡片
# 完全可能撞上同一个 id（前端那个 uid() 是本机随机生成的）——按 id 直接查全局表
# 的话，撞上就是「点我的卡片打开了别人那台机器」。
_CACHE_TTL = 5.0
_cache: dict[int, tuple[float, tuple[int, int], dict[str, Target]]] = {}


def _targets(user_id: int, admin: bool = False) -> dict[str, Target]:
    """(这个人的) 卡片 id → 目标，只收「门户代理」打开了的那些。

    `admin` 决定他**自己**的卡片能不能转内网（`Target.private_ok`）；分享过来的卡片
    一定是管理员的（navstore.shared 只认启用中的管理员），所以总是可以。

    打开一个被代理的页面能连着打出几十个子资源请求，每个都重查一遍库太费，
    所以压一层缓存。失效有两道：

      - `navstore.revision()` 是进程内的改动计数，**同一个进程里存完导航，
        下一个请求就看得到新的**；
      - 5 秒 TTL 兜住「别的进程改了库」这种情况（多实例部署）。

    两道都要：只靠 TTL 的话，自己刚改完卡片地址、点进去还是老的，查起来很懵。
    """
    now = time.monotonic()
    # 分享过来的卡片在别人名下，它们的改动看全局那个计数
    # 角色也进失效条件：降级之后，他自己那些卡片当场不能再转内网
    rev = (navstore.revision(user_id), navstore.shared_revision(), admin)
    hit = _cache.get(user_id)
    if hit and hit[0] > now and hit[1] == rev:
        return hit[2]

    table: dict[str, Target] = {}
    for item in navstore.cards(user_id):
        target = _to_target(item)
        if target:
            table[target.item_id] = target._replace(login=user_id, private_ok=admin)
    # 管理员分享给这个人的卡片（navstore.shared）。归属校验的意思变成
    # 「是你的卡片，或者分享给了你」，仍然只查这个人自己的那份名单。
    # 卡片的配置照管理员那张走，但罐子用的是 user_id 这个人自己的（_cookie_out / _save_cookies）：
    # 分享的是配置，不是登录
    # 自己的卡片先进表：id 撞上了（前端随机生成的，极少见）以自己那张为准
    for item in navstore.shared(user_id):
        target = _to_target(item)
        if target:
            login = int(item['ownerId']) if item.get('shareLogin') else user_id
            table.setdefault(target.item_id, target._replace(login=login, private_ok=True))

    _cache[user_id] = (now + _CACHE_TTL, rev, table)
    return table


def _to_target(item: object) -> Optional[Target]:
    if not isinstance(item, dict) or not item.get('proxy'):
        return None
    item_id, url = str(item.get('id') or ''), str(item.get('url') or '')
    parts = urlsplit(url)
    # 只转 http/https。卡片上填 ssh:// rdp:// 这类地址也是有的，
    # 那些不是 HTTP，转不了，当没开代理处理
    if not item_id or parts.scheme not in ('http', 'https') or not parts.netloc:
        return None

    module = webside.find(parts.hostname or '')
    # 卡片选了「整站」，或者这个主机被某个站点模块认领了。后一条是有意的：
    # proxy_webside 里躺着的就是「直接转发必坏」的那几个站，认出来了还按直接转发办，
    # 只会让人对着半张页面查半天
    site = str(item.get('proxy')).lower() == 'site' or module is not None
    allow = proxyrewrite.allow_for(parts.netloc, webside.hosts_of(module),
                                   item.get('proxyHosts')) if site else ()
    # cryptography 没装时整个功能自己关掉（见 app/secretbox.py）——存不了就别存，
    # 明文落库比不做这个功能更糟
    jar = bool(item.get('cookieJar')) and secretbox.available()
    return Target(item_id, url, parts.scheme, parts.netloc,
                  f'{parts.scheme}://{parts.netloc}', site, allow, module, jar)


def _login_of(target: Target, user_id: int) -> int:
    """这张卡片上用谁的登录态。见 `Target.login`。"""
    return target.login or user_id


def _is_admin(user: dict) -> bool:
    return (user.get('role') or 'user') == 'admin'


def _target(user: dict, item_id: str) -> Target:
    """**归属校验就是这一句**：查的是「这个人的」那张表，不是全局表。

    写成「查出来再比一比 user_id」的话，哪天多一条取卡片的路就漏一次，
    而漏一次的后果是：拿到别人的卡片 id 就能借门户往那台机器上打。
    """
    target = _targets(int(user['id']), _is_admin(user)).get(item_id)
    if not target:
        # 不区分「这张卡片不是你的」「存在但没开代理」「压根没这张卡片」：
        # 分开说等于让人拿一串 id 去探别人的导航里有什么
        raise HTTPException(404, '这张卡片没有开门户代理')
    return target


def _rewriter(target: Target, foreign: str = '', portal_origin: str = '',
              user_id: int = 0) -> Rewriter:
    """`foreign` 是白名单之外、从链接点过来的那台主机（见 `_upstream`）。

    这时候页面在它那边，它自己那一族域名上的资源也该跟着走代理，
    所以这一次的改写把它的注册域临时并进白名单——只影响这一份响应，卡片的白名单不变。

    `user_id` 拿来给注入脚本定本地存储的前缀（app/proxyhook.py），按「门户用户 + 卡片」分开。
    调用方给的是 `_login_of` 那个人：共享登录时是分享人，两个人看到的是同一份。
    """
    allow = target.allow
    if foreign:
        name = foreign.lower().split(':', 1)[0]
        allow = tuple(sorted({*allow, name, proxyrewrite.site_of(name)}))
    store = f'__portal:u{user_id}:{target.item_id}:' if user_id else ''
    return Rewriter(target.mount, allow, webside.browser_js(target.module),
                    webside.rewrite_js(target.module), portal_origin=portal_origin, store=store)


# ---------------------------------------------------------------- 白名单之外：只许公网

# 主机名 → (过期时间, 是不是公网)。一页几十个子资源都要问一遍，不缓存的话每个请求
# 都去解析一次 DNS。60 秒：DNS 记录变了，最多一分钟后跟上
_PUBLIC_TTL = 60.0
_public_cache: dict[str, tuple[float, bool]] = {}
_addr_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


def _ip(text: str):
    try:
        ip = ipaddress.ip_address(text.split('%', 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped                            # ::ffff:10.0.0.1 这种绕法
    return ip


def _ip_public(text: str) -> bool:
    ip = _ip(text)
    return ip is not None and ip.is_global and not ip.is_multicast


def _ip_forbidden(text: str) -> bool:
    """**谁的卡片都不许转**的地址：链路本地（169.254.169.254 云元数据就在这儿）、
    0.0.0.0 / ::、组播。管理员要转内网可以，但这几类不是「内网里的一台机器」。"""
    ip = _ip(text)
    return ip is None or ip.is_link_local or ip.is_unspecified or ip.is_multicast


def _bare_host(host: str) -> str:
    name = host.lower()
    if name.startswith('['):
        return name[1:].split(']', 1)[0]
    return name.rsplit(':', 1)[0] if name.count(':') == 1 else name


async def _addresses(host: str) -> Optional[tuple[str, ...]]:
    """主机解析出来的全部地址（本身是 IP 就是它自己）。解析不了返回 None。缓存 60 秒。"""
    name = _bare_host(host)
    if not name:
        return None
    if _ip(name) is not None:
        return (name,)
    now = time.monotonic()
    hit = _addr_cache.get(name)
    if hit and hit[0] > now:
        return hit[1]
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(name, None)
    except OSError:
        return None
    addrs = tuple(sorted({info[4][0] for info in infos}))
    if len(_addr_cache) > 4096:
        _addr_cache.clear()
    _addr_cache[name] = (now + _PUBLIC_TTL, addrs)
    return addrs


async def _check_address(target: Target, host: str) -> None:
    """不在「白名单之外」那条路上的请求也要过的一道：这张卡片能不能连这台主机。

    - 链路本地 / 未指定 / 组播：谁都不行（`_ip_forbidden`）；
    - 内网、本机：只有 `target.private_ok` 的卡片行（管理员的、管理员分享的）。
      普通用户的卡片只许公网，单标签名字（`nas`）一律当内网。

    以前这里什么都不查，任何一个账号给自己加一张 `http://169.254.169.254/` 的卡片、
    打开门户代理，门户就替他把云元数据端点读回来了。和 `_public` 一样挡不住 DNS rebinding。
    """
    if not target.private_ok and '.' not in _bare_host(host) and _ip(_bare_host(host)) is None:
        raise HTTPException(403, f'这张卡片不转 {host}：只有管理员的卡片能经门户访问内网地址')
    addrs = await _addresses(host)
    if addrs is None:
        if target.private_ok:
            return                     # 解析不了就交给 httpx 自己报 502，管理员的内网名字常见这样
        raise HTTPException(502, f'门户解析不了 {host}')
    if not addrs or any(_ip_forbidden(a) for a in addrs):
        raise HTTPException(403, f'门户代理不转 {host}（链路本地 / 保留地址）')
    if target.private_ok:
        return
    if not all(_ip_public(a) for a in addrs):
        raise HTTPException(403, f'这张卡片不转 {host}：只有管理员的卡片能经门户访问内网地址')


async def _public(host: str) -> Optional[bool]:
    """这台主机是不是**只**解析到公网地址。解析不了返回 None。

    白名单之外的主机（从链接点过去的外站）靠这一道兜：公网站点随便转，
    内网、本机、链路本地（169.254.169.254 那种云元数据地址）一律不转。
    **解析出来的地址要全是公网才算**——一个域名同时带着公网和内网两条记录，
    httpx 连的是哪条说不准。

    挡不住 DNS rebinding（查的时候给公网、连的时候给内网）：httpx 自己再解析一遍。
    这条缝和「被代理的页面能调 /api/nav 给自己加一张指向内网的卡片」是同一个量级，
    见 CLAUDE.md 门户代理那节末尾。
    """
    name = host.lower()
    if name.startswith('['):
        name = name[1:].split(']', 1)[0]
    else:
        name = name.rsplit(':', 1)[0] if name.count(':') == 1 else name
    if not name:
        return False
    try:
        ipaddress.ip_address(name.split('%', 1)[0])
        return _ip_public(name)                        # 本身就是 IP，不用解析
    except ValueError:
        pass
    if '.' not in name:
        return False                                   # nas、gitlab 这种单标签名字只会是内网

    now = time.monotonic()
    hit = _public_cache.get(name)
    if hit and hit[0] > now:
        return hit[1]
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(name, None)
    except OSError:
        return None
    ok = bool(infos) and all(_ip_public(info[4][0]) for info in infos)
    if len(_public_cache) > 4096:
        _public_cache.clear()
    _public_cache[name] = (now + _PUBLIC_TTL, ok)
    return ok


async def _check_foreign(host: str) -> None:
    ok = await _public(host)
    if ok is None:
        raise HTTPException(502, f'门户解析不了 {host}')
    if not ok:
        # 白名单之外只转公网。放宽了，被代理页面里的一段脚本就能借门户去连内网任意一台机器
        raise HTTPException(403, f'这张卡片不转 {host}（不在它的域名白名单里，也不是公网地址）')


def _entry_ok(headers, target: Target, host: str) -> bool:
    """这次请求能不能落到 `host` 上：卡片自己那台机器随便进，别的主机只能从**门户自己的页面里**进。

    为什么要这一道：被代理的页面和门户同源。不拦的话，任何人发一条
    `/api/proxy/<卡片>/__portal__/https/坏站/x.html` 给登着门户的人（管理员），点开就是
    坏站的 HTML 跑在门户的源下，带着他的会话去调 `/api/users`——`Fallback` 靠的 Referer
    一句 `referrerPolicy: 'no-referrer'` 就绕开了。所以白名单里的兄弟域名（`xxx.github.io`
    这种注册域放进来的，内容未必是卡片主人的）和白名单之外的外站，都只认：

      - `Sec-Fetch-Site: same-origin`（从被代理的页面里点出去 / 加载 / 跳转过去，
        经过的跳转全在门户源下）或 `none`（自己在地址栏敲的、书签）；
      - 老浏览器没有 Sec-Fetch-*：Referer 得是同一张卡片代理底下的页面。

    跨站链过来的（cross-site / same-site）、什么都没带的，一律不认。卡片自己那台机器不受影响：
    那是卡片主人自己选的站，从聊天软件里点一条深链接进去是正常用法。

    **这只是把门槛从「一条链接」抬到「得先让人在代理里点到坏站」**：页面里的第三方脚本、
    从正常页面点出去的外站，仍然和门户同源。根治要把代理放到另一个源上，见 CLAUDE.md。
    """
    if host.lower() == target.host.lower():
        return True
    site = (headers.get('sec-fetch-site') or '').lower()
    if site:
        return site in ('same-origin', 'none')
    referer = urlsplit(headers.get('referer') or '')
    here = (headers.get('host') or '').lower()
    return bool(referer.path.startswith(target.mount)
                and (not referer.netloc or referer.netloc.lower() == here))


# ---------------------------------------------------------------- 地址

def _raw_path(scope) -> str:
    """请求的**原始**路径，不做百分号解码。

    不能用 `request.url.path` 或者路由参数：那两个都解过码了，`%2F` 会变成真的 `/`，
    路径分段跟着变，日文商品名那种路径重新编码回去也未必和原样一致。
    上游对这个是敏感的——图片地址里带 `%2F` 的系统不少，解错一个字符就是 404。
    """
    raw = scope.get('raw_path')
    if raw:
        text = raw.decode('latin-1')
        return text.split('?', 1)[0]          # 个别服务器把查询串也塞在 raw_path 里
    return scope.get('path', '')


def _upstream(target: Target, scope) -> tuple[str, str, str, bool]:
    """从请求路径里解出「转给谁」：(协议, 主机, 剩下的路径, 是不是白名单之外)。

    白名单之外的（从链接点过去的外站）这里先放过，调用方必须接着
    `await _check_foreign(host)`——只许公网那一道要解析 DNS，得是异步的。
    """
    path = _raw_path(scope)
    mount = target.mount
    rest = path[len(mount):] if path.startswith(mount) else path.lstrip('/')

    if not rest.startswith(MARK + '/'):
        # 老形状：/api/proxy/<id>/<路径>，转给卡片自己那台机器
        return target.scheme, target.host, rest, False

    bits = rest[len(MARK) + 1:].split('/', 2)
    if len(bits) < 2 or bits[0] not in ('http', 'https'):
        raise HTTPException(404, '代理地址不完整')
    scheme, host = bits[0], unquote(bits[1])
    tail = bits[2] if len(bits) > 2 else ''
    if not _HOSTNAME.match(host):
        raise HTTPException(400, '代理地址里的主机名不对')
    if not target.site:
        raise HTTPException(403, f'这张卡片不转 {host}（没开整站模式）')
    # 白名单之外的交给 `_check_foreign` 查公网。这道别省：省了，任何登录者都能拿
    # 这个接口当跳板去连内网任意一台机器
    return scheme, host, tail, not proxyrewrite.host_ok(host, target.allow)


def _real_url(target: Target, value: str) -> str:
    """门户上的一条代理地址 → 它对应的上游真实地址。不是这张卡片的就返回空串。

    `Referer` 要用它：上游拿这个判来路（防盗链、跨站校验都看它），
    给一条门户的地址等于告诉它「这是从一个它不认识的站点点过来的」。
    """
    if not value:
        return ''
    parts = urlsplit(value)
    if not parts.path.startswith(target.mount):
        return ''
    rest = parts.path[len(target.mount):]
    if rest.startswith(MARK + '/'):
        bits = rest[len(MARK) + 1:].split('/', 2)
        if len(bits) < 2 or bits[0] not in ('http', 'https'):
            return ''
        scheme, host, tail = bits[0], bits[1], (bits[2] if len(bits) > 2 else '')
    else:
        scheme, host, tail = target.scheme, target.host, rest
    out = f'{scheme}://{host}/{tail}'
    if parts.query:
        out += '?' + parts.query
    return out


def _clean_query(query: str, rewriter: Rewriter, *, keep_public_base: bool = False) -> str:
    """查询串里凡是指回门户的地址，换成上游的真实地址。

    登录那一步的常见写法是 `login?.done=<当前页面地址>`，页面脚本拿
    `location.href` 拼出来的是门户的地址。上游一看不是自己家的地址就不认，
    登完弹回首页——表现成「登录成功了但没跳回原来那一页」。

    只动**确实变了**的那几个参数，别整串重新编码：有的上游拿原串算签名，
    把 `,` 重编成 `%2C` 就对不上了。
    """
    if 'proxy' not in unquote(query):
        return query
    out = []
    for piece in query.split('&'):
        key, sep, value = piece.partition('=')
        # OIDC 客户端用 base 拼浏览器回调，必须保留公网代理前缀。
        # 仅 SSO 登录入口保留这一项，普通站点的返回地址仍按原规则反解。
        if keep_public_base and unquote(key) == 'base':
            out.append(piece)
            continue
        decoded = unquote(value)
        real = rewriter.unproxy(decoded)
        out.append(key + sep + (quote(real, safe='') if real != decoded else value))
    return '&'.join(out)


# ---------------------------------------------------------------- 首部

def _cookies_up(cookie: str) -> str:
    """转给上游之前收拾一下 Cookie。

    **门户自己那枚会话 Cookie 必须摘掉。** 上游页面在浏览器眼里跟门户同源
    （都在门户这个地址上），所以浏览器会把 portal_session 一并发过来。
    原样转过去等于把「登录门户」那张票交给对面那台机器，它拿着就能冒充登录者。

    下发时改过名的 `__Host-` / `__Secure-` 在这儿改回去，上游认的是原来那个名字。
    """
    kept = []
    for part in re.split(r';\s*', cookie):
        if not part:
            continue
        name = part.split('=', 1)[0].strip()
        if name in (COOKIE_NAME, _OWNER_COOKIE):
            continue
        if name.startswith(_COOKIE_MANGLE):
            bare = name[len(_COOKIE_MANGLE):]
            if bare.startswith(_COOKIE_PREFIXES):
                part = bare + part[len(name):]
        kept.append(part)
    return '; '.join(kept)


def _cookie_owner(cookie: str, user_id: int) -> tuple[bool, list[str]]:
    """浏览器带来的这些 Cookie 是不是这个门户用户的。返回 (是不是, 浏览器里那些 Cookie 的名字)。

    没有标记也算「不是」：改成这样之前种下的那些分不清是谁的，宁可让本人重登一次。
    名字给 `_owner_cookies` 拿去删；门户自己那枚会话 Cookie 不在里面。
    """
    owner, names = None, []
    for part in re.split(r';\s*', cookie or ''):
        name, _, value = part.partition('=')
        name = name.strip()
        if not name or name == COOKIE_NAME:
            continue
        if name == _OWNER_COOKIE:
            owner = value.strip()
        else:
            names.append(name)
    return owner == str(user_id), names


def _owner_cookies(target: Target, user_id: int, owned: bool, names: list[str],
                   refresh: bool) -> list[tuple[bytes, bytes]]:
    """随这次响应发给浏览器的 Set-Cookie：不是这个人的就先删掉，再把标记种成这个人。

    要排在上游那几条 Set-Cookie **前面**：同名的话浏览器按顺序处理，上游刚种的才留得下。
    只删得掉钉在卡片路径上的那些（上游种的、注入脚本种的都在这儿）；别处路径上的
    删不着，但标记对不上时它们也不会被转给上游。
    """
    if owned and not refresh:
        return []
    secure = '; Secure' if COOKIE_SECURE else ''
    out = [] if owned else [f'{name}=; Path={target.mount}; Max-Age=0{secure}' for name in names]
    out.append(f'{_OWNER_COOKIE}={user_id}; Path={target.mount}; Max-Age={_OWNER_TTL}; '
               f'HttpOnly; SameSite=Lax{secure}')
    return [(b'set-cookie', value.encode('latin-1')) for value in out]


def _cookie_out(request_cookie: str, target: Target, user_id: int,
                scheme: str, host: str, path: str) -> str:
    """发给上游的 `Cookie` 头。没开 Cookie 代理时就是收拾过的浏览器那份。

    开了之后**以服务器那个罐子为准**，浏览器那份里只有罐子没有的名字才补进来。
    两份都留着的原因分两头：

      - 罐子优先，是因为浏览器那份可能是上次清数据前的陈货，或者干脆是别的设备
        留下的；而罐子里那份是上游最近一次实际发下来的。
      - 浏览器那份仍然补，是因为页面里的脚本会拿 `document.cookie` 自己种东西
        （语言、时区、A/B 分桶之类）。那些从来不经过 `Set-Cookie`，罐子里没有，
        丢掉的话页面会每次都重新问一遍。
    """
    browser = _cookies_up(request_cookie)
    if not target.jar:
        return browser

    jar = cookiejar.header_for(cookiejar.load(_login_of(target, user_id), target.item_id),
                               scheme, host, path)
    if not jar:
        return browser
    if not browser:
        return jar

    taken = {part.split('=', 1)[0].strip() for part in re.split(r';\s*', jar) if part}
    extra = [part for part in re.split(r';\s*', browser)
             if part and part.split('=', 1)[0].strip() not in taken]
    return '; '.join([jar, *extra]) if extra else jar


def _save_cookies(resp: httpx.Response, target: Target, user_id: int,
                  host: str, path: str) -> None:
    """把这次响应里的 `Set-Cookie` 收进罐子。存不下去也不能把请求带崩。

    走 `resp.headers.raw`：一次响应里 `Set-Cookie` 可能有好几条，字典形状只留得下
    最后一条——表现成「明明登录成功了，下次进来还是没登录」，因为真正管用的那枚
    正好不是最后一条。
    """
    if not target.jar:
        return
    raw = [value.decode('latin-1') for name, value in resp.headers.raw
           if name.lower() == b'set-cookie']
    if not raw:
        return
    try:
        cookiejar.store(_login_of(target, user_id), target.item_id, raw, host, path)
    except Exception as exc:                          # noqa: BLE001
        # 库写不进去（连接断了、磁盘满了）不该让人手里这个页面跟着 500。
        # 代价只是这次的登录态没记住，下次重登一遍
        print(f'[Cookie 代理] 卡片 {target.item_id} 存登录数据失败: {exc}')


def _fetch_site(referer: str, scheme: str, host: str) -> str:
    """重算 `Sec-Fetch-Site`。

    浏览器填的是「浏览器 ↔ 门户」那一跳的关系，代理底下什么都是 same-origin，
    照直转过去，上游会看到一个页面在跨站取资源却自称同源的怪样子，
    查跨站的那类接口会拒掉。按上游那边的真实关系重算一遍。
    """
    if not referer:
        return 'none'
    from_host = (urlsplit(referer).hostname or '').lower()
    to_host = host.split(':', 1)[0].lower()
    if not from_host:
        return 'none'
    if from_host == to_host:
        return 'same-origin'
    if proxyrewrite.site_of(from_host) == proxyrewrite.site_of(to_host):
        return 'same-site'
    return 'cross-site'


def _request_headers(request: Request, target: Target, scheme: str, host: str,
                     rewriter: Optional[Rewriter], user_id: int = 0,
                     path: str = '/', owned: bool = True) -> list[tuple[str, str]]:
    """`owned` 为 False = 浏览器带来的 Cookie 不是这个门户用户的（`_cookie_owner`），一条都不转。"""
    referer = _real_url(target, request.headers.get('referer', ''))
    out: list[tuple[str, str]] = []
    seen_cookie = False
    dest = request.headers.get('sec-fetch-dest', '')
    navigation = dest in _DOCUMENT_DESTS or (not dest and 'text/html' in request.headers.get('accept', ''))
    for name, value in request.headers.items():
        low = name.lower()
        if _dropped(low):
            continue
        if target.site and navigation and low in _CONDITIONAL:
            continue                           # 见 _CONDITIONAL
        if low == 'cookie':
            seen_cookie = True
            # 不是这个人的那份不转，罐子里他自己那份照常补上来
            value = _cookie_out(value if owned else '', target, user_id, scheme, host, path)
            if not value:
                continue
        elif low == 'referer':
            if not referer:
                continue                       # 换不回真实地址就不发，别把门户的地址漏出去
            value = referer
        elif low == 'origin':
            # 浏览器填的是门户的 origin，在上游看来是跨站，查 CSRF 的接口一律拒。
            # 换成它自己那边的 origin——本来就是用户自己让门户去连它的
            value = _origin_of(referer) or f'{scheme}://{host}'
        elif low == 'accept-encoding' and target.site:
            value = _ACCEPT_ENCODING
        elif low == 'sec-fetch-site' and target.site:
            value = _fetch_site(referer, scheme, host)
        out.append((name, value))

    if target.jar and not seen_cookie:
        # 浏览器一条 Cookie 都没带（第一次进、或者刚清过数据）时上面那个分支根本不会走到，
        # 而这恰恰是 Cookie 代理最该出场的时候——罐子里那份得自己补上来
        jar = _cookie_out('', target, user_id, scheme, host, path)
        if jar:
            out.append(('cookie', jar))

    # content-length 特意留着（上面没滤）：httpx 见到请求头里已经有它，就不会再给
    # 这个流式请求体加一个 Transfer-Encoding: chunked——有些老后台认不得 chunked 上传

    if not target.site:
        # 直接转发的多半是内网后台，它们靠这几个头记访问来源、做 IP 白名单
        out.append(('x-forwarded-for', client_ip(request)))
        out.append(('x-forwarded-proto', request.url.scheme))
        forwarded_host = request.headers.get('host')
        if forwarded_host:
            out.append(('x-forwarded-host', forwarded_host))
    # 整站模式**故意不发** X-Forwarded-For：那是用户自己的 IP。
    # 公网站点认这个头的不少，一认这个功能就白做了——门户存在的意义正是
    # 让请求从门户这台机器出去。上游只该看见门户的出口 IP。
    return out


def _accepts(request: Request, encoding: str) -> bool:
    """调用方认不认这种压缩方式。

    整站模式下发给上游的 `Accept-Encoding` 是门户自己写的（`_ACCEPT_ENCODING`）——
    正文要解得开才改得动。所以上游回来的编码**调用方未必认**：浏览器无所谓
    （gzip / br 都认），但拿 `wget` 或者下载工具去拉一个 Release 附件的会说
    `identity`，照直把 gzip 转回去，落到磁盘上的就是一坨解不开的字节，
    而且文件名、长度看着都对。
    """
    header = request.headers.get('accept-encoding')
    if header is None:
        return False                      # 一个字没说 = 只认原文（RFC 9110 8.4.1）
    star = False
    for piece in header.split(','):
        name, _, params = piece.strip().partition(';')
        q = 1.0
        for param in params.split(';'):
            key, _, value = param.partition('=')
            if key.strip().lower() == 'q':
                try:
                    q = float(value.strip())
                except ValueError:
                    q = 0.0
        name = name.strip().lower()
        if name == encoding:
            return q > 0
        if name == '*':
            star = q > 0
    return star


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}' if parts.scheme and parts.netloc else ''


def _rewrite_cookie(value: str, mount: str) -> str:
    """上游种的 Cookie：Path 改写到 /api/proxy/<id>/，Domain 去掉。

    不改 Path 的话 `Path=/` 会让这枚 Cookie 跟着门户的每个请求跑：几个站的 Cookie
    互相串味，撞上同名的（两个系统都叫 session 太常见了）还会互相顶掉，
    表现成「开了 B 系统之后 A 系统就掉登录」。

    Domain 去掉之后，这张卡片底下所有主机共用一份 Cookie。对整族站点来说这正是要的：
    `login.yahoo.co.jp` 种的 `domain=.yahoo.co.jp`，回头要跟着
    `auctions.yahoo.co.jp` 的请求发出去，登录才算数。

    http 门户下还要把 Secure 去掉，否则浏览器直接不存这枚 Cookie，
    表现成「上游怎么登都登不上」。SameSite=None 少了 Secure 同样会被丢掉，
    一起降成 Lax——代理底下全是同源请求，Lax 一样会发出去。
    """
    parts = re.split(r';\s*', value)
    head = parts[0]
    name, sep, rest = head.partition('=')
    name = name.strip()
    if name.startswith(_COOKIE_PREFIXES) and (name.startswith('__Host-') or not COOKIE_SECURE):
        # 名字带前缀的那两种，条件在代理底下满足不了，改个名字发给浏览器，
        # 回传时由 `_cookies_up` 改回去
        head = _COOKIE_MANGLE + name + sep + rest

    out = [head]
    for attr in parts[1:]:
        key, _, val = attr.partition('=')
        low = key.strip().lower()
        if low in ('path', 'domain'):
            continue
        if low == 'secure' and not COOKIE_SECURE:
            continue
        if low == 'samesite' and val.strip().lower() == 'none' and not COOKIE_SECURE:
            out.append('SameSite=Lax')
            continue
        out.append(attr)
    out.append(f'Path={mount}')
    return '; '.join(out)


def _rewrite_location(value: str, target: Target, scheme: str, host: str,
                      rewriter: Optional[Rewriter]) -> str:
    """跳转地址：能转的折回代理底下，转不了的原样留着。

    整站模式下跳到别的站也折回来（和点链接出去是一回事）：跳过去那一下
    由浏览器重新发请求，到时候 `_check_foreign` 会查它是不是公网，
    内网地址照样转不过去，所以这里不会变成开放中继。
    直接转发模式下只认卡片自己那台机器，别处的原样留着。
    """
    if rewriter is not None:
        return rewriter.url(value, scheme, host, nav=True)
    mount = target.mount
    if value.startswith('//'):
        return value                                   # //host/x，换机器了
    if value.startswith('/'):
        return mount + value[1:]
    here = urlsplit(value)
    if not here.scheme or not here.netloc:
        return value                                   # 相对地址，本来就在代理底下
    if f'{here.scheme}://{here.netloc}' != target.origin:
        return value
    tail = here.path.lstrip('/')
    if here.query:
        tail += '?' + here.query
    if here.fragment:
        tail += '#' + here.fragment
    return mount + tail


def _response_headers(resp: httpx.Response, target: Target, scheme: str, host: str,
                      rewriter: Optional[Rewriter], decoded: bool,
                      cookies: list[tuple[bytes, bytes]] = (),
                      drop_cookies: bool = False) -> list[tuple[bytes, bytes]]:
    """原样带回，除了逐跳首部、那几个讲源站规矩的（`_STRIP`）、Set-Cookie 和 Location。

    走 `resp.headers.raw` 而不是 `.items()`：Set-Cookie 一次可能有好几条，
    字典形状的首部只留得下最后一条，表现成「上游明明种了三枚 Cookie 只到了一枚」。

    `cookies` 是 `_owner_cookies` 给的那几条，排在最前面，理由见那边。
    """
    out: list[tuple[bytes, bytes]] = list(cookies)
    for raw_name, raw_value in resp.headers.raw:
        name = raw_name.decode('latin-1')
        low = name.lower()
        if low in _HOP or low in _STRIP:
            continue
        if decoded and low in ('content-length', 'content-encoding'):
            # 正文已经解压、改过长度了，这两个头再带回去就是错的
            continue
        value = raw_value.decode('latin-1')
        if low == 'set-cookie':
            if drop_cookies:
                continue                       # 外站种的不收，见 forward 里 foreign 那段
            value = _rewrite_cookie(value, target.mount)
        elif low == 'location':
            original = value
            value = _rewrite_location(value, target, scheme, host, rewriter)
            diagnostics.event('redirect.rewrite', before=diagnostics.safe_url(original),
                              after=diagnostics.safe_url(value), changed=value != original)
        elif low == 'link' and rewriter is not None:
            value = rewriter.link_header(value, scheme, host)
        out.append((raw_name, value.encode('latin-1')))
    return out


def _no_store(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """注入过脚本的 HTML 不许浏览器缓存，见 `_CACHE_HEADERS`。"""
    out = [(name, value) for name, value in headers if name.lower() not in _CACHE_HEADERS]
    out.append((b'cache-control', b'no-store'))
    return out


# ---------------------------------------------------------------- HTTP

_http: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    """转发用的连接池，整个进程一个，进程退出时由 `shutdown` 关掉。

    - `follow_redirects=False`：跳转要交回浏览器，由它带着 Cookie 再来一趟，
      顺带让地址栏跟着走。门户替它跳的话，浏览器那边地址一直停在第一个页面上。
    - `read=None`：不设读超时。实时日志、SSE、长轮询都是一个请求挂很久，
      设了超时就会隔一会儿断一次。连接超时还是留着，免得目标关机时干等。
    - `verify=False`：内网系统的 https 基本都是自签证书（PVE、群晖、各种带外管理口），
      按标准校验必然失败，而失败的表现是「这张卡片点了就报 502」，谁都想不到是证书。
    - `http2`：装了 h2 就开。公网站点基本都在 h2 上，用 h1.1 去问偶尔会拿到
      不一样的东西（有的站按协议版本分流）。h2 不在 requirements 里，所以是探到才开。
    """
    global _http
    if _http is None:
        try:
            import h2                                  # noqa: F401
            http2 = True
        except ImportError:
            http2 = False
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, read=None),
            follow_redirects=False,
            verify=False,
            http2=http2,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )
    return _http


async def shutdown() -> None:
    """进程退出时收掉连接池（app/main.py 的 lifespan 里调）。"""
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


@router.api_route('/{item_id}', methods=['GET', 'HEAD'])
def enter(item_id: str, user: dict = Depends(require_login)):
    """卡片的链接落在这里，再跳到带路径的那条。

    前端只拼 /api/proxy/<id>，路径和查询串在这儿从卡片地址里补上——两头都去解析
    一遍 URL 没必要，而且卡片地址改了之后前端那份不会还记着老路径。
    307 不是 302：万一是带着方法过来的，方法别被改成 GET。
    """
    target = _target(user, item_id)
    parts = urlsplit(target.url)
    tail = parts.path.lstrip('/')
    if parts.query:
        tail += '?' + parts.query
    head = f'{target.base}{target.scheme}/{target.host}/' if target.site else target.mount
    return RedirectResponse(head + tail, status_code=307)


@router.api_route('/{item_id}/{path:path}', methods=_METHODS)
async def forward(item_id: str, path: str, request: Request,
                  user: dict = Depends(require_login)):
    diagnostics.event('proxy.resolve', card=item_id)
    user_id = int(user['id'])
    target = _target(user, item_id)
    scheme, host, rest, foreign = _upstream(target, request.scope)
    if not _entry_ok(request.headers, target, host):
        raise HTTPException(403, f'{host} 只能从这张卡片打开的页面里进去，不能从别处直接链过来')
    if foreign:
        await _check_foreign(host)
    else:
        await _check_address(target, host)
    login = _login_of(target, user_id)
    rewriter = (_rewriter(target, host if foreign else '', _origin_of(str(request.url)), login)
                if target.site else None)
    if foreign:
        # 白名单之外的外站：这张卡片底下浏览器里那些 Cookie（上游站点的登录态）一条都不给它，
        # 罐子也不用，它种的也不收。以前是整份转过去的——在メルカリ 里点一个外链，
        # メルカリ 的会话 Cookie 就到了那个外站的服务器上
        target = target._replace(jar=False)
        owned, browser_cookies = False, []
    else:
        owned, browser_cookies = _cookie_owner('; '.join(request.headers.getlist('cookie')), login)

    url = f'{scheme}://{host}/{rest}'
    up_path = '/' + rest.split('?', 1)[0]
    query = request.url.query
    if query:
        sso_login = up_path in ('/mercariV2/src/use_web/sso/login', '/api/sso/login')
        url += '?' + (_clean_query(query, rewriter, keep_public_base=sso_login) if rewriter else query)
    ctx = webside.Ctx(item_id, target.mount, target.base, scheme, host,
                      up_path, request.method)
    headers = _request_headers(request, target, scheme, host, rewriter,
                               user_id, up_path, owned)
    webside.on_request(target.module, ctx, headers)

    # 有没有请求体，看调用方自己说了什么，不按方法猜：GET 带体、POST 不带体都是有的。
    # 没有体时别传 content，否则 httpx 会给一个空 GET 也挂上 chunked
    has_body = 'content-length' in request.headers or 'transfer-encoding' in request.headers

    client = _client()
    upstream = client.build_request(
        request.method, url,
        headers=headers,
        content=request.stream() if has_body else None,
    )
    try:
        diagnostics.event('upstream.begin', url=diagnostics.safe_url(url), site=target.site, foreign=foreign)
        resp = await client.send(upstream, stream=True)
        diagnostics.event('upstream.response', status=resp.status_code,
                          location=diagnostics.safe_url(resp.headers.get('location', '')),
                          content_type=resp.headers.get('content-type', '')[:100])
    except httpx.HTTPError as exc:
        diagnostics.event('upstream.error', error_type=type(exc).__name__)
        raise HTTPException(502, f'门户连不上 {scheme}://{host}：{exc}') from None

    # 存罐子挂在这一句：底下有「改写后整份发」和「流式转」两条出路，
    # 挂在这儿两条都覆盖得到，挂到 _response_headers 里则会被调用两次
    _save_cookies(resp, target, user_id, host, up_path)
    cookies = ([] if foreign else
               _owner_cookies(target, login, owned, browser_cookies, 'set-cookie' in resp.headers))

    if rewriter is not None and request.method != 'HEAD' and resp.status_code not in (204, 304):
        try:
            rewritten = await _rewritten(resp, target, ctx, rewriter, scheme, host, cookies,
                                         drop_cookies=foreign)
        except httpx.HTTPError as exc:
            # 正文读到一半断了、或者压缩数据解不开。响应头还没发，回一个干净的 502，
            # 别让它变成一屏 500 的 traceback
            await resp.aclose()
            diagnostics.event('upstream.error', error_type=type(exc).__name__)
            raise HTTPException(502, f'{scheme}://{host} 回来的正文读不下来：{exc}') from None
        if rewritten is not None:
            return rewritten

    # 流式转：几百兆的下载不该在门户内存里攒一份。
    # aiter_raw 给的是没解压过的原字节，所以 content-encoding 原样带回去也对得上。
    # 例外是上游用了一种**调用方没说自己认**的压缩（见 `_accepts`），那就走
    # aiter_bytes 当场解开——httpx 边收边解，一样是流式的，内存不会涨。
    encoding = resp.headers.get('content-encoding', '').strip().lower()
    decode = (bool(encoding) and encoding != 'identity' and not _accepts(request, encoding)
              and request.method != 'HEAD' and resp.status_code not in (204, 304))
    # 响应读完（或者浏览器中途跑了）之后由 BackgroundTask 把上游连接还回池子
    response = StreamingResponse(resp.aiter_bytes() if decode else resp.aiter_raw(),
                                 status_code=resp.status_code,
                                 background=BackgroundTask(resp.aclose))
    response.raw_headers = _response_headers(resp, target, scheme, host, rewriter, decode, cookies,
                                             drop_cookies=foreign)
    return response


async def _read_capped(resp: httpx.Response):
    """读到上限为止。没超过就返回 (整份正文, None)，超了返回 (已读那截, 剩下的迭代器)。

    不能直接 `aread()`：`Content-Length` 是可以没有的（chunked），一份边生成边发的
    大 JSON 会把门户的内存吃光。读到上限就认输，退回流式——正文已经读了一截，
    所以要把那一截和同一个迭代器剩下的部分接起来，不能重新来一遍。
    """
    stream = resp.aiter_bytes()
    chunks: list[bytes] = []
    total = 0
    async for chunk in stream:
        chunks.append(chunk)
        total += len(chunk)
        if total > proxyrewrite.MAX_REWRITE:
            return b''.join(chunks), stream
    return b''.join(chunks), None


async def _rewritten(resp: httpx.Response, target: Target, ctx: webside.Ctx,
                     rewriter: Rewriter, scheme: str, host: str,
                     cookies: list[tuple[bytes, bytes]] = (),
                     drop_cookies: bool = False) -> Optional[Response]:
    """要改写就把整份读进来改完再发；不用改的返回 None，由调用方照常流式转。

    改写必然要整份读进内存（HTML 里那些地址不按块对齐），所以只对 HTML/CSS/JS/JSON
    这几类动手，而且卡着一个大小上限——真有一份几百兆的 JS，那份不改也罢。
    """
    kind = proxyrewrite.kind_of(resp.headers.get('content-type', ''))
    if kind is None:
        return None
    declared = resp.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > proxyrewrite.MAX_REWRITE:
        return None
    charset = proxyrewrite.charset_of(resp.headers.get('content-type', ''))

    body, rest = await _read_capped(resp)
    if rest is not None:
        # 比上限还大，而且上游没提前说长度（chunked）。不改了，把已经读出来的那截
        # 接上剩下的继续流式发回去——**不能**整份读进内存，门户这点内存扛不住
        async def chained():
            yield body
            async for chunk in rest:
                yield chunk
        big = StreamingResponse(chained(), status_code=resp.status_code,
                                background=BackgroundTask(resp.aclose))
        big.raw_headers = _response_headers(resp, target, scheme, host, rewriter, True, cookies,
                                            drop_cookies=drop_cookies)
        return big

    await resp.aclose()
    # 这里往下不能再退回流式了：正文已经读完，而且是解压过的。
    # 所以即使不改写，也得按「解压过的」发回去（content-encoding / length 都要重算）
    if charset is not None:
        try:
            text = rewriter.body(kind, body.decode(charset, errors='replace'), scheme, host)
            if kind == 'html':
                text = webside.on_html(target.module, ctx, text)
            body = text.encode(charset, errors='replace')
        except (UnicodeError, LookupError) as exc:
            print(f'[代理] {host} 的这份 {kind} 改写不了，按原样发回: {exc}')

    headers = _response_headers(resp, target, scheme, host, rewriter, True, cookies,
                                drop_cookies=drop_cookies)
    if kind == 'html':
        headers = _no_store(headers)           # 里面注入了按人的脚本，见 _CACHE_HEADERS
    headers.append((b'content-length', str(len(body)).encode('latin-1')))
    response = Response(content=body, status_code=resp.status_code)
    response.raw_headers = headers
    return response


# ---------------------------------------------------------------- WebSocket

def _ws_module():
    """websockets 的客户端模块，没有就返回 None。

    import 写在函数里（和 tray.py 一个路数）：websockets 是 uvicorn[standard] 顺带
    装上的，不是 requirements 里写明的直接依赖。哪天它不在了，代价应该是
    「这个页面的实时那块不转了」，不是整个门户起不来。
    """
    try:
        import websockets
    except ImportError:
        return None
    return websockets


async def _connect_upstream(ws_mod, url: str, headers: list[tuple[str, str]],
                            subprotocols: Optional[list[str]]):
    """websockets 13 之前叫 extra_headers，14 之后叫 additional_headers，两个名字都认。"""
    try:
        params = inspect.signature(ws_mod.connect).parameters
        name = 'additional_headers' if 'additional_headers' in params else 'extra_headers'
    except (TypeError, ValueError):
        name = 'additional_headers'
    kwargs = {name: headers, 'open_timeout': 10, 'max_size': None}
    if subprotocols:
        kwargs['subprotocols'] = subprotocols
    return await ws_mod.connect(url, **kwargs)


@router.websocket('/{item_id}/{path:path}')
async def forward_ws(ws: WebSocket, item_id: str, path: str):
    """WebSocket 也要转：带 web 终端、实时日志、聊天的页面不在少数，
    只转 HTTP 的话那些地方会一直卡在「连接中」。

    这里**不能**挂 require_login：它抛的是 HTTPException，而握手这会儿还没有
    HTTP 那套异常处理接着，结果是一个 500 而不是干净的拒绝。自己查一遍 Cookie，
    不对就按 1008（policy violation）关掉。
    """
    user = user_from_token(ws.cookies.get(COOKIE_NAME, ''))
    if user is None:
        await ws.close(code=1008)
        return

    ws_mod = _ws_module()
    # 和 HTTP 那条一样，查的是这个人自己的目标表
    target = _targets(int(user['id']), _is_admin(user)).get(item_id)
    if ws_mod is None or target is None:
        await ws.close(code=1011)
        return
    # 跨站发起的握手不带 Lax 的会话 Cookie，上面那句就拦住了，这里不再比 Origin 和 Host：
    # 反代没把 Host 原样转过来的部署上，两者本来就对不上
    try:
        scheme, host, rest, foreign = _upstream(target, ws.scope)
        if foreign:
            await _check_foreign(host)
        else:
            await _check_address(target, host)
    except HTTPException:
        await ws.close(code=1008)      # 主机名不在白名单里，也不是公网
        return
    if foreign:
        target = target._replace(jar=False)    # 外站不给 Cookie，见 forward

    url = f'{"wss" if scheme == "https" else "ws"}://{host}/{rest}'
    if ws.url.query:
        url += '?' + ws.url.query

    ws_path = '/' + rest.split('?', 1)[0]
    # 握手这一下没法顺手删浏览器里的 Cookie，只做到「不是这个人的不转」；
    # 删和换标记由同一页面的 HTTP 请求去做
    owned, _ = _cookie_owner('; '.join(ws.headers.getlist('cookie')),
                             _login_of(target, int(user['id'])))
    owned = owned and not foreign
    headers = []
    seen_cookie = False
    for name, value in ws.headers.items():
        low = name.lower()
        # sec-websocket-* 和 upgrade 那几个由 websockets 自己重新握手时生成，
        # 原样带过去会和它生成的撞成两份
        if _dropped(low) or low.startswith('sec-websocket-'):
            continue
        if low == 'cookie':
            seen_cookie = True
            # WebSocket 这条也要带罐子里那份：带实时推送的站点多半在握手时就查登录，
            # 只给 HTTP 那条接上的话，页面本身打得开，实时那块一直「连接中」
            value = _cookie_out(value if owned else '', target, int(user['id']), scheme, host, ws_path)
            if not value:
                continue
        elif low == 'origin':
            # 上游多半要查 Origin。浏览器填的是门户的地址，在上游看来是跨站，
            # 一查就拒。换成上游自己的地址——本来就是用户自己让门户去连它的
            value = f'{scheme}://{host}'
        elif low == 'referer':
            real = _real_url(target, value)
            if not real:
                continue
            value = real
        headers.append((name, value))
    if target.jar and not seen_cookie:
        jar = _cookie_out('', target, int(user['id']), scheme, host, ws_path)
        if jar:
            headers.append(('cookie', jar))
    if not target.site:
        headers.append(('x-forwarded-for', client_ip(ws)))

    subprotocols = [p.strip() for p in
                    ws.headers.get('sec-websocket-protocol', '').split(',') if p.strip()]

    try:
        upstream = await _connect_upstream(ws_mod, url, headers, subprotocols)
    except Exception:                  # noqa: BLE001  连不上就连不上，页面自己会重试
        await ws.close(code=1011)
        return

    await ws.accept(subprotocol=getattr(upstream, 'subprotocol', None))

    async def to_upstream() -> None:
        while True:
            msg = await ws.receive()
            if msg['type'] == 'websocket.disconnect':
                return
            # 用 receive() 而不是 receive_text()：分得清文字帧和二进制帧，
            # 转错类型的话对面按字符串解二进制，直接报协议错
            data = msg.get('text')
            if data is None:
                data = msg.get('bytes')
            if data is not None:
                await upstream.send(data)

    async def to_browser() -> None:
        async for data in upstream:
            if isinstance(data, (bytes, bytearray)):
                await ws.send_bytes(bytes(data))
            else:
                await ws.send_text(data)

    up = asyncio.create_task(to_upstream())
    down = asyncio.create_task(to_browser())
    try:
        # 哪边先断，另一边就没有存在的意义了
        _, pending = await asyncio.wait([up, down], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    finally:
        with contextlib.suppress(Exception):
            await upstream.close()
        with contextlib.suppress(Exception):
            await ws.close()


# ---------------------------------------------------------------- 根绝对地址兜底

_FROM_PROXY = re.compile(
    rf'^{re.escape(PREFIX)}/([^/?#]+)/({re.escape(MARK)}/https?/[^/?#]+/)?')


def _fallback_target(scope) -> Optional[str]:
    """这个请求是不是从某张卡片的代理页面里发出来的；是的话返回该转去哪。"""
    path = scope.get('path', '')
    if path.startswith(PREFIX + '/'):
        return None                       # 已经在代理底下了
    if path.startswith('/api/oidc/'):
        # 门户当 IdP 的那几个端点（app/idp.py）。经代理打开的系统点「门户单点登录」时，
        # 浏览器跳过来那一下带的 Referer 正是代理页面——不放过的话授权请求会被
        # 转回那个系统自己身上，单点登录永远走不通
        return None

    referer = host = ''
    for raw_name, raw_value in scope.get('headers') or []:
        name = raw_name.decode('latin-1').lower()
        if name == 'referer':
            referer = raw_value.decode('latin-1')
        elif name == 'host':
            host = raw_value.decode('latin-1')

    if not referer:
        return None
    parts = urlsplit(referer)
    if parts.netloc and host and parts.netloc != host:
        return None                       # 别人站点链过来的，不是代理页面里出来的
    match = _FROM_PROXY.match(parts.path)
    if not match:
        return None

    # 带主机名那一段的话一并带上：页面在 s.yimg.jp 上，它里面的 /images/x.png
    # 就该回到 s.yimg.jp，而不是回到卡片地址那台机器
    target = f'{PREFIX}/{match.group(1)}/{match.group(2) or ""}'.rstrip('/') + path
    query = scope.get('query_string') or b''
    if query:
        target += '?' + query.decode('latin-1')
    return target


class Fallback:
    """被代理页面里的根绝对地址（/static/x.js）由这里转回代理底下。

    页面在浏览器看来的地址是 /api/proxy/<id>/…，它里面写着 /static/x.js 时，
    浏览器会去请求门户的 /static/x.js——那儿只有门户自己的前端，给不了它想要的东西。
    Referer 里带着「这次请求是从哪个代理页面发出来的」，照着它 307 转回去。

    整站模式下正文里的根绝对地址大半已经被改写过了（app/proxyrewrite.py），
    这条仍然留着兜运行时才冒出来的那些：改写不碰 JS 里 `"/"` 开头的字符串，
    注入的脚本也有够不着的角落（Worker 里、被别的脚本存下来的原始 fetch）。

    - **为什么是 307 不是 308**：同一个 /static/x.js，A 系统和 B 系统都可能有。
      308 会被浏览器永久记住，下次打开 B 系统时它会直接去 A 系统那儿拿。
    - **为什么 /api/ 开头的也兜**（只放过 /api/proxy/ 自己）：上游系统自己也会有
      /api/xxx。Referer 既然说了这次请求出自代理页面，那它就该归上游，
      不该被门户自己那几个接口截胡。顺带挡了一道：被代理的页面里的脚本
      按同源去摸 /api/nav 时，摸到的是它自己那台上游。
    - **写成 ASGI 中间件，不用 `@app.middleware('http')`**：后者是
      BaseHTTPMiddleware，会把响应体整个搬进一个队列再吐出去，
      而这个代理专门要转大文件下载和 SSE 长连接，经它一道就不流式了。
    - Referer 被上游用 referrer-policy 关掉时这条兜不住——所以响应里的
      `referrer-policy` 会被摘掉（见 `_STRIP`）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            target = _fallback_target(scope)
            if target:
                await RedirectResponse(target, status_code=307)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def describe() -> str:
    """给启动日志用的一句话。数的是全库所有人的卡片，不走那层按用户的缓存。"""
    targets = [t for t in (_to_target(item) for item in navstore.proxy_cards()) if t]
    if not targets:
        return '没有卡片开着（卡片编辑框里「门户代理」那一栏选「开启」）'
    sites = [t for t in targets if t.site]
    text = f'{len(targets)} 张卡片走代理'
    if sites:
        named = sorted({webside.name_of(t.module) for t in sites if t.module})
        text += f'，其中 {len(sites)} 张整站'
        if named:
            text += '（' + '、'.join(named) + '）'
    return text
