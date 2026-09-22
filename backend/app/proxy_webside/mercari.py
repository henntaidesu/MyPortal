"""メルカリ（jp.mercari.com）。

日本的二手交易站，Next.js App Router 写的。整站代理它要过三关：

1. **静态资源全在别的域名上**：页面在 `jp.mercari.com`，JS 和 CSS 在
   `web-jp-assets-v2.mercdn.net`，商品图在 `static.mercdn.net`。
   不把 `mercdn.net` 放进白名单的话，页面能开，但是一张图没有、脚本一个不跑。
2. **接口是同域的相对地址**（`/v1/...` 这种），所以没有额外的接口域名要放行——
   翻过它的打包产物确认过，里面出现的外部域名只有文档链接。
3. **App Router 的路由首部里带着「当前路径」**，见下面 `on_request`。

按地区拦人这件事由通用那边管：整站模式不会把 `X-Forwarded-For` 发给上游
（见 app/proxy.py 的 `_request_headers`），所以上游看到的是门户这台机器的出口 IP，
而不是用户自己的。这正是这个功能的意义所在——门户放在能开这个站的网络里。
"""
from . import Ctx, get_header, prefer_japanese, set_header, upstream_path

NAME = 'メルカリ'

# 卡片地址落在这些域名下就用这份规则。`jp.mercari.com`、`www.mercari.com` 都算
DOMAINS = ('mercari.com', 'mercari.jp', 'mercdn.net')

HOSTS = (
    'mercari.com',                  # jp. / www. / about. / auth. / help.jp.
    'mercari.jp',
    'mercdn.net',                   # web-jp-assets-v2.（JS/CSS）、static.（商品图）
    'merpay.com',                   # 结算那一段会跳过去
    'mercari-shops.com',
    'mercari-shops-static.com',
    'mercariapp.com',
)


def on_request(ctx: Ctx, headers: list) -> None:
    prefer_japanese(headers)

    # App Router 翻页时不整页刷新，而是带上 `RSC: 1` 要一份组件负载，
    # 同时用 `Next-Url` 首部告诉服务端「我现在在哪一页」。浏览器地址栏这会儿是
    # /api/proxy/<id>/__portal__/https/jp.mercari.com/items/m123，客户端路由照抄进这个首部，
    # 上游拿到的就是一条它根本没有的路由，回一份对不上的负载——表现成「点商品进去一片空白，
    # 刷新一下又好了」（刷新走的是整页请求，不带这个首部）。还原成上游自己的路径。
    url = get_header(headers, 'next-url')
    if url:
        set_header(headers, 'next-url', upstream_path(ctx, url))

    # 路由状态树里也埋着同一条路径，但它是 JSON 又整个 URL 编码过，
    # 拆开改回去太脆。里面确实混进了代理路径就整条丢掉：上游会当成首次进入这条路由，
    # 回一份完整的树。代价只是这一次少了点增量渲染的优化，换来的是路由不会错位。
    tree = get_header(headers, 'next-router-state-tree')
    if tree and ('%2Fapi%2Fproxy%2F' in tree or ctx.mount in tree):
        headers[:] = [(k, v) for k, v in headers if k.lower() != 'next-router-state-tree']


# 这个站暂时没有需要额外注入的脚本：路由跳转走 history.pushState、
# 取数据走 fetch，两条通用钩子都盖得住（app/proxyhook.py）。
BROWSER_JS = ''
