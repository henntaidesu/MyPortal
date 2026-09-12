"""SSO 核心：签发一次性票据，业务系统后端拿票换用户身份。

为什么是这套流程（CAS 风格）而不是直接把令牌塞进 URL：
  - 门户和业务系统不同域，Cookie 天然带不过去；
  - URL 里只出现一次性、60 秒过期、绑死 client 的票据，
    就算被浏览器历史、Referer、日志记下来也换不出身份；
  - 真正的身份数据走服务端到服务端的 HTTP，中间不经过浏览器。
"""
import html
import json
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import clients, db, settings
from ..deps import clear_session_cookie, current_session
from ..notify import broadcast_logout
from ..security import secret_equal

router = APIRouter(prefix='/sso', tags=['sso'])


def _error_page(title: str, detail: str, status: int = 400) -> HTMLResponse:
    """出错就停在这里，绝不往没验证过的地址跳。"""
    body = f"""<!doctype html><meta charset="utf-8">
<title>单点登录失败</title>
<style>
  body {{ font: 15px/1.7 system-ui, "Microsoft YaHei", sans-serif; color: #1c2026;
         background: #f6f7f9; display: flex; min-height: 100vh; margin: 0;
         align-items: center; justify-content: center; padding: 20px; }}
  .box {{ background: #fff; border: 1px solid #e4e7ec; border-radius: 12px;
          padding: 28px 32px; max-width: 520px; }}
  h1 {{ font-size: 17px; margin: 0 0 10px; }}
  p {{ margin: 0; color: #5a6472; }}
  code {{ background: #f2f4f7; padding: 2px 6px; border-radius: 4px; }}
</style>
<div class="box"><h1>{html.escape(title)}</h1><p>{detail}</p></div>"""
    return HTMLResponse(body, status_code=status)


def _with_query(url: str, params: dict[str, str]) -> str:
    """往地址上追加参数，原本就带 ? 的也不会被拼坏。"""
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: v for k, v in params.items() if v})
    return urlunparse(parts._replace(query=urlencode(query)))


@router.get('/authorize')
def authorize(request: Request, client_id: str = '', redirect_uri: str = '', state: str = ''):
    """浏览器跳到这里。带着门户会话就发票，没有就先去登录。"""
    client = clients.get(client_id)
    if client is None:
        return _error_page(
            '未注册的系统',
            f'client_id <code>{html.escape(client_id) or "(空)"}</code> 不在 '
            '数据库的 clients 表里，先用 <code>python manage.py addclient</code> 注册。')

    target = clients.check_redirect_uri(client, redirect_uri.strip())
    if target is None:
        return _error_page(
            '回调地址未登记',
            f'<code>{html.escape(redirect_uri)}</code> 不在 <code>{html.escape(client_id)}</code> '
            '的 redirect_uris 白名单里。必须一字不差地匹配，'
            '这是防止票据被送去别人服务器的关键。')

    session = current_session(request)
    if session is None:
        # 回门户登录，登完再原样跳回来。next 只允许站内路径，不接受完整地址，
        # 否则这个接口就成了给别人用的开放重定向。
        back = '/sso/authorize?' + urlencode(
            {'client_id': client_id, 'redirect_uri': target, 'state': state})
        return RedirectResponse('/?' + urlencode({'next': back}), status_code=302)

    ticket = db.issue_ticket(client_id, target, session['user']['id'], session['sid'])
    return RedirectResponse(_with_query(target, {'ticket': ticket, 'state': state}),
                            status_code=302)


async def _read_body(request: Request) -> dict:
    """JSON 和表单都收，省得各语言的 HTTP 库还要迁就我们。"""
    ctype = request.headers.get('content-type', '')
    if 'application/json' in ctype:
        try:
            data = json.loads(await request.body() or b'{}')
        except json.JSONDecodeError:
            raise HTTPException(400, 'body 不是合法 JSON')
        return data if isinstance(data, dict) else {}
    return dict(await request.form())


@router.post('/validate')
async def validate(request: Request):
    """业务系统后端调这里换身份。必须服务端到服务端，别放到前端 JS 里。"""
    body = await _read_body(request)
    client_id = str(body.get('client_id') or '').strip()
    secret = str(body.get('client_secret') or body.get('secret') or '')
    ticket = str(body.get('ticket') or '').strip()

    client = clients.get(client_id)
    if client is None or not client['secret'] or not secret_equal(secret, client['secret']):
        raise HTTPException(401, 'client_id 或 client_secret 不对')

    result = db.consume_ticket(ticket, client_id)
    if result is None:
        raise HTTPException(401, '票据无效、已过期或已经用过')

    return {
        'ok': True,
        'user': {k: result['user'][k] for k in ('username', 'display_name', 'email', 'roles')},
        'sid': result['session_sid'],
        'session_expires_at': result['session_expires_at'],
    }


@router.get('/logout')
async def sso_logout(request: Request, client_id: str = ''):
    """业务系统里点「退出」可以跳到这里，顺手把门户和其它系统一起退掉。

    回跳地址只认注册时填的 home_url，不接受调用方自带地址：
    登出接口最容易被拿来做开放重定向。
    """
    sid = db.drop_session(request.cookies.get(settings.session_cookie(), ''))

    client = clients.get(client_id)
    target = (client['home_url'] if client else '') or '/'

    response = RedirectResponse(target, status_code=302)
    clear_session_cookie(response)
    if sid:
        await broadcast_logout(sid)
    return response
