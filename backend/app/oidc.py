"""OIDC 单点登录：门户做 RP（依赖方），认人这件事交给外面的 IdP。

支持任何讲标准 OIDC 的身份提供方——Keycloak、Authentik、Casdoor、Logto、
Okta、Auth0，以及 Google 这类。配置只要 issuer + client_id + client_secret，
授权地址、token 地址、userinfo 地址全从 `<issuer>/.well-known/openid-configuration`
现取（取回来缓存一小时）。

## 为什么不引 authlib / python-jose

只用得上授权码流程这一条路，而那两个包带进来的是一整套 JOSE 实现和
`cryptography` 这个二进制扩展。门户现在的依赖全是纯 Python，PyInstaller
靠静态分析就能打包；加一个带 C 扩展、还按字符串名字动态选算法后端的包，
换来的是「源码跑得好好的，exe 里一登录就崩」这类只在打包后才出现的问题。

## 那 id_token 的签名谁验

**不验，靠 TLS。** OIDC Core 3.1.3.7 第 6 条写明了：id_token 是客户端自己
带着 client_secret 直接从 token 端点、经 TLS 取回来的时候，可以不验签名——
中间没有第三方经手，签名要防的那个人根本不在这条路上。（会被绕开的是
implicit / hybrid 那几种「让浏览器捎回来」的流程，这里用的是授权码流程。）

不验签名，但下面这几项一条都不能少，它们防的是别的东西：

  - `iss` 必须等于配置里那个 issuer —— 防的是把门户指到一个假 IdP 上；
  - `aud` 必须含 client_id —— 防的是拿别的客户端的 token 来换门户的会话；
  - `exp` 必须没过；
  - `nonce` 必须等于我们这次发出去的那个 —— 防重放。

## state / nonce / PKCE 存哪

存在一枚**签名过的临时 Cookie**里（`portal_oidc`，10 分钟就过期），服务端
什么都不存。和会话 Cookie 一个路数，理由也一样：不用维护一张随时间长胖、
还得定期清理的表，多实例部署时也不用让两台机器共享内存。
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from . import config
from .auth import _secret

PROVIDER = 'oidc'            # user_identities.provider 里存的值
STATE_COOKIE = 'portal_oidc'
STATE_TTL = 600              # 从点「单点登录」到 IdP 跳回来，10 分钟够了

_DISCOVERY_TTL = 3600
_discovery: Optional[dict] = None
_discovery_at = 0.0


class OidcError(Exception):
    """这一路上任何一步不对。消息是给人看的，会原样显示在登录页上。"""


# ---------------------------------------------------------------- 发现文档

def discovery() -> dict:
    """`<issuer>/.well-known/openid-configuration`，缓存一小时。"""
    global _discovery, _discovery_at
    now = time.monotonic()
    if _discovery is not None and now - _discovery_at < _DISCOVERY_TTL:
        return _discovery
    url = f'{config.OIDC_ISSUER}/.well-known/openid-configuration'
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        doc = resp.json()
    except Exception as exc:
        raise OidcError(f'取不到 IdP 的发现文档（{url}）：{exc}') from None
    if not isinstance(doc, dict) or not doc.get('authorization_endpoint'):
        raise OidcError(f'{url} 返回的不是一份 OIDC 发现文档')
    # issuer 对不上 = 配置里那个地址指错了地方，后面每一步都会莫名其妙
    if str(doc.get('issuer', '')).rstrip('/') != config.OIDC_ISSUER:
        raise OidcError(f'发现文档里的 issuer 是 {doc.get("issuer")!r}，'
                        f'和 conf.json 里配的 {config.OIDC_ISSUER!r} 对不上')
    _discovery, _discovery_at = doc, now
    return doc


# ---------------------------------------------------------------- 临时状态

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def pack_state(payload: dict) -> str:
    """把 state / nonce / code_verifier / 登录后去哪儿签成一个串，塞进临时 Cookie。"""
    body = _b64(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    return f'{body}.{hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()}'


def unpack_state(raw: str) -> dict:
    body, _, sig = (raw or '').partition('.')
    if not body or not sig:
        raise OidcError('登录状态丢了，请重新点一次单点登录')
    expect = hmac.new(_secret(), body.encode('ascii'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        raise OidcError('登录状态校验不过，请重新点一次单点登录')
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        raise OidcError('登录状态读不出来，请重新点一次单点登录') from None
    if not isinstance(payload, dict) or payload.get('exp', 0) < time.time():
        raise OidcError('登录状态已经过期，请重新点一次单点登录')
    return payload


# ---------------------------------------------------------------- 授权

def begin(redirect_uri: str, next_url: str = '/') -> tuple[str, str]:
    """返回 (要跳过去的授权地址, 要种进临时 Cookie 的状态串)。"""
    doc = discovery()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode('ascii')).digest())

    params = {
        'response_type': 'code',
        'client_id': config.OIDC_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'scope': config.OIDC_SCOPES,
        'state': state,
        'nonce': nonce,
        # PKCE。授权码流程带着 client_secret 本来就够了，但 PKCE 多挡一道
        # 「授权码在跳转链路上被人截走」——日志、Referer、浏览器历史都可能把它漏出去
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    url = f'{doc["authorization_endpoint"]}?{urlencode(params)}'
    packed = pack_state({
        'state': state, 'nonce': nonce, 'verifier': verifier,
        'redirect_uri': redirect_uri, 'next': next_url,
        'exp': int(time.time()) + STATE_TTL,
    })
    return url, packed


def exchange(code: str, saved: dict) -> dict:
    """拿授权码换 token。返回 token 端点的整个响应。"""
    doc = discovery()
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': saved['redirect_uri'],
        'client_id': config.OIDC_CLIENT_ID,
        'code_verifier': saved['verifier'],
    }
    # client_secret_post 和 client_secret_basic 两种都发得出去，按 IdP 声明的来。
    # 声明里没写就用 post：绝大多数 IdP 都收，而 basic 那种要求 client_id
    # 和 secret 做一次 form-urlencode，写错的人不少
    auth = None
    methods = doc.get('token_endpoint_auth_methods_supported') or []
    if config.OIDC_CLIENT_SECRET:
        if 'client_secret_basic' in methods and 'client_secret_post' not in methods:
            auth = (config.OIDC_CLIENT_ID, config.OIDC_CLIENT_SECRET)
        else:
            data['client_secret'] = config.OIDC_CLIENT_SECRET

    try:
        resp = httpx.post(doc['token_endpoint'], data=data, auth=auth, timeout=15,
                          headers={'accept': 'application/json'})
    except Exception as exc:
        raise OidcError(f'连不上 IdP 的 token 端点：{exc}') from None
    if resp.status_code != 200:
        # IdP 的报错原样带出来，这一步配错的概率最高（redirect_uri 没登记、
        # client_secret 填错、授权码过期都会落在这儿）
        raise OidcError(f'换 token 被 IdP 拒绝（{resp.status_code}）：{resp.text[:300]}')
    try:
        return resp.json()
    except Exception:
        raise OidcError('IdP 的 token 端点返回的不是 JSON') from None


def _payload(id_token: str) -> dict:
    parts = (id_token or '').split('.')
    if len(parts) != 3:
        raise OidcError('id_token 的形状不对')
    try:
        payload = json.loads(_unb64(parts[1]))
    except Exception:
        raise OidcError('id_token 解不开') from None
    if not isinstance(payload, dict):
        raise OidcError('id_token 里不是一个对象')
    return payload


def claims(tokens: dict, saved: dict) -> dict:
    """校验 id_token 里那几项，再补上 userinfo，返回合并后的 claims。

    userinfo 是「补」不是「换」：id_token 里的 sub 才是认人的依据，
    userinfo 里要是回了一个不一样的 sub，说明这条路上有东西不对，直接拒。
    """
    id_token = tokens.get('id_token')
    if not id_token:
        raise OidcError('IdP 没返回 id_token，确认 scope 里带了 openid')
    payload = _payload(id_token)

    if str(payload.get('iss', '')).rstrip('/') != config.OIDC_ISSUER:
        raise OidcError('id_token 的 iss 和配置里的 issuer 对不上')
    aud = payload.get('aud')
    aud_list = aud if isinstance(aud, list) else [aud]
    if config.OIDC_CLIENT_ID not in aud_list:
        raise OidcError('id_token 不是发给这个 client_id 的')
    if int(payload.get('exp') or 0) < time.time():
        raise OidcError('id_token 已经过期了')
    if payload.get('nonce') != saved.get('nonce'):
        raise OidcError('nonce 对不上，这次登录被丢弃')
    if not payload.get('sub'):
        raise OidcError('id_token 里没有 sub')

    merged = dict(payload)
    endpoint = discovery().get('userinfo_endpoint')
    access = tokens.get('access_token')
    if endpoint and access:
        # 很多 IdP 把 email / groups 放在 userinfo 而不是 id_token 里。
        # 取不到不算错——认人靠 sub，那一项 id_token 里已经有了
        try:
            resp = httpx.get(endpoint, timeout=10,
                             headers={'authorization': f'Bearer {access}',
                                      'accept': 'application/json'})
            if resp.status_code == 200:
                info = resp.json()
                if isinstance(info, dict):
                    if info.get('sub') and info['sub'] != payload['sub']:
                        raise OidcError('userinfo 的 sub 和 id_token 的对不上')
                    merged.update({k: v for k, v in info.items() if k != 'sub'})
        except OidcError:
            raise
        except Exception as exc:
            print(f'[OIDC] userinfo 取不到，只用 id_token 里的 claims: {exc}')
    return merged


# ---------------------------------------------------------------- claims → 用户

def pick_username(claims_: dict) -> str:
    """挑一个当门户用户名。取不到配置里那个 claim 就退回 email，再退回 sub。

    只在**第一次**给这个 sub 建号时用得上；建完之后认人一律靠 sub，
    IdP 那边改名不会把人变成另一个账号。
    """
    for key in (config.OIDC_USERNAME_CLAIM, 'preferred_username', 'email', 'sub'):
        value = str(claims_.get(key) or '').strip()
        if value:
            # 邮箱当用户名时把 @ 后面那截留着，同名不同域的人不会撞
            return value[:64]
    return ''


def is_admin(claims_: dict) -> Optional[bool]:
    """按 claim 判断是不是管理员。没配 admin_claim_value 就返回 None = 不看。

    返回 None 和返回 False 是两回事：None 表示「这件事门户自己说了算」，
    不会把管理员在 IdP 里降级；False 表示「IdP 说他不是」。
    """
    if not (config.OIDC_ADMIN_CLAIM and config.OIDC_ADMIN_CLAIM_VALUE):
        return None
    value: Any = claims_.get(config.OIDC_ADMIN_CLAIM)
    if value is None:
        return False
    if isinstance(value, str):
        value = [v.strip() for v in value.replace(',', ' ').split()]
    if isinstance(value, (list, tuple, set)):
        return config.OIDC_ADMIN_CLAIM_VALUE in {str(v) for v in value}
    return str(value) == config.OIDC_ADMIN_CLAIM_VALUE


def describe() -> str:
    """给启动日志用的一句话。"""
    if not config.OIDC_ENABLED:
        return '没开（登录页只有本地账号那一条路）'
    return f'{config.OIDC_ISSUER}，client_id={config.OIDC_CLIENT_ID}'
