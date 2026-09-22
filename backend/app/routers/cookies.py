"""Cookie 代理的管理接口：看这张卡片存了多少条、把它倒空。

**只有两条，而且都不返回任何 Cookie 的值。** 值是用户在外部站点的登录凭证，
门户自己转发时要用，但没有任何理由把它交回给浏览器——真交回去了，页面里
随便一个第三方脚本都能顺走。

挂在 `/api/cookies` 而不是 `/api/proxy/<id>/__cookies__`：后者会撞上代理那条
`/{item_id}/{path:path}` 的全匹配路由，而且「门户自己的接口」和「转给上游的路径」
混在同一个前缀下，迟早有一天某个上游站点真有一个叫 `__cookies__` 的路径。
"""
from fastapi import APIRouter, Depends, HTTPException

from .. import cookiejar, navstore, secretbox
from ..auth import require_login

router = APIRouter(prefix='/api/cookies', tags=['cookies'])


@router.get('/{item_id}')
def peek(item_id: str, user: dict = Depends(require_login)):
    """这张卡片罐子里的条数、涉及哪些域、最后更新时间。

    **归属校验和代理那边同一个路数**：拿 (当前用户, 卡片 id) 去查，查不到就 404，
    不是查出来再比对。不然拿一串 id 就能探出别人开了哪些卡片、登过哪些站。
    """
    if navstore.card(int(user['id']), item_id) is None:
        raise HTTPException(404, '没有这张卡片')
    info = cookiejar.stats(int(user['id']), item_id)
    info['available'] = secretbox.available()
    if not info['available']:
        info['reason'] = secretbox.why_unavailable()
    return info


@router.delete('/{item_id}')
def clear(item_id: str, user: dict = Depends(require_login)):
    """倒空 = 退出那个外部站点的登录。

    这里**不**要求卡片还存在：卡片删掉之后残留的行也该让人清得掉。
    反正查询条件里钉着 user_id，清的只可能是自己的。
    """
    return {'removed': cookiejar.clear(int(user['id']), item_id)}
