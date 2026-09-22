"""ヤフオク 和 PayPayフリマ 共用的那部分。

下划线开头 = **这不是一个站点模块**，`__init__.py` 的 `_MODULES` 里不会有它。
两个站在同一套账号体系下（登录都走 `login.yahoo.co.jp`，图都在 `yimg.jp`），
域名清单抄两份的话，改一处忘一处，表现成「ヤフオク 好好的，PayPayフリマ 少半张图」。
"""
from . import Ctx, prefer_japanese

# 两个站都要放行的域名。翻过它们的页面和打包产物之后列出来的，不是照着猜的：
#   yahoo.co.jp   页面、登录（login. / auth.login.）、账号（account.edit.）、
#                 结算（payment. / paypay. / edit.wallet.）
#   yimg.jp       静态资源和图片（s. / auction-assets.c. / paypayfleamarket.c. /
#                 auc-pctr.c. / yads.c.）
#   yahoo-net.jp  帮助中心（support.），页面里的说明链接大半指到这儿
#   yahooapis.jp  approach.（行为日志的信标）
#   yahoo.jp      rdr.（站外链接的跳板）
#   yjtag.jp      广告标签
#   paypay.ne.jp  PayPay 本体
#   lycorp.co.jp  运营方的隐私条款页
HOSTS = (
    'yahoo.co.jp',
    'yimg.jp',
    'yahoo-net.jp',
    'yahooapis.jp',
    'yahoo.jp',
    'yjtag.jp',
    'paypay.ne.jp',
    'lycorp.co.jp',
)


def on_request(ctx: Ctx, headers: list) -> None:
    prefer_japanese(headers)


# 雅虎那几个站的老脚本会写 `document.domain = 'yahoo.co.jp'` 来打通子域。
# 在门户这个域名下赋值会当场抛 SecurityError，把整个脚本带停——
# 这条已经在通用的注入脚本里吞掉了（app/proxyhook.py），不用在这儿再写一遍。
BROWSER_JS = ''
