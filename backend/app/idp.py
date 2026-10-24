"""门户当 OIDC 提供方：导航里那些系统（DL 媒体库之类）来门户认人。

和 app/oidc.py 方向相反——那边门户是 RP，去外面的 IdP 认人；这边门户是 IdP，
别的系统是 RP。只做授权码流程这一条路（+ PKCE），不做 implicit / hybrid /
client_credentials / refresh_token：接进来的都是有后端的系统，用得上的只有这一条。

## 客户端从哪来

`conf.json` 的 `idp.clients`，和 oidc 那段一样只在启动时读。不做页面上的注册：
登记一个客户端等于把「以门户里任意一个人的身份登进那个系统」的钥匙交出去，
这件事该由能改服务器文件的人来做，而不是任何一个管理员账号点一下。

## 票据都长什么样

- **授权码**：随机串，存在进程内的字典里，60 秒、只能换一次。授权码必须一次性
  （OAuth 2.0 4.1.2），签名串做不到这一点，所以这里是全仓库唯一一处「服务端存票」。
  门户本来就是单进程（见 main.py 末尾），重启丢掉的只是那一分钟里还没换的码。
- **access_token**：`<用户 id>.<token_version>.<client_id>.<过期>.<HMAC>`，和会话 Cookie
  同一把钥匙、同一个路数，服务端不存。带着 token_version，所以门户里改口令 /
  停用 / 踢下线之后，userinfo 当场就不认它了。
- **id_token**：JWT，HS256，拿**那个客户端自己的 client_secret** 签（OIDC Core 10.1）。
  不上 RS256：那要一对密钥和 jwks 端点，还要把 `cryptography` 从「可选」变成「必需」。
  HS256 对每个客户端是独立的，A 系统拿着自己的 secret 也伪造不出发给 B 的 token。

## 认人认什么

`sub` 是门户的**用户 id**（转成字符串），不是用户名。门户里改了名，接进来的系统
按 sub 还能认出是同一个人；用户名放在 `preferred_username` 里，只给对方建号用。
"""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Optional
from urllib.parse import urlencode

from . import config, users
from .auth import _lookup, _secret

CODE_TTL = 60                # 授权码从发出到被换掉，正常是毫秒级的事
TOKEN_TTL = 3600             # access_token / id_token。对方拿完 userinfo 就建自己的会话了

_codes: dict[str, dict] = {}
_codes_lock = threading.Lock()


class IdpError(Exception):
    """给客户端看的错误。`code` 是 OAuth 规定的那几个错误码之一。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


# ---------------------------------------------------------------- 客户端

def client(client_id: str) -> Optional[dict]:
    return config.IDP_CLIENTS.get(client_id or '')


def check_redirect(cli: dict, redirect_uri: str) -> bool:
    """**逐字比对，不做前缀匹配。** 前缀匹配的话 `https://a/cb` 会放过
    `https://a/cb/../坏页面` 和 `https://a/cb.evil.com`，授权码就被送到别处了。"""
    return redirect_uri in cli['redirect_uris']


def authenticate(client_id: str, client_secret: str) -> dict:
    cli = client(client_id)
    # secret 用恒定时间比较；客户端不存在时也照比一次，别让响应时间说出「有没有这个 client_id」
    expect = cli['client_secret'] if cli else secrets.token_hex(16)
    ok = hmac.compare_digest(expect.encode('utf-8'), (client_secret or '').encode('utf-8'))
    if not (cli and ok):
        raise IdpError('invalid_client', 'client_id 或 client_secret 不对')
    return cli


# ---------------------------------------------------------------- 授权码

def _purge(now: float) -> None:
    for key in [k for k, v in _codes.items() if v['exp'] < now]:
        _codes.pop(key, None)


def issue_code(cli: dict, user: dict, *, redirect_uri: str, scope: str, nonce: str,
               challenge: str, method: str, issuer: str) -> str:
    code = secrets.token_urlsafe(32)
    now = time.time()
    with _codes_lock:
        _purge(now)
        _codes[code] = {
            'client_id': cli['client_id'], 'user_id': int(user['id']),
            'redirect_uri': redirect_uri, 'scope': scope, 'nonce': nonce,
            'challenge': challenge, 'method': method,
            # issuer 在这一刻定下来：换 token 的请求是对方后端发的，多半走内网地址，
            # 那时候按 Host 现拼出来的和浏览器看到的不是一个
            'issuer': issuer,
            'exp': now + CODE_TTL,
        }
    return code


def _pkce_ok(saved: dict, verifier: str) -> bool:
    challenge = saved.get('challenge') or ''
    if not challenge:
        return True                     # 授权时没带 PKCE，换的时候也就不查
    if not verifier:
        return False
    if saved.get('method') == 'S256':
        calc = _b64(hashlib.sha256(verifier.encode('ascii')).digest())
    else:
        calc = verifier
    return hmac.compare_digest(calc, challenge)


def redeem(cli: dict, code: str, redirect_uri: str, verifier: str) -> dict:
    """换掉授权码，返回 token 端点该回的整个响应。码不论成败都当场作废。"""
    with _codes_lock:
        saved = _codes.pop(code or '', None)
    if saved is None or saved['exp'] < time.time():
        raise IdpError('invalid_grant', '授权码无效、已用过或已过期')
    if saved['client_id'] != cli['client_id']:
        raise IdpError('invalid_grant', '授权码不是发给这个客户端的')
    if saved['redirect_uri'] != redirect_uri:
        raise IdpError('invalid_grant', 'redirect_uri 和授权时的不一致')
    if not _pkce_ok(saved, verifier):
        raise IdpError('invalid_grant', 'code_verifier 校验不过')

    user = _lookup(saved['user_id'])
    if user is None or not user['is_active']:
        raise IdpError('invalid_grant', '这个账号已经不存在或被停用了')

    now = int(time.time())
    claims = {
        'iss': saved['issuer'], 'sub': str(int(user['id'])), 'aud': cli['client_id'],
        'iat': now, 'exp': now + TOKEN_TTL, 'auth_time': now,
        **profile(user),
    }
    if saved['nonce']:
        claims['nonce'] = saved['nonce']
    return {
        'access_token': _access_token(user, cli['client_id'], now + TOKEN_TTL),
        'token_type': 'Bearer',
        'expires_in': TOKEN_TTL,
        'scope': saved['scope'],
        'id_token': _jwt(claims, cli['client_secret']),
    }


# ---------------------------------------------------------------- token

def _jwt(claims: dict, key: str) -> str:
    header = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    body = _b64(json.dumps(claims, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
    sig = hmac.new(key.encode('utf-8'), f'{header}.{body}'.encode('ascii'), hashlib.sha256).digest()
    return f'{header}.{body}.{_b64(sig)}'


def _sign(payload: str) -> str:
    # 前面加一截用途标记，别让 access_token 和会话 Cookie 的签名能互换着用
    return hmac.new(_secret(), b'idp-access:' + payload.encode('utf-8'), hashlib.sha256).hexdigest()


def _access_token(user: dict, client_id: str, expires: int) -> str:
    payload = f'{int(user["id"])}.{int(user["token_version"])}.{client_id}.{expires}'
    return f'{payload}.{_sign(payload)}'


def user_from_access(token: str) -> Optional[dict]:
    """userinfo 用。认就返回用户行，不认返回 None，不区分原因。"""
    head, _, sig = (token or '').rpartition('.')
    parts = head.split('.')
    if len(parts) != 4 or not sig:
        return None
    if not hmac.compare_digest(sig, _sign(head)):
        return None
    try:
        user_id, version, expires = int(parts[0]), int(parts[1]), int(parts[3])
    except ValueError:
        return None
    if expires < time.time() or client(parts[2]) is None:
        return None
    row = _lookup(user_id)
    if row is None or not row['is_active'] or int(row['token_version']) != version:
        return None
    return row


def profile(user: dict) -> dict:
    """发给客户端的用户资料。**口令哈希、token_version 这些绝不能进来。**"""
    pub = users.public(user)
    return {
        'preferred_username': pub['username'],
        'name': pub['display_name'],
        # 用 groups 这个名字是因为大多数 RP（含 app/oidc.py 自己）认管理员就看它
        'groups': ['admin'] if pub['is_admin'] else [],
    }


# ---------------------------------------------------------------- 发现文档

def discovery(issuer: str) -> dict:
    return {
        'issuer': issuer,
        'authorization_endpoint': f'{issuer}/api/oidc/authorize',
        'token_endpoint': f'{issuer}/api/oidc/token',
        'userinfo_endpoint': f'{issuer}/api/oidc/userinfo',
        # HS256 用不上公钥，但不少 RP 库要求这一项存在，给一个空集合
        'jwks_uri': f'{issuer}/api/oidc/jwks',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code'],
        'subject_types_supported': ['public'],
        'id_token_signing_alg_values_supported': ['HS256'],
        'scopes_supported': ['openid', 'profile'],
        'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
        'code_challenge_methods_supported': ['S256', 'plain'],
        'claims_supported': ['sub', 'iss', 'aud', 'exp', 'iat', 'nonce',
                             'preferred_username', 'name', 'groups'],
    }


def redirect_with(uri: str, params: dict) -> str:
    joiner = '&' if '?' in uri else '?'
    return f'{uri}{joiner}{urlencode({k: v for k, v in params.items() if v})}'


def describe() -> str:
    if not config.IDP_ENABLED:
        return '没开'
    names = '、'.join(c['name'] for c in config.IDP_CLIENTS.values()) or '一个客户端都没登记'
    return f'开着（{names}），issuer={config.IDP_ISSUER or "按请求现拼"}'
