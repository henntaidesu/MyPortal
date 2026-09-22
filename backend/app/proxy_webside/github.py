"""GitHub（github.com）。

整站代理它，要紧的只有一件事：**资源全在别的域名上**。页面在 `github.com`，
但 JS / CSS / 字体在 `github.githubassets.com`，头像、README 里的图、
Release 的附件在 `*.githubusercontent.com`。不把这两个放进白名单的话，
页面能开、文字也在，但**一百多个请求会从用户自己的出口 IP 直接打到 GitHub**——
门户就白架了（实测首页 152 个请求全绕过去）。

其余几条都由通用那套盖住了，这里不用写：

- `__Host-` / `__Secure-` 开头的 Cookie（GitHub 的会话 Cookie 是
  `__Host-user_session_same_site`）在代理底下条件满足不了，会被浏览器直接丢掉。
  改名下发、回传时改回去已经在 app/proxy.py 的 `_rewrite_cookie` 里做了。
- `Content-Security-Policy` 照着 github.com 写的，搬到门户域名下只会拦到自己，
  由 `_STRIP` 摘掉。
- Turbo（GitHub 的局部刷新）走 `fetch`，注入的脚本包着（app/proxyhook.py）。
- `alive.github.com` 的 WebSocket（实时通知、Actions 日志）落在 `github.com`
  底下，白名单自动放行，WS 那条路由照转。

**过不去的**：登录页上的 reCAPTCHA（`www.gstatic.com` / `www.google.com`）是按域名
发牌的，代理与否都一样，这类第三方本来就在白名单外、原样直连。营销页里嵌的
YouTube 同理，不值得为它开一个域名。
"""
NAME = 'GitHub'

DOMAINS = ('github.com',)

HOSTS = (
    'github.com',           # 页面、api.、collector.、codeload.（下载 ZIP）、
                            # alive.（WebSocket）、gist.、docs.、*-feed.
    'githubassets.com',     # github. —— JS / CSS / 字体 / 图标，量最大的那一批
    'githubusercontent.com',  # avatars. / raw. / camo. / user-images. /
                              # objects.（Release 附件）/ private-user-images.
    # 营销页（/features/... 那些）的配图走 Contentful。写全主机名不写
    # `ctfassets.net`：那是 Contentful 给所有客户共用的域，放行整个注册域等于
    # 让这张卡片能借门户去取任何一家租户的东西
    'images.ctfassets.net',
)


# **JS 正文里的地址照常改写**（没关 `REWRITE_JS`）。GitHub 这边必须改：
# 它的前端是 ES 模块 + 动态 `import()` 拼出来的，而 `import()` 是语法不是函数，
# 注入的脚本包不住它——不改的话页面一交互就去 githubassets.com 直连拉 chunk。
# 反过来它也不像 メルカリ 那样拿地址算签名，改了不会把鉴权搞坏。
