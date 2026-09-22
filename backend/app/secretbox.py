"""对称加密，只给一个地方用：Cookie 代理存进库里的那些上游 Cookie
（[cookiejar.py](cookiejar.py)）。

那些值等同于用户在外部站点的登录凭证。明文存的话，谁能读到这个库——**包括谁拿到
一份数据库备份**——谁就能冒充用户登进那些站点，而用户在门户里根本看不出来。

## 密钥从哪来

`db.session_secret()` 那把随机密钥 HKDF 派生出来的，不是配置项，也不单独存一份。
这样换一台机器部署时只要库跟着走，密钥就跟着走；conf.json 里也不会多出一串
「不知道是什么、但删了就全坏」的东西。

**反过来说：`portal_meta.session_secret` 那一行删了，所有存下来的 Cookie 就解不开了。**
解不开不是错误——`open_box` 返回 None，那枚 Cookie 当作没有，用户重新登一次上游站点
就是了。不会因此报错，也不会把整张卡片带崩。

## AAD 把密文钉在它该在的位置

加密时把 `<用户 id>:<卡片 id>:<Cookie 名>` 作为附加认证数据（AAD）。这样即使有人能
改库，也没法把 A 的那行密文抄到 B 名下——AAD 对不上，解密直接失败。
（AES-GCM 的 AAD 不进密文，只参与认证，所以不占存储。）

## cryptography 装不上时

整个模块退化成「不可用」（`available()` 返回 False），Cookie 代理那个功能自己关掉，
其余一切照旧。**不在 import 时抛异常**：为了一个可选功能让整个门户起不来不值当，
而且老部署升上来时多半还没装这个包。
"""
import hashlib
import hmac
import os
from typing import Optional

from . import db

_INFO = b'portal-cookie-jar-v1'
_SALT = b'portal-secretbox'
_VERSION = b'\x01'
_NONCE_LEN = 12          # AES-GCM 的标准长度，换别的值会让 96 位以外的路径变慢

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _AVAILABLE = True
except Exception:                                  # noqa: BLE001  没装就是没装
    AESGCM = None                                  # type: ignore[assignment]
    _AVAILABLE = False

_key: Optional[bytes] = None


def available() -> bool:
    return _AVAILABLE


def why_unavailable() -> str:
    return ('没装 cryptography，Cookie 代理这个功能会自动关掉。'
            '要用的话：pip install cryptography（或者重新跑一遍 '
            'pip install -r requirements.txt）')


def _hkdf(material: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256，stdlib 的 hmac 就够，不用再往 cryptography 里伸手。"""
    prk = hmac.new(_SALT, material, hashlib.sha256).digest()
    out, block, counter = b'', b'', 1
    while len(out) < length:
        block = hmac.new(prk, block + _INFO + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _aes() -> 'AESGCM':
    global _key
    if _key is None:
        # 第一次用到时才去库里取，不在 import 时取——那会儿 db.init() 还没跑过
        _key = _hkdf(db.session_secret())
    return AESGCM(_key)


def seal(plain: str, aad: bytes) -> bytes:
    """明文 → `版本(1) || nonce(12) || 密文+tag`。"""
    nonce = os.urandom(_NONCE_LEN)
    return _VERSION + nonce + _aes().encrypt(nonce, plain.encode('utf-8'), aad)


def open_box(blob: Optional[bytes], aad: bytes) -> Optional[str]:
    """解不开就返回 None，**不抛异常**。

    解不开的原因有好几种：换过签名密钥、库被人改过、密文被截断。
    它们对调用方是同一件事——这枚 Cookie 用不了，当作没有。
    """
    if not blob or not _AVAILABLE or len(blob) < 1 + _NONCE_LEN:
        return None
    if blob[:1] != _VERSION:
        return None
    try:
        raw = _aes().decrypt(blob[1:1 + _NONCE_LEN], blob[1 + _NONCE_LEN:], aad)
        return raw.decode('utf-8')
    except Exception:                              # noqa: BLE001  见上
        return None
