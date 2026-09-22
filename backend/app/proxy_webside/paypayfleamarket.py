"""PayPayフリマ（paypayfleamarket.yahoo.co.jp）。

Next.js Pages Router 写的，翻页时去取 `/_next/data/<构建 id>/<路径>.json`——
根绝对地址，正文改写那道特意不碰这种（JS 里 `"/"` 开头的字符串大半不是地址），
靠注入脚本里那层 `fetch` 包装在运行时接住（app/proxyhook.py）。
所以这个站**必须**是整站模式，「直接转发」下翻页会静静地打到门户自己身上，
表现成「首页好好的，一点分类就转圈」。

和 ヤフオク! 同一套账号、同一批图片域名，共用那部分在 `_yahoo.py`。
"""
from . import Ctx
from ._yahoo import HOSTS as _YAHOO_HOSTS
from ._yahoo import on_request as _on_request

NAME = 'PayPayフリマ'

DOMAINS = ('paypayfleamarket.yahoo.co.jp',)

HOSTS = _YAHOO_HOSTS


def on_request(ctx: Ctx, headers: list) -> None:
    _on_request(ctx, headers)


BROWSER_JS = ''
