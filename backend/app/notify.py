"""单点登出：门户退出时，挨个通知这次会话登过的业务系统把本地会话也销毁。

通知是「尽力而为」的：某个系统挂了不影响门户退出，它那边的本地会话
最多活到自己设的过期时间。
"""
import asyncio

import httpx

from . import clients, db
from .config import LOGOUT_NOTIFY_TIMEOUT
from .security import new_token


async def _post_one(client: httpx.AsyncClient, cfg: dict, sid: str) -> None:
    try:
        await client.post(
            cfg['logout_uri'],
            json={'sid': sid, 'client_id': cfg['client_id'], 'secret': cfg['secret'],
                  'nonce': new_token(8)},
        )
    except httpx.HTTPError as exc:
        print(f"[logout] 通知 {cfg['client_id']} 失败: {exc}")


async def broadcast_logout(sid: str) -> None:
    if not sid:
        return
    targets = [cfg for cid in db.clients_of_session(sid)
               if (cfg := clients.get(cid)) and cfg['logout_uri']]
    if not targets:
        return
    async with httpx.AsyncClient(timeout=LOGOUT_NOTIFY_TIMEOUT) as http:
        await asyncio.gather(*(_post_one(http, cfg, sid) for cfg in targets))
