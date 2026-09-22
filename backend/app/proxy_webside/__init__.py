"""按站点分开写的那些特化规则，一个站点一个文件。

整站代理的通用部分在 app/proxyrewrite.py（改正文）和 app/proxyhook.py（改运行时），
那两处对谁都一样。但具体到某个站，总有几条只属于它的事：

- **图和接口散在别的域名上**：メルカリ 的静态资源在 `mercdn.net`，雅虎的在 `yimg.jp`。
  白名单得把这些一并放进去，否则页面能开，图全裂。
- **首部得顺着它的脾气**：日文站按 `Accept-Language` 决定给日文还是英文页面；
  Next.js 那种把「当前路径」塞进首部的，得把代理路径还原回去。
- **偶尔要动一动正文或者补一小段脚本**：某个站有它自己那套跳转写法，
  通用那几条盖不住。

把这些堆进 proxy.py 的话，那个文件很快就会变成一坨 if-else，而且改 A 站的规则要在
一堆 B 站的分支中间下手。所以一个站一个文件，这里只做一件事：按卡片地址的主机名
把对应的模块找出来。

## 写一个新站点

新建 `app/proxy_webside/<站名>.py`，照 mercari.py 的样子写，然后加进文件末尾那个
`_MODULES`。模块里这几样都是可选的，缺了就当没有：

    NAME         str          日志里显示的名字
    DOMAINS      tuple[str]   认领哪些卡片（域名后缀，子域算数）
    HOSTS        tuple[str]   这个站还要一起代理的域名后缀
    BROWSER_JS   str          追加到注入脚本末尾的 JS（能用 window.__portal）
    REWRITE_JS   bool         默认 True；False = 不改这个站 JS/JSON 里的地址（见下）
    on_request(ctx, headers)   就地改发给上游的首部（list[tuple[str, str]]）
    on_response(ctx, headers)  就地改带回浏览器的首部（list[tuple[bytes, bytes]]）
    on_html(ctx, text) -> str  通用改写之后再过一道正文

## REWRITE_JS：什么时候该关

改写分两种，性质完全不同：

- **HTML / CSS 里的地址**：浏览器解析到就直接拿去发请求了，注入的脚本插不上手，
  所以这一道非改不可，`REWRITE_JS` 也管不着它。
- **JS / JSON 里的地址**：总要经过 `fetch`、`XHR`、某个元素的 `src` 才发得出去，
  而这几个出口注入的脚本全包了（app/proxyhook.py），**不改也照样走代理**。

改了反而有代价：`https://api.example.com` 变成 `/api/proxy/<id>/…` 之后就不再是一条
**绝对**地址了，而站点代码往往当它是绝对地址在用——

- `new URL(路径, 那个常量)` 直接抛 `Invalid base URL`，React 那类框架的错误边界
  一接住就是整页「页面加载失败」，而且报错在浏览器里，后端日志一片 200；
- 拿地址算签名的（メルカリ 的 DPoP 把请求路径签进 `htu` 里）签的是代理路径，
  上游一对不上就 401，表现成「页面壳子出来了，内容全是空的」。

所以：**站点自己拿地址做文章的（签名、鉴权、SDK 里写死的 origin 比对），把这项关掉。**
代价是 `location.href = 'https://…'` 这种钩子拦不住的跳转会直接走出代理
（`Location` 的属性是 unforgeable，脚本改不了）——关之前先点几下确认没有这种写法。

**被认领的卡片会自动按整站模式办**（见 app/proxy.py 的 `_to_target`）：这里躺着的
本来就是「直接转发必坏」的那几个站，认出来了还按直接转发办没有意义。

**`_MODULES` 必须是写死的 import**，别改成 pkgutil 扫目录：PyInstaller 靠静态分析
决定哪些模块要打进 exe，扫出来的动态导入它看不见，表现成「源码跑得好好的，
打包出来一到这几个站就没有特化规则」，而且不报错。
"""
import re
from types import ModuleType
from typing import NamedTuple, Optional


class Ctx(NamedTuple):
    """一次转发的上下文，给下面几个钩子用。"""
    item_id: str        # 卡片 id
    mount: str          # /api/proxy/<id>/
    base: str           # /api/proxy/<id>/__portal__/
    scheme: str         # 这次请求发给上游时用的协议
    host: str           # 这次请求发给上游的主机名
    path: str           # 上游那边的路径（不带查询串）
    method: str


def find(host: str) -> Optional[ModuleType]:
    """卡片地址的主机名 → 站点模块。没有对应的返回 None（只走通用那套）。

    后缀最长的赢：`auctions.yahoo.co.jp` 和 `paypayfleamarket.yahoo.co.jp` 是两个站，
    谁也别把对方的卡片认领走。
    """
    name = (host or '').lower().split(':', 1)[0]
    best, best_len = None, -1
    for mod in _MODULES:
        for domain in getattr(mod, 'DOMAINS', ()):
            d = domain.lower().lstrip('.')
            if (name == d or name.endswith('.' + d)) and len(d) > best_len:
                best, best_len = mod, len(d)
    return best


def hosts_of(mod: Optional[ModuleType]) -> tuple[str, ...]:
    return tuple(getattr(mod, 'HOSTS', ())) if mod else ()


def browser_js(mod: Optional[ModuleType]) -> str:
    return str(getattr(mod, 'BROWSER_JS', '') or '') if mod else ''


def rewrite_js(mod: Optional[ModuleType]) -> bool:
    """这个站的 JS/JSON 正文要不要改写地址。没写就是要（默认 True），见上面那段。"""
    return bool(getattr(mod, 'REWRITE_JS', True)) if mod else True


def name_of(mod: Optional[ModuleType]) -> str:
    return str(getattr(mod, 'NAME', '')) if mod else ''


def on_request(mod: Optional[ModuleType], ctx: Ctx, headers: list) -> None:
    _call(mod, 'on_request', ctx, headers)


def on_response(mod: Optional[ModuleType], ctx: Ctx, headers: list) -> None:
    _call(mod, 'on_response', ctx, headers)


def on_html(mod: Optional[ModuleType], ctx: Ctx, text: str) -> str:
    hook = getattr(mod, 'on_html', None) if mod else None
    if hook is None:
        return text
    try:
        out = hook(ctx, text)
    except Exception as exc:      # noqa: BLE001
        print(f'[代理] {name_of(mod)} 的 on_html 出错，这一份按原样发回: {exc}')
        return text
    return out if isinstance(out, str) else text


def _call(mod: Optional[ModuleType], hook_name: str, ctx: Ctx, headers: list) -> None:
    """钩子抛异常不能把整条请求带崩。

    这几个文件是「某个站现在这么写」的经验，站点改版之后随时可能对不上。
    对不上的代价应该是「这个站的特化规则没生效」，不是「这张卡片点了就 500」。
    """
    hook = getattr(mod, hook_name, None) if mod else None
    if hook is None:
        return
    try:
        hook(ctx, headers)
    except Exception as exc:      # noqa: BLE001
        print(f'[代理] {name_of(mod)} 的 {hook_name} 出错，忽略: {exc}')


# ---------------------------------------------------------------- 给各站共用的小工具

def set_header(headers: list, name: str, value: str) -> None:
    """改发给上游的某个首部（没有就加上）。给 on_request 用。"""
    low = name.lower()
    for i, (key, _) in enumerate(headers):
        if key.lower() == low:
            headers[i] = (key, value)
            return
    headers.append((name, value))


def get_header(headers: list, name: str, default: str = '') -> str:
    low = name.lower()
    for key, value in headers:
        if key.lower() == low:
            return value
    return default


def upstream_path(ctx: Ctx, value: str) -> str:
    """把一段文本里的代理地址还原成**上游那边的路径**。

    有些站点会把「我现在在哪一页」放进请求首部或者查询串里（メルカリ 的 `Next-Url`
    就是），浏览器地址栏这会儿是一条代理路径，照抄过去上游认不得。
    协议和主机名一并丢掉，只留路径——这类字段要的本来就是路径。
    """
    if not value:
        return value
    pattern = re.compile(r'(?:https?://[^/\s"]+)?' + re.escape(ctx.base)
                         + r'https?/[^/?#\s"]+(/[^\s"]*)?')
    return pattern.sub(lambda m: m.group(1) or '/', value)


def prefer_japanese(headers: list) -> None:
    """`Accept-Language` 里没有日语就补一个在最前面。

    这几个站都按这个首部决定给日文页还是英文页。浏览器多半报的是中文或英文，
    照直转过去的话，日本站点会给一份翻译过的、菜单和真实站点对不上的页面
    （メルカリ 更干脆，非日语直接引导去海外版）。
    """
    value = get_header(headers, 'accept-language')
    if 'ja' in value.lower():
        return
    set_header(headers, 'accept-language', 'ja-JP,ja;q=0.9' + (f',{value}' if value else ''))


# ---------------------------------------------------------------- 站点清单
# **必须放在文件末尾**：下面几个模块 import 的是本文件里的 Ctx 和那几个小工具，
# 放在开头的话它们 import 回来时这些还没定义，整个包都起不来。
from . import github, mercari, paypayfleamarket, yahoo_auctions     # noqa: E402

_MODULES: tuple[ModuleType, ...] = (github, mercari, paypayfleamarket, yahoo_auctions)
