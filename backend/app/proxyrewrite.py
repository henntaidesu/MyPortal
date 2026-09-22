"""整站代理的内容改写：把上游页面里的地址折回门户底下。

卡片的「门户代理」选成**整站**时才走这里（选「直接转发」的按老样子原样转，
见 app/proxy.py 开头那段）。内网后台多半自己用相对地址，转发一下就能用；
公网站点（メルカリ、ヤフオク、PayPayフリマ 这些）不行——它们的 HTML 里写死的是
`https://web-jp-assets-v2.mercdn.net/...`、`//s.yimg.jp/...` 这类**别的主机**上的
完整地址，浏览器照着去取就绕过了门户，从用户自己的出口 IP 直连上游；
上游按地区拦人的话，页面就成了半张。

所以整站模式做两件事：

1. **地址里带上主机名**。代理地址从 `/api/proxy/<id>/<路径>` 变成

       /api/proxy/<id>/__portal__/<http|https>/<主机[:端口]>/<路径>

   这样一张卡片能转一整族主机（页面在 jp.mercari.com，图片在 mercdn.net），
   而且相对地址天然还落在同一个主机段底下，不用管。

2. **改写响应正文**里的地址：HTML/CSS/JS/JSON 里凡是指向**白名单内**主机的完整地址，
   都换成上面那个形状。白名单外的原样留着（浏览器直连，转不转都不该由这个代理决定）。

**白名单是这里唯一的安全边界**，别去掉：主机名是从 URL 里来的，谁登进来都能随手填一个。
不拦的话这个代理对登录者就成了「想连哪台连哪台」的开放中继（SSRF 中继），
而门户多半正站在内网里。白名单由卡片地址的注册域 + `app/proxy_webside/` 下那个站点的
附带域名 + 卡片上手填的那几个凑出来，见 `allow_for`。

## 改写不到的地方（知道就行，别指望）

- **运行时拼出来的地址**：`fetch(base + '/x')`。字面量那半截会被改写掉，
  所以拼出来的多半还是对的；真拼不出来的由注入浏览器的那段脚本在运行时兜
  （app/proxyhook.py）。
- **`location.href = '...'` 这类赋值**：`Location` 的属性是 unforgeable，脚本拦不住。
  字面量改写能覆盖大半。
- **Worker / iframe srcdoc 里的根绝对地址**：注入的脚本进不去 Worker。
- **reCAPTCHA 之类按域名发牌的第三方**：域名对不上，代理与否都过不了。
"""
import codecs
import re

from . import proxyhook

# 主机名那一段前面的记号。取一个绝不会和上游真实路径撞上的词——
# 撞上了的话上游 `/__portal__/x` 这种路径会被当成主机名段解析，表现成整站乱跳
MARK = '__portal__'

# 域名取「注册域」时要多留一级的那些二级域：jp/cn/uk 这些国家域下面
# co./ne./com. 才是真正的公共后缀。列不全没关系——列漏了只会把白名单收得更窄
# （auctions.yahoo.co.jp 的注册域算成 yahoo.co.jp 而不是 co.jp），错在安全那一侧。
_PUBLIC_2ND = frozenset({
    'co', 'ne', 'or', 'ac', 'go', 'ed', 'gr', 'lg', 'ad',
    'com', 'net', 'org', 'gov', 'edu', 'mil', 'int',
    'ltd', 'plc', 'sch', 'nom', 'info', 'biz', 'name', 'web',
})


def site_of(host: str) -> str:
    """域名 → 注册域。`auctions.yahoo.co.jp` → `yahoo.co.jp`，`jp.mercari.com` → `mercari.com`。

    不带公共后缀表（那是一份几千行、每月都在变的清单，为一个导航页不值当），
    只认「国家域 + 二级公共域」这一条规律。判不准时宁可多留一级——
    多留一级只是白名单窄了点，少留一级就等于放行了别人家整个 co.jp。
    """
    parts = [p for p in (host or '').lower().split('.') if p]
    if len(parts) <= 2:
        return '.'.join(parts)
    if len(parts[-1]) <= 3 and parts[-2] in _PUBLIC_2ND:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])


def host_ok(host: str, allow: tuple[str, ...]) -> bool:
    """这个主机在不在白名单里。白名单存的是**域名后缀**，子域一并算数。"""
    name = (host or '').lower().split(':', 1)[0]
    if not name:
        return False
    return any(name == d or name.endswith('.' + d) for d in allow)


def allow_for(host: str, extra_hosts: tuple[str, ...], manual: object) -> tuple[str, ...]:
    """一张卡片能转到哪些域名。

    三块凑起来：卡片自己那台机器的注册域（子域自动算数，页面和它的静态资源
    多半就在这一族里）、站点模块带来的附带域名（app/proxy_webside/，
    メルカリ的图在 mercdn.net、雅虎的图在 yimg.jp 这种）、卡片上手填的那几个。
    """
    name = (host or '').lower().split(':', 1)[0]
    out = {name}
    # IP 地址没有「注册域」这回事。硬算的话 192.168.1.10 会算出个 `1.10`，
    # 白名单里多一条谁也看不懂的后缀，还白白放行了叫 `x.1.10` 的域名
    if not re.fullmatch(r'[0-9.]+|\[[0-9A-Fa-f:.]+\]', name):
        out.add(site_of(name))
    out.update(d.lower().lstrip('.') for d in extra_hosts if d)
    if isinstance(manual, str):
        manual = re.split(r'[,\s;]+', manual)
    if isinstance(manual, (list, tuple)):
        for one in manual:
            name = str(one or '').strip().lower().lstrip('.')
            # 手填的那栏挡一道：整段粘个 URL 进来是常事，取出主机名就行
            name = re.sub(r'^[a-z][a-z0-9+.\-]*://', '', name).split('/')[0].split(':')[0]
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.\-]*[a-z0-9])?', name or ''):
                continue
            # 单标签的名字（localhost、nas、gitlab 这种内网叫法）是认的，但公共后缀不认：
            # 手滑填一个 `com` 进去，等于把整个 .com 放进白名单，而且从界面上看不出来
            if '.' not in name and (name in _PUBLIC_2ND or len(name) <= 3):
                continue
            out.add(name)
    return tuple(sorted(d for d in out if d))


# ---------------------------------------------------------------- 正文类型

# 值是改写的路数：html 三道都走，css 只管 url()，code 只换完整地址。
# text/x-component 是 Next.js 的 RSC 响应（メルカリ 就是 Next.js），
# 漏了它的话首屏之后翻页拿到的地址全是没改过的，表现成「点进去就白屏」。
_KINDS = {
    'text/html': 'html',
    'application/xhtml+xml': 'html',
    'text/css': 'css',
    'text/javascript': 'code',
    'application/javascript': 'code',
    'application/x-javascript': 'code',
    'application/json': 'code',
    'application/ld+json': 'code',
    'application/manifest+json': 'code',
    'text/x-component': 'code',
    'text/xml': 'code',
    'application/xml': 'code',
    'application/rss+xml': 'code',
}

# 超过这个大小就不改写了，原样流式转回去。改写要把整份读进内存，
# 而这个代理专门也要转大文件下载——真有 200 MB 的 JS 的话，那份不改也罢
MAX_REWRITE = 8 * 1024 * 1024


def kind_of(content_type: str) -> str | None:
    """按 Content-Type 决定怎么改；不认得的返回 None（原样转）。"""
    return _KINDS.get((content_type or '').split(';', 1)[0].strip().lower())


def charset_of(content_type: str, default: str = 'utf-8') -> str | None:
    """取 Content-Type 里的字符集。认不出这个编码就返回 None——那种就别改了。

    不统一转成 utf-8 再发回去：老系统的页面有 shift_jis、euc-jp、gb2312 的，
    转了编码还得同步改 `<meta charset>`，漏一处就是一屏乱码。
    改写只往里插 ASCII，用原编码写回去最稳。
    """
    m = re.search(r'charset\s*=\s*"?([\w.\-]+)"?', content_type or '', re.I)
    name = (m.group(1) if m else default).strip()
    try:
        codecs.lookup(name)
    except LookupError:
        return None
    return name


# ---------------------------------------------------------------- 改写

# 完整地址（`https://host`）和协议相对地址（`//host`）。两种转义都认：
#   "https://s.yimg.jp/x"      HTML 里、JS 源码里
#   "https:\/\/s.yimg.jp\/x"   JSON 和打包后的 JS 里很常见
# 只换到主机名为止，后面的路径原样留着——这样路径里是 `\/` 还是 `/` 都不用管。
# **主机名里必须带点**：不带点的话，JS 源码里的 `// 这是注释` 会被当成协议相对地址，
# 后面那个词恰好在白名单里就被改坏了。代价是 `http://nas/` 这种单标签的内网名字
# 在正文里改不着（根绝对和相对地址不受影响，照常能用）。
_ABS = re.compile(r"""
    (?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*:)?       # 协议，可以没有（协议相对地址）
    (?P<slash>\\?/\\?/)                          # // 或者 \/\/
    (?P<host>(?:[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,}
              |\d{1,3}(?:\.\d{1,3}){3})           # 内网页面里指向另一台机器的 IP 地址
             (?::\d{1,5})?)
    (?=[/\\"'<>)\]}\s,;?#&]|$)                   # 主机名到这儿为止
""", re.X)

# HTML 里的根绝对地址。只挑真的是地址的那几个属性，别拿 `\S+=` 一把梭——
# 把 `data-x="/a"` 这种业务字段也改了，出问题时根本想不到是代理干的
_ATTR = re.compile(r"""
    (?P<pre>\s(?:href|src|action|formaction|poster|ping|manifest|data)\s*=\s*)
    (?P<q>["'])(?P<v>/(?!/)[^"'>]*)(?P=q)
""", re.X | re.I)

_SRCSET = re.compile(r"""(?P<pre>\s(?:srcset|imagesrcset)\s*=\s*)(?P<q>["'])(?P<v>[^"'>]*)(?P=q)""",
                     re.X | re.I)

_CSS_URL = re.compile(r"""(?P<pre>url\(\s*)(?P<q>["']?)(?P<v>/(?!/)[^"')]*)(?P=q)""", re.I)
_CSS_IMPORT = re.compile(r"""(?P<pre>@import\s+)(?P<q>["'])(?P<v>/(?!/)[^"']*)(?P=q)""", re.I)

# 子资源完整性校验：正文被我们改过，哈希必然对不上，浏览器会直接**拒绝执行**那个脚本。
# 表现成「页面白屏，控制台说 integrity 不匹配」，所以整段拆掉
_INTEGRITY = re.compile(r"""\s+integrity\s*=\s*(["']).*?\1""", re.I | re.S)

# 页面里自带的 CSP 和 referrer 策略：前者是照着上游那个域名写的，搬到门户这个域名下
# 只会拦掉自己（表单 action、脚本来源全对不上）；后者关掉 Referer 的话，
# app/proxy.py 末尾那条靠 Referer 兜根绝对地址的路就断了
_META_KILL = re.compile(
    r"""<meta\s[^>]*(?:http-equiv\s*=\s*["']?content-security-policy|name\s*=\s*["']?referrer)[^>]*>""",
    re.I)

_HEAD = re.compile(r'<head\b[^>]*>', re.I)
_META_CHARSET = re.compile(r'<meta\s[^>]*charset\s*=[^>]*>', re.I)


class Rewriter:
    """一张卡片一套改写规则。`mount` 是 `/api/proxy/<卡片 id>/`。"""

    __slots__ = ('mount', 'base', 'allow', 'extra_js', '_proxied')

    def __init__(self, mount: str, allow: tuple[str, ...], extra_js: str = ''):
        self.mount = mount
        self.base = f'{mount}{MARK}/'
        self.allow = allow
        self.extra_js = extra_js
        # 反向：代理地址 → 真实地址。前面那半截门户地址可有可无，
        # 页面脚本拿 location.href 拼出来的是带门户地址的完整形状
        self._proxied = re.compile(r'(?:(https?)://[^/\s"\'<>]+)?' + re.escape(self.base)
                                   + r'(https?)/([^/?#\s"\'<>]+)(/[^\s"\'<>]*)?')

    # ---------------- 单个地址

    def url(self, raw: str, scheme: str, host: str) -> str:
        """一个地址 → 代理底下的地址。转不了的原样返回（调用方照原样用）。

        Location 和 Link 首部也走这儿，所以四种形状都要认：完整地址、
        协议相对（`//host/x`）、根绝对（`/x`）、相对（原样）。
        """
        value = (raw or '').strip()
        if not value:
            return raw
        if value.startswith(self.mount):
            return raw                                  # 已经在代理底下了
        if value.startswith('//'):
            head, _, tail = value[2:].partition('/')
            if not host_ok(head, self.allow):
                return raw
            return f'{self.base}{scheme}/{head}/{tail}' if tail else f'{self.base}{scheme}/{head}/'
        if value.startswith('/'):
            return self.base + f'{scheme}/{host}' + value
        m = re.match(r'(?i)(https?)://([^/?#]+)(.*)', value)
        if not m:
            return raw                                  # 相对地址，或者 ws:/mailto: 这类
        up, head, tail = m.group(1).lower(), m.group(2), m.group(3)
        if '@' in head or not host_ok(head, self.allow):
            # 带 userinfo 的地址（user@host）不接：主机名在哪一段上容易看走眼，
            # 而看走眼的后果是白名单被绕过
            return raw
        return f'{self.base}{up}/{head}' + (tail or '/')

    def unproxy(self, value: str) -> str:
        """反过来：代理地址 → 真实地址。**上游不该看见门户的地址。**

        `Referer` 要用（上游按它判来路，给一条门户路径等于说「不知道从哪儿来的」），
        查询串里的返回地址也要用：登录那一步常见的写法是
        `login?.done=<当前页面地址>`，页面脚本拿 `location.href` 拼出来的是门户地址，
        上游一看不是自己家的地址就不认，登完弹回首页——表现成「登录成功了但没跳回来」。
        """
        if not value or self.base not in value:
            return value
        return self._proxied.sub(
            lambda m: f'{m.group(2)}://{m.group(3)}{m.group(4) or "/"}', value)

    # ---------------- 正文

    def body(self, kind: str, text: str, scheme: str, host: str) -> str:
        if kind == 'html':
            return self.html(text, scheme, host)
        if kind == 'css':
            return self.css(text, scheme, host)
        return self.code(text, scheme, host)

    def code(self, text: str, scheme: str, host: str) -> str:
        """JS / JSON / XML：只换完整地址和协议相对地址。

        **根绝对地址（`"/api/items"`）这里不碰**：JS 里 `"/"` 开头的字符串大半不是地址，
        是路由名、正则、模板。改错了页面还能跑，但行为悄悄变了，最难查。
        那一类交给注入浏览器的那段脚本在真要发请求时再判（app/proxyhook.py）。
        """
        return self._absolute(text, scheme)

    def css(self, text: str, scheme: str, host: str) -> str:
        text = self._absolute(text, scheme)
        text = _CSS_URL.sub(lambda m: self._root(m, scheme, host), text)
        text = _CSS_IMPORT.sub(lambda m: self._root(m, scheme, host), text)
        return text

    def html(self, text: str, scheme: str, host: str) -> str:
        text = _META_KILL.sub('', text)
        text = _INTEGRITY.sub('', text)
        text = self._absolute(text, scheme)             # 先换完整地址
        text = _ATTR.sub(lambda m: self._root(m, scheme, host), text)
        text = _SRCSET.sub(lambda m: self._srcset(m, scheme, host), text)
        text = _CSS_URL.sub(lambda m: self._root(m, scheme, host), text)   # style="..." 里的
        return self._inject(text, scheme, host)

    # ---------------- 三个替换函数

    def _absolute(self, text: str, scheme: str) -> str:
        def sub(m: re.Match) -> str:
            got = (m.group('scheme') or '').lower()
            if got and got not in ('http:', 'https:'):
                # ws://、blob:、自定义协议：`//` 前面有别的协议名就别动。
                # 尤其 ws/wss —— 那条由 app/proxyhook.py 在运行时换，
                # 在这儿换成 http 形状的话握手都发不出去
                return m.group(0)
            head = m.group('host')
            if not host_ok(head, self.allow):
                return m.group(0)
            return f'{self.base}{(got[:-1] if got else scheme)}/{head}'
        return _ABS.sub(sub, text)

    def _root(self, m: re.Match, scheme: str, host: str) -> str:
        value = m.group('v')
        if value.startswith(self.mount):
            return m.group(0)                           # 上一道已经换过了，别套两层
        return f'{m.group("pre")}{m.group("q")}{self.base}{scheme}/{host}{value}{m.group("q")}'

    def _srcset(self, m: re.Match, scheme: str, host: str) -> str:
        """srcset 是「地址 描述符」用逗号隔开的一串，得一个一个换。"""
        out = []
        for part in m.group('v').split(','):
            piece = part.strip()
            if not piece:
                continue
            bits = piece.split(None, 1)
            bits[0] = self.url(bits[0], scheme, host)
            out.append(' '.join(bits))
        return f'{m.group("pre")}{m.group("q")}{", ".join(out)}{m.group("q")}'

    # ---------------- 注入

    def _inject(self, text: str, scheme: str, host: str) -> str:
        """把运行时那段脚本插进 `<head>`。

        插在 `<meta charset>` **后面**：整段脚本有好几 KB，插在前面会把 charset 声明
        挤出文档开头那 1024 字节，浏览器就猜不到编码了，表现成一屏乱码。
        （响应头里的 charset 本来优先级更高，但上游不给这个头的页面也有。）
        """
        script = proxyhook.script(self.base, self.mount, scheme, host, self.allow, self.extra_js)
        head = _HEAD.search(text)
        if head:
            at = head.end()
            meta = _META_CHARSET.search(text, at, at + 2048)
            if meta:
                at = meta.end()
            return text[:at] + script + text[at:]
        return script + text

    # ---------------- 首部里的地址

    def link_header(self, value: str, scheme: str, host: str) -> str:
        """`Link: </x>; rel=preload, <https://cdn/y>; rel=preconnect` 里的地址。

        预加载指到没改写过的地址上，浏览器会照着直连一份、再跟着页面里改写过的那份
        又下一份，白费一趟；`rel=preload` 对不上还会在控制台刷警告。
        """
        return re.sub(r'<([^>]*)>', lambda m: '<' + self.url(m.group(1), scheme, host) + '>', value)
