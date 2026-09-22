"""ヤフオク!（auctions.yahoo.co.jp）。

和 PayPayフリマ 是两张卡片、两个站，但同一套账号和同一批静态资源域名，
共用那部分在 `_yahoo.py`。

这个站要注意的是**登录会跨到别的主机上**：`login.yahoo.co.jp` 登完之后
`Set-Cookie` 带的是 `domain=.yahoo.co.jp`，指望这枚 Cookie 回头能跟着
`auctions.yahoo.co.jp` 的请求一起发出去。代理底下 Domain 属性会被去掉、
Path 统一钉成 `/api/proxy/<卡片 id>/`（见 app/proxy.py 的 `_rewrite_cookie`），
所以这张卡片底下的所有雅虎主机共用一份 Cookie——正好是登录要的效果。
"""
from . import Ctx
from ._yahoo import HOSTS as _YAHOO_HOSTS
from ._yahoo import on_request as _on_request

NAME = 'ヤフオク!'

# 只认领这一个主机：PayPayフリマ 也在 yahoo.co.jp 底下，写成 'yahoo.co.jp'
# 会把对方的卡片一并认领走（`find` 按后缀最长的算，但两边都写宽了就分不开了）
DOMAINS = ('auctions.yahoo.co.jp',)

HOSTS = _YAHOO_HOSTS


def on_request(ctx: Ctx, headers: list) -> None:
    _on_request(ctx, headers)


BROWSER_JS = ''
