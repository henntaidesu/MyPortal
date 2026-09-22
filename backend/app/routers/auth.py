"""登录、登出、我是谁，以及 OIDC 单点登录那两条跳转。

两条登录路：

  - 本地账号：用户名 + 口令，口令哈希存在 users 表里（app/users.py）；
  - OIDC：跳到 IdP，回来时按 `sub` 认人（app/oidc.py）。

两条路都关着的话服务在启动时就停了（见 app/config.py 末尾），所以这里不用管
「一个登录方式都没有」这种情况。
"""
from typing import Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .. import auth, config, oidc, users

router = APIRouter(prefix='/api', tags=['auth'])


class LoginIn(BaseModel):
    username: str = ''
    password: str = ''


def _public(user: Optional[dict]) -> dict:
    return {'user': users.public(user)}


@router.get('/me')
def me(request: Request):
    """没登录也返回 200（user 为 null）。

    不用 401：页面一起来就要问一次，401 会触发前端那条「会话过期」的通路，
    把「本来就还没登录」也报成掉线。
    """
    return _public(auth.current_user(request))


@router.get('/auth/providers')
def providers():
    """登录页拿它决定画哪几个按钮。**不要登录**——没登录的人才需要它。

    这里只说「有哪几条路」，不带 client_secret 之类的东西。
    """
    return {
        'local': config.ALLOW_LOCAL_LOGIN,
        'oidc': {'enabled': config.OIDC_ENABLED, 'label': '单点登录'},
    }


@router.post('/login')
def login(body: LoginIn, request: Request, response: Response):
    if not config.ALLOW_LOCAL_LOGIN:
        # 配置里把本地登录关了。前端本来就不画这个表单，走到这儿的是绕过页面直接打接口的
        raise HTTPException(403, '本地账号登录已经关闭，请走单点登录')

    key = auth.client_ip(request)
    wait = auth.login_blocked(key)
    if wait:
        raise HTTPException(429, f'失败次数太多，请 {wait} 秒后再试')

    user = auth.check_credentials(body.username, body.password)
    if user is None:
        auth.note_fail(key)
        # 不区分「用户名不对」「口令不对」「账号被停用」：分开说等于告诉对方蒙对了哪一半
        raise HTTPException(401, '用户名或密码不对')

    auth.clear_fails(key)
    auth.set_cookie(response, auth.make_token(user))
    return _public(user)


@router.post('/logout')
def logout(response: Response):
    """只把 Cookie 删掉，别的设备不受影响。

    要把自己所有设备都踢掉，用 /api/account/sessions（那条会 token_version +1）。
    """
    auth.clear_cookie(response)
    return {'ok': True}


# ---------------------------------------------------------------- OIDC

def _redirect_uri(request: Request) -> str:
    """回调地址。配置里写死了就用配置里那个。

    没写死就按这次请求的 Host 现拼。**优先用配置**：Host 和 X-Forwarded-Proto
    都是调用方能伪造的，而且门户挂在反代后面时算出来的多半和 IdP 那边登记的
    不一样——地址对不上 IdP 会直接拒绝授权请求，表现成「点了单点登录跳过去就报错」。
    """
    if config.OIDC_REDIRECT_URL:
        return config.OIDC_REDIRECT_URL
    proto = request.headers.get('x-forwarded-proto') or request.url.scheme
    host = request.headers.get('x-forwarded-host') or request.headers.get('host') or ''
    return f'{proto}://{host}/api/auth/oidc/callback'


def _safe_next(raw: str) -> str:
    """登录后去哪儿。**只认站内的根绝对路径。**

    不拦的话这就是一个开放重定向：`/api/auth/oidc/login?next=https://坏站`
    会让门户把刚登录的人送到别人那儿去，而地址栏上一跳还是门户，看着很可信。
    `//坏站` 也要挡——那是协议相对地址，浏览器当跨站处理。
    """
    value = raw or '/'
    if not value.startswith('/') or value.startswith('//'):
        return '/'
    if urlsplit(value).netloc:
        return '/'
    return value


def _fail(message: str) -> RedirectResponse:
    """出错时回登录页，把原因带在查询串里让页面显示出来。

    不回 JSON：这条路是浏览器的顶层跳转，回一段 JSON 的话用户看到的是
    满屏大括号，而他只是点了个「单点登录」。
    """
    return RedirectResponse(f'/?oidc_error={quote(message)}', status_code=303)


@router.get('/auth/oidc/login')
def oidc_login(request: Request, next: str = '/'):
    if not config.OIDC_ENABLED:
        raise HTTPException(404, '没有配置单点登录')
    try:
        url, packed = oidc.begin(_redirect_uri(request), _safe_next(next))
    except oidc.OidcError as exc:
        return _fail(str(exc))

    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(
        oidc.STATE_COOKIE, packed,
        max_age=oidc.STATE_TTL,
        httponly=True,
        secure=config.COOKIE_SECURE,
        # 必须是 lax 不能是 strict：从 IdP 跳回来那一下是跨站发起的顶层导航，
        # strict 会让浏览器不带这枚 Cookie，表现成「每次回来都说登录状态丢了」
        samesite='lax',
        path='/api/auth/oidc',
    )
    return resp


@router.get('/auth/oidc/callback')
def oidc_callback(request: Request, code: str = '', state: str = '',
                  error: str = '', error_description: str = ''):
    if not config.OIDC_ENABLED:
        raise HTTPException(404, '没有配置单点登录')
    if error:
        return _fail(f'{error}: {error_description}'.strip(': '))
    if not code:
        return _fail('IdP 没有返回授权码')

    try:
        saved = oidc.unpack_state(request.cookies.get(oidc.STATE_COOKIE, ''))
        if not state or state != saved.get('state'):
            # state 对不上 = 这次回调不是我们发出去的那一次（CSRF，或者用户
            # 在两个标签页里各点了一次，回来时串了）
            raise oidc.OidcError('state 对不上，这次登录被丢弃')
        tokens = oidc.exchange(code, saved)
        claims = oidc.claims(tokens, saved)
        user = _resolve(claims)
    except oidc.OidcError as exc:
        return _fail(str(exc))
    except ValueError as exc:
        return _fail(str(exc))

    resp = RedirectResponse(_safe_next(saved.get('next', '/')), status_code=303)
    auth.set_cookie(resp, auth.make_token(user))
    resp.delete_cookie(oidc.STATE_COOKIE, path='/api/auth/oidc')
    return resp


def _resolve(claims: dict) -> dict:
    """claims → 门户里的那个用户。找不到就按配置决定建号还是拒绝。

    认人**只认 sub**。email 和用户名在 IdP 里都是能改的，跟着它们走会让
    改过名的人下次登录变成另一个账号，导航也就跟着丢了。
    """
    subject = str(claims.get('sub'))
    user = users.by_identity(oidc.PROVIDER, subject)

    if user is None:
        username = oidc.pick_username(claims)
        if not username:
            raise oidc.OidcError('IdP 没给出可以当用户名的 claim')
        try:
            username = users.normalize_username(username)
        except ValueError as exc:
            raise oidc.OidcError(f'IdP 给的用户名 {username!r} 门户收不了：{exc}') from None

        existing = users.by_username(username)
        if existing is not None:
            # 同名的本地账号：认领它，以后这个人两条路都能登。
            # **这一步信任的是「管理员配了这个 IdP」**——换个 IdP 就等于换一批人，
            # 所以 conf.json 里那几项只有能改文件的人才动得了
            user = existing
        elif config.OIDC_AUTO_CREATE:
            # password=None 而不是空串：这个人没有本地口令，本地登录那条路对他关着
            user = users.create(username, None, role='user',
                                display_name=str(claims.get('name') or username))
            print(f'[OIDC] 新用户 {username}（sub={subject}）自动建号')
        else:
            raise oidc.OidcError(
                f'IdP 认了你（{username}），但门户里还没有这个账号，'
                '而且 oidc.auto_create_user 是关的。请让管理员先建一个同名账号')

        users.link_identity(user['id'], oidc.PROVIDER, subject,
                            str(claims.get('email') or ''))

    if not user['is_active']:
        raise oidc.OidcError('这个账号已经被停用了')

    # IdP 说了算的管理员身份。admin_claim_value 没配就返回 None = 不看，
    # 谁是管理员完全由门户自己的用户表说了算
    admin = oidc.is_admin(claims)
    want = 'admin' if admin else 'user'
    if admin is not None and user['role'] != want:
        try:
            user = users.update(int(user['id']), role=want)
        except ValueError as exc:
            # 「最后一个管理员不能降级」会走到这儿。登录本身不该因此失败
            print(f'[OIDC] 按 claim 调整 {user["username"]} 的角色失败: {exc}')
        auth.forget(int(user['id']))

    return user
