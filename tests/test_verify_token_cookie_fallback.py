"""
验证 verify_token 在缺少 Bearer/API Key/API Token 时，
回落到已下发的资源令牌 Cookie 完成鉴权（修复插件页因 SW 重发/同源重复请求
丢失 Bearer 头而被误判未登录、强制登出）。
"""
from datetime import timedelta

import jwt
import pytest

from app.core import security
from app.core.config import settings


class _FakeRequest:
    def __init__(self, cookies: dict, scheme: str = "https"):
        self.cookies = cookies
        self.url = type("U", (), {"scheme": scheme})()
        self.headers = type("H", (), {"get": lambda self, k, default=None: default})()


class _FakeResponse:
    def set_cookie(self, *args, **kwargs):
        pass


def _make_resource_token() -> str:
    payload = {
        "exp": security.datetime.datetime.now(security.datetime.UTC)
        + timedelta(minutes=30),
        "iat": security.datetime.datetime.now(security.datetime.UTC),
        "sub": "1",
        "username": "narratorz",
        "super_user": True,
        "level": 1,
        "purpose": "resource",
    }
    return jwt.encode(payload, settings.RESOURCE_SECRET_KEY, algorithm=security.ALGORITHM)


def test_verify_token_falls_back_to_resource_cookie():
    """无 Bearer，仅带资源令牌 Cookie -> 应成功返回 payload，不抛 401。"""
    token = _make_resource_token()
    req = _FakeRequest({settings.PROJECT_NAME: token})
    resp = _FakeResponse()
    payload = security.verify_token(req, resp, None, None, None)
    assert payload is not None
    assert payload.sub == 1
    assert payload.super_user is True


def test_verify_token_401_when_no_credential_at_all():
    """既无 Bearer 也无资源令牌 Cookie -> 仍应 401。"""
    req = _FakeRequest({})
    resp = _FakeResponse()
    with pytest.raises(Exception) as exc:
        security.verify_token(req, resp, None, None, None)
    assert exc.value.status_code == 401


def test_verify_token_bearer_still_works():
    """带 Bearer 时行为不变。"""
    access = security.create_access_token(
        userid=1,
        username="narratorz",
        super_user=True,
    )
    req = _FakeRequest({})
    resp = _FakeResponse()
    payload = security.verify_token(req, resp, access, None, None)
    assert payload.username == "narratorz"
