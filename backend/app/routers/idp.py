"""门户当 OIDC 提供方的那几个端点（逻辑在 app/idp.py）。

    GET  /.well-known/openid-configuration   发现文档
    GET  /api/oidc/authorize                  浏览器跳过来；没登录先去登录页
    POST /api/oidc/token                      对方后端拿授权码换 token
    GET  /api/oidc/userinfo                   对方后端拿 access_token 问「这是谁」
    GET  /api/oidc/jwks                       HS256 用不上，给个空集合

    GET  /api/idp/cards/<卡片 id>             卡片编辑框：这张卡片登记的客户端（管理员）
    PUT  /api/idp/cards/<卡片 id>             卡片编辑框：登记 / 修改（管理员）

    GET  /api/idp/settings                    提供方总开关 / issuer / 客户端一览（管理员）
    PUT  /api/idp/settings                    改总开关 / issuer（管理员），即刻生效

总开关关着时 oidc 那几条一律 404，和没有这几条路一样。卡片和设置那几条不看它：
先把客户端登记好、再打开总开关是正常顺序，编辑框里会提示还没开。
"""
import base64
from html import escape
from urllib.parse import parse_qs, quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .. import auth, config, idp

router = APIRouter(tags=['idp'])

_NO_STORE = {'cache-control': 'no-store', 'pragma': 'no-cache'}


def _enabled() -> None:
    if not idp.settings()['enabled']:
        raise HTTPException(404, '门户没有开单点登录提供方（右上角菜单 → 单点登录）')


def _request_origin(request: Request) -> str:
    proto = request.headers.get('x-forwarded-proto') or request.url.scheme
    host = request.headers.get('x-forwarded-host') or request.headers.get('host') or ''
    return f'{proto}://{host}'


def _issuer(request: Request) -> str:
    """设置里写死了就用那个。没写就按这次请求现拼——只有浏览器那一跳会走到这儿，
    拼出来的就是浏览器看到的地址。"""
    issuer = idp.settings()['issuer']
    if issuer:
        return issuer
    proto = request.headers.get('x-forwarded-proto') or request.url.scheme
    host = request.headers.get('x-forwarded-host') or request.headers.get('host') or ''
    return f'{proto}://{host}'


def _error_page(message: str) -> HTMLResponse:
    """client_id / redirect_uri 不对时只能停在门户这儿说清楚，**不能跳回去**：
    往一个没登记过的地址跳正是开放重定向（RFC 6749 4.1.2.1）。"""
    return HTMLResponse(
        '<!doctype html><meta charset="utf-8"><title>单点登录失败</title>'
        '<body style="font-family:sans-serif;padding:40px">'
        f'<h2>单点登录失败</h2><p>{escape(message)}</p>'
        '<p><a href="/">回到门户</a></p></body>', status_code=400)


@router.get('/.well-known/openid-configuration', include_in_schema=False)
def well_known(request: Request):
    _enabled()
    return idp.discovery(_issuer(request))


@router.get('/api/oidc/authorize')
def authorize(request: Request, client_id: str = '', redirect_uri: str = '',
              response_type: str = '', scope: str = '', state: str = '', nonce: str = '',
              code_challenge: str = '', code_challenge_method: str = ''):
    _enabled()
    cli = idp.client(client_id)
    if cli is None:
        return _error_page(f'门户里没有登记 client_id={client_id!r} 这个系统。')
    if not idp.check_redirect(cli, redirect_uri):
        return _error_page(f'回调地址 {redirect_uri} 没有登记在「{cli["name"]}」名下。'
                           '请在门户里编辑这张卡片，把它加进「单点登录 · 回调地址」'
                           '（conf.json 里登记的客户端则加进 idp.clients 的 redirect_uris）。')

    def back(error: str, desc: str) -> RedirectResponse:
        return RedirectResponse(idp.redirect_with(redirect_uri, {
            'error': error, 'error_description': desc, 'state': state}), status_code=303)

    if response_type != 'code':
        return back('unsupported_response_type', '只支持授权码流程（response_type=code）')
    if 'openid' not in scope.split():
        return back('invalid_scope', 'scope 里要带 openid')
    method = code_challenge_method or ('plain' if code_challenge else '')
    if method not in ('', 'S256', 'plain'):
        return back('invalid_request', f'不支持的 code_challenge_method：{method}')

    user = auth.current_user(request)
    if user is None:
        # 没登录：先去门户登录页，登完由前端照着 continue 跳回这里（App.vue）。
        # no-referrer 是给经门户代理打开的系统准备的：这一跳带着代理页面的 Referer，
        # 落到 `/` 时会被 proxy.Fallback 转回那个系统身上
        here = request.url.path + ('?' + request.url.query if request.url.query else '')
        resp = RedirectResponse(f'/?continue={quote(here, safe="")}', status_code=303)
        resp.headers['referrer-policy'] = 'no-referrer'
        return resp

    code = idp.issue_code(cli, user, redirect_uri=redirect_uri, scope=scope, nonce=nonce,
                          challenge=code_challenge, method=method, issuer=_issuer(request))
    print(f'[单点登录] {user["username"]} 登录「{cli["name"]}」')
    return RedirectResponse(idp.redirect_with(redirect_uri, {'code': code, 'state': state}),
                            status_code=303)


def _token_error(exc: idp.IdpError) -> JSONResponse:
    status = 401 if exc.code == 'invalid_client' else 400
    return JSONResponse({'error': exc.code, 'error_description': str(exc)},
                        status_code=status, headers=_NO_STORE)


@router.post('/api/oidc/token')
async def token(request: Request):
    _enabled()
    # 表单自己解，不用 FastAPI 的 Form：那要多装一个 python-multipart，
    # 而这里只有 application/x-www-form-urlencoded 一种形状
    raw = (await request.body()).decode('utf-8', 'replace')
    form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    client_id = form.get('client_id', '')
    client_secret = form.get('client_secret', '')
    header = request.headers.get('authorization', '')
    if header[:6].lower() == 'basic ':
        # client_secret_basic：RFC 6749 2.3.1 要求两段各自先 form-urlencode 再拼，
        # 所以解完 base64 还要再 unquote 一次
        try:
            pair = base64.b64decode(header[6:].strip()).decode('utf-8')
            cid, _, secret = pair.partition(':')
            client_id, client_secret = unquote(cid.replace('+', ' ')), unquote(secret.replace('+', ' '))
        except Exception:
            return _token_error(idp.IdpError('invalid_client', 'Authorization 头解不开'))

    try:
        cli = idp.authenticate(client_id, client_secret)
        if form.get('grant_type') != 'authorization_code':
            raise idp.IdpError('unsupported_grant_type', '只支持 authorization_code')
        result = idp.redeem(cli, form.get('code', ''), form.get('redirect_uri', ''),
                            form.get('code_verifier', ''))
    except idp.IdpError as exc:
        return _token_error(exc)
    return JSONResponse(result, headers=_NO_STORE)


@router.api_route('/api/oidc/userinfo', methods=['GET', 'POST'])
def userinfo(request: Request):
    _enabled()
    header = request.headers.get('authorization', '')
    token_ = header[7:].strip() if header[:7].lower() == 'bearer ' else ''
    user = idp.user_from_access(token_)
    if user is None:
        return JSONResponse({'error': 'invalid_token'}, status_code=401,
                            headers={'www-authenticate': 'Bearer error="invalid_token"', **_NO_STORE})
    return JSONResponse({'sub': str(int(user['id'])), **idp.profile(user)}, headers=_NO_STORE)


@router.get('/api/oidc/jwks')
def jwks():
    _enabled()
    return {'keys': []}


# ---------------------------------------------------------------- 卡片编辑框

class CardClientIn(BaseModel):
    client_id: str
    name: str = ''
    redirect_uris: list[str] = []
    client_secret: str = ''          # 留空 = 沿用已存的那串


def _card_payload(request: Request, client: dict | None) -> dict:
    return {
        'idp_enabled': idp.settings()['enabled'],
        # 对方系统里那栏「门户地址」要填的就是它，编辑框里照着显示出来让人抄
        'issuer': _issuer(request),
        'client': client,
    }


@router.get('/api/idp/cards/{item_id}')
def get_card_client(item_id: str, request: Request, admin: dict = Depends(auth.require_admin)):
    return _card_payload(request, idp.card_client(int(admin['id']), item_id[:64]))


@router.put('/api/idp/cards/{item_id}')
def put_card_client(item_id: str, body: CardClientIn, request: Request,
                    admin: dict = Depends(auth.require_admin)):
    try:
        saved = idp.save_card_client(int(admin['id']), item_id[:64], client_id=body.client_id,
                                     name=body.name, redirect_uris=body.redirect_uris,
                                     client_secret=body.client_secret)
    except idp.IdpError as exc:
        raise HTTPException(400, str(exc)) from None
    print(f'[单点登录] {admin["username"]} 在卡片上登记了客户端 {saved["client_id"]}')
    return _card_payload(request, saved)


# ---------------------------------------------------------------- 设置（总开关 / issuer）

class SettingsIn(BaseModel):
    enabled: bool | None = None
    issuer: str | None = None        # 空串 = 按请求现拼


def _settings_payload(request: Request) -> dict:
    cfg = idp.settings()
    return {
        'enabled': cfg['enabled'],
        'issuer': cfg['issuer'],
        # 没填 issuer 时实际会用的那个，页面上给人看「现在等于什么」
        'effective_issuer': _issuer(request),
        'request_origin': _request_origin(request),
        'clients': idp.all_clients(),
    }


@router.get('/api/idp/settings')
def get_settings(request: Request, admin: dict = Depends(auth.require_admin)):
    return _settings_payload(request)


@router.put('/api/idp/settings')
def put_settings(body: SettingsIn, request: Request, admin: dict = Depends(auth.require_admin)):
    try:
        cfg = idp.save_settings(enabled=body.enabled, issuer=body.issuer)
    except idp.IdpError as exc:
        raise HTTPException(400, str(exc)) from None
    print(f'[单点登录] {admin["username"]} 把提供方设成 '
          f'{"开" if cfg["enabled"] else "关"}，issuer={cfg["issuer"] or "按请求现拼"}')
    return _settings_payload(request)
