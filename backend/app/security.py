"""口令哈希与随机票据。用标准库 scrypt，Windows 上不用编译 bcrypt 轮子。"""
import base64
import hashlib
import hmac
import secrets

_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f'scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}'


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, expect = stored.split('$')
        if algo != 'scrypt':
            return False
        dk = hashlib.scrypt(
            password.encode('utf-8'), salt=_unb64(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(_unb64(expect)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, _unb64(expect))


def new_token(nbytes: int = 32) -> str:
    """会话令牌 / 票据 / client_secret 都用它，128 位以上熵。"""
    return secrets.token_urlsafe(nbytes)


def token_fingerprint(token: str) -> str:
    """入库只存指纹：库被看到也换不回可用的 Cookie。"""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def secret_equal(a: str, b: str) -> bool:
    return hmac.compare_digest((a or '').encode('utf-8'), (b or '').encode('utf-8'))
