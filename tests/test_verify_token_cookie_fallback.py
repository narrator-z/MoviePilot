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


def test_verify_token_invalid_bearer_falls_back_to_cookie():
    """Bearer 失效（旧密钥签发）但资源令牌 Cookie 有效 -> 回落到 Cookie，不抛 403。

    这是修复『点击插件页即被踢』的关键：浏览器可能仍持有修复前旧密钥签发的
    无效 Bearer，此时不应直接 403，而应尝试同源下发的资源令牌 Cookie 兜底。
    """
    # 用错误密钥伪造一个『可解码但验签失败』的 Bearer
    bad_payload = {
        "exp": security.datetime.datetime.now(security.datetime.UTC) + timedelta(minutes=30),
        "iat": security.datetime.datetime.now(security.datetime.UTC),
        "sub": "1",
        "username": "narratorz",
        "super_user": True,
        "level": 1,
        "purpose": "authentication",
    }
    bad_bearer = jwt.encode(bad_payload, "this-is-a-wrong-secret-key", algorithm=security.ALGORITHM)

    # 同时携带一个有效的资源令牌 Cookie
    cookie = _make_resource_token()
    req = _FakeRequest({settings.PROJECT_NAME: cookie})
    resp = _FakeResponse()
    payload = security.verify_token(req, resp, bad_bearer, None, None)
    assert payload is not None
    assert payload.sub == 1


def test_verify_token_malformed_bearer_returns_401_not_500():
    """令牌可解码但载荷不符合 schema（旧版本签名）-> 返回干净的 401，而非 500。"""
    now = security.datetime.datetime.now(security.datetime.UTC)
    # 用正确密钥签名，但故意制造不符合 TokenPayload 的载荷（缺 purpose、sub 为 int）
    bad_payload = {
        "exp": now + timedelta(minutes=30),
        "iat": now,
        "sub": 1,
        "username": "narratorz",
        "super_user": True,
        "level": 1,
    }
    bad_bearer = jwt.encode(bad_payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)
    req = _FakeRequest({})
    resp = _FakeResponse()
    with pytest.raises(Exception) as exc:
        security.verify_token(req, resp, bad_bearer, None, None)
    assert exc.value.status_code == 401

