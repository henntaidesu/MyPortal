"""管理端接口。目前只有运行期配置，全部要管理员身份。

配置值本身的合法性由 settings.validate 把关，这里只负责鉴权和把错误翻译成 400。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import settings
from ..deps import require_admin

router = APIRouter(prefix='/api/settings', tags=['settings'])


class SettingIn(BaseModel):
    value: str = Field(max_length=255)


@router.get('')
def list_settings(request: Request):
    require_admin(request)
    return {'items': settings.all_items()}


@router.put('/{name}')
def update_setting(name: str, body: SettingIn, request: Request):
    require_admin(request)
    try:
        value = settings.put(name, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {'name': name, 'value': value}
