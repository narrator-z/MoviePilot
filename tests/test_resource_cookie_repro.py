"""复现「点击插件页即登出」：验证资源 Cookie 的下发与接受链路。

核心怀疑：插件静态文件端点 `/plugin/file/{id}/{path}` 仅用 `verify_resource_token`
（只读资源 Cookie，不认 Bearer）。若资源 Cookie 未被服务端可靠下发，或该端点不接受
Bearer 兜底，则所有设备（含全新登录）在加载插件自定义前端时都会拿到 401，触发前端登出。
"""
import re

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.security import (
    create_access_token,
    verify_resource_token,
    verify_token,
)


def _make_request(cookies: dict | None = None, scheme: str = "https") -> Request:
    """构造最小测试请求，默认 https 以模拟反向代理后的真实访问。"""
    headers = [(b"host", b"nas.example.com")]
    if scheme == "https":
        headers.append((b"x-forwarded-proto", b"https"))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/plugin/statistic",
        "headers": headers,
        "scheme": scheme,
        "server": ("nas.example.com", 443),
        "client": ("127.0.0.1", 1234),
    }
    if cookies:
        # Starlette 的 cookie 解析依赖 scope 中的 headers；用 headers 注入更稳
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        scope["headers"].append((b"cookie", cookie_str.encode()))
    return Request(scope)


def _extract_resource_cookie(response: Response) -> str | None:
    """从响应 Set-Cookie 头里解析出资源令牌 Cookie 的值。"""
    sc = response.headers.get("set-cookie")
    if not sc:
        return None
    m = re.search(rf"{re.escape(settings.PROJECT_NAME)}=([^;]+)", sc)
    return m.group(1) if m else None


def test_verify_token_sets_resource_cookie_on_valid_bearer():
    """有效 Bearer 经 verify_token 后，服务端必须下发资源 Cookie。"""
    bearer = create_access_token(
        userid="1", username="admin", super_user=True, purpose="authentication"
    )
    req = _make_request()
    resp = Response()
    payload = verify_token(req, resp, jwt_token=bearer, api_key=None, api_token=None)
    assert payload is not None
    cookie = _extract_resource_cookie(resp)
    assert cookie is not None, "verify_token 未在有效 Bearer 后下发资源 Cookie"
    # Cookie 必须是合法 resource 令牌
    rt_payload = verify_resource_token(jwt_token=None, resource_token=cookie)
    assert rt_payload.purpose == "resource"


def test_verify_resource_token_accepts_bearer_as_fallback():
    """关键健壮性检查：verify_resource_token 必须接受 Bearer 兜底。

    插件静态文件端点此前只认资源 Cookie。Cookie 缺失（首次加载、跨上下文、旧镜像
    未下发）时所有设备都会 401 -> 前端登出。这是「点击插件页即退」的根因。
    """
    bearer = create_access_token(
        userid="1", username="admin", super_user=True, purpose="authentication"
    )
    # 仅带 Bearer、不带 Cookie 访问 verify_resource_token 保护的端点
    payload = verify_resource_token(jwt_token=bearer, resource_token=None)
    assert payload is not None
    assert payload.purpose == "authentication"


def test_verify_resource_token_cookie_still_primary():
    """资源 Cookie 仍作为首选凭据，且能正常解析。"""
    resource = create_access_token(
        userid="1", username="admin", super_user=True, purpose="resource"
    )
    payload = verify_resource_token(jwt_token=None, resource_token=resource)
    assert payload.purpose == "resource"


def test_verify_resource_token_rejects_when_both_missing():
    """Cookie 与 Bearer 均缺失时必须 401（不再误判为已登录）。"""
    with pytest.raises(Exception):
        verify_resource_token(jwt_token=None, resource_token=None)
