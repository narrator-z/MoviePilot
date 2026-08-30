"""
用户认证的优雅降级与降噪测试。

聚焦两类问题（见主理人调查）：
1. `app/scheduler.Scheduler.user_auth` 在未认证时每 10 分钟以 error 级别刷屏；
2. `app.startup.modules_initializer.check_auth` 启动告警文案夸大、暗示全盘瘫痪。

所有外部依赖（SitesHelper、SystemConfigOper、SchedulerChain、PluginManager、logger）
均在边界处 mock，绝不触达真实站点 / 网络 / 数据库。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.scheduler import Scheduler


class _FakeLogger:
    """记录日志调用级别与最终消息，用于断言降级后的日志级别（不触达真实日志系统）。"""

    def __init__(self):
        self.calls = []

    def _record(self, level: str, msg: str, args) -> None:
        text = msg % args if args else msg
        self.calls.append((level, text))

    def info(self, msg: str, *args, **kwargs) -> None:
        self._record("info", msg, args)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._record("error", msg, args)

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._record("debug", msg, args)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._record("warning", msg, args)


def _fake_sites_class(auth_level: int = 1, check_user_result: tuple = (False, "认证失败")):
    """
    构造一个与 SitesHelper 接口一致的假类，避免触发真实资源包拉取 / 网络。
    """

    class _Sites:
        def __init__(self):
            self.auth_level = auth_level

        def check_user(self, **kwargs):
            return check_user_result

    return _Sites


def _build_auth_scheduler(auth_count: int = 0, auth_message: bool = False) -> Scheduler:
    """
    构造不触发 `Scheduler.init()` 的测试对象，仅携带 user_auth 所需的计数器。

    `Scheduler` 是单例且 `__init__` 会启动 APScheduler / 连接资源，单元测试中
    通过 `object.__new__` 绕过构造，仅填充 user_auth 用到的实例属性。
    """
    scheduler = object.__new__(Scheduler)
    scheduler._auth_count = auth_count
    scheduler._auth_message = auth_message
    return scheduler


@pytest.fixture
def patched_user_auth(monkeypatch):
    """
    mock 掉 user_auth 内所有会触达真实站点 / 网络 / 插件的依赖，并返回捕获句柄。
    """
    captured = {
        "messagehelper_put": [],
        "post_message": [],
    }
    fake_messagehelper = SimpleNamespace(
        put=lambda **kwargs: captured["messagehelper_put"].append(kwargs)
    )
    fake_chain = SimpleNamespace(
        messagehelper=fake_messagehelper,
        post_message=lambda notification: captured["post_message"].append(notification),
    )
    fake_logger = _FakeLogger()
    # 默认给出一个「未认证且认证失败」的站点助手，模拟常态
    monkeypatch.setattr("app.scheduler.SitesHelper", _fake_sites_class())
    monkeypatch.setattr(
        "app.scheduler.SystemConfigOper",
        lambda: SimpleNamespace(get=lambda _key: None),
    )
    monkeypatch.setattr("app.scheduler.SchedulerChain", lambda: fake_chain)
    monkeypatch.setattr(
        "app.scheduler.PluginManager",
        lambda: SimpleNamespace(init_config=lambda: None),
    )
    monkeypatch.setattr(Scheduler, "init_plugin_jobs", lambda self: None)
    monkeypatch.setattr("app.scheduler.register_plugin_api", lambda: None)
    monkeypatch.setattr("app.scheduler.logger", fake_logger)
    return {"captured": captured, "logger": fake_logger}


def _info_texts(handle) -> list:
    return [text for level, text in handle["logger"].calls if level == "info"]


def _error_texts(handle) -> list:
    return [text for level, text in handle["logger"].calls if level == "error"]


def test_user_auth_failure_logs_info_not_error(monkeypatch, patched_user_auth):
    """认证失败时不应刷 error 日志，仅以 info 记录并累计失败次数（优雅降级）。"""
    monkeypatch.setattr(
        "app.scheduler.SitesHelper",
        _fake_sites_class(auth_level=1, check_user_result=(False, "cookie 已过期")),
    )
    scheduler = _build_auth_scheduler(auth_count=0, auth_message=False)

    scheduler.user_auth()

    # 仍应累计失败次数
    assert scheduler._auth_count == 1
    # 不应出现任何 error 级别记录（避免刷屏告警）
    assert _error_texts(patched_user_auth) == []
    # 应有一条 info 级别的失败记录，且包含累计次数
    assert any("用户认证失败" in t and "共失败 1 次" in t for t in _info_texts(patched_user_auth))


def test_user_auth_at_max_retry_logs_error_once(monkeypatch, patched_user_auth):
    """达到最大重试次数（30）时，升级为 error 并提示不再尝试认证。"""
    monkeypatch.setattr(
        "app.scheduler.SitesHelper",
        _fake_sites_class(auth_level=1, check_user_result=(False, "cookie 已过期")),
    )
    scheduler = _build_auth_scheduler(auth_count=29, auth_message=False)

    scheduler.user_auth()

    assert scheduler._auth_count == 30
    # 仅在达到上限时出现一条 error 级「放弃认证」提示
    assert any(
        "用户认证失败次数过多，将不再尝试认证！" in t
        for t in _error_texts(patched_user_auth)
    )
    # 同时仍保留一条 info 级失败记录（含累计次数）
    assert any(
        "用户认证失败" in t and "共失败 30 次" in t
        for t in _info_texts(patched_user_auth)
    )


def test_user_auth_success_resets_and_reinit(monkeypatch, patched_user_auth):
    """认证成功时归零失败计数、重新初始化插件并推送成功通知。"""
    monkeypatch.setattr(
        "app.scheduler.SitesHelper",
        _fake_sites_class(auth_level=1, check_user_result=(True, "站点A")),
    )
    scheduler = _build_auth_scheduler(auth_count=5, auth_message=False)

    scheduler.user_auth()

    assert scheduler._auth_count == 0
    # 成功路径不应产生 error
    assert _error_texts(patched_user_auth) == []
    # 应推送认证成功通知
    assert patched_user_auth["captured"]["post_message"], "应推送认证成功通知"
    # 认证成功通知的「用户认证成功」写在 Notification.title（text 为「使用站点：...」），
    # 断言应针对 title 而非 text。
    assert "用户认证成功" in patched_user_auth["captured"]["post_message"][0].title


def test_user_auth_exceeded_max_retry_only_notifies_once(monkeypatch, patched_user_auth):
    """超过最大重试次数后，仅推送一次通知并停止尝试，不再刷 error。"""
    monkeypatch.setattr(
        "app.scheduler.SitesHelper",
        _fake_sites_class(auth_level=1, check_user_result=(False, "x")),
    )
    scheduler = _build_auth_scheduler(auth_count=31, auth_message=False)

    scheduler.user_auth()

    assert scheduler._auth_message is True
    assert patched_user_auth["captured"]["messagehelper_put"], "应推送一次放弃通知"
    assert "用户认证失败次数过多，将不再尝试认证！" in \
        patched_user_auth["captured"]["messagehelper_put"][0]["message"]
    # 超过上限的后续运行不应再刷 error 日志
    assert _error_texts(patched_user_auth) == []


def test_check_auth_message_is_non_alarming(monkeypatch):
    """全开后不再告警：站点功能已开放，check_auth() 在 auth_level=1 时不再推送任何「功能受限」类通知。"""
    from app.startup.initializers import modules as modules_initializer

    captured = {"put": [], "post_message": []}
    monkeypatch.setattr(
        modules_initializer,
        "SitesHelper",
        lambda: SimpleNamespace(auth_level=1),
    )
    monkeypatch.setattr(
        modules_initializer,
        "MessageHelper",
        lambda: SimpleNamespace(
            put=lambda content, **kw: captured["put"].append((content, kw))
        ),
    )
    monkeypatch.setattr(
        modules_initializer,
        "CommandChain",
        lambda: SimpleNamespace(
            post_message=lambda notification: captured["post_message"].append(notification)
        ),
    )

    modules_initializer.check_auth()

    # 全开后 check_auth 已成为无害空操作，不再推送任何「功能受限」类告警
    assert captured["put"] == [], "全开后不应再推送受限告警"
    assert captured["post_message"] == [], "全开后不应再推送受限通知"


def test_add_site_not_blocked_without_auth(monkeypatch):
    """站点认证闸门全开：即使站点未认证(auth_level=1)，新增站点也应走到解析/保存逻辑，而非被认证拦截。"""
    from app import schemas
    from app.api.endpoints import site as site_endpoint

    class _FakeSitesHelper:
        """站点助手桩：auth_level=1（未通过认证），async_get_indexer 返回可解析的站点信息。"""
        auth_level = 1

        def async_get_indexer(self, domain: str):
            async def _coro():
                return {"name": "TestSite", "public": False}
            return _coro()

    # 站点未认证，但新增站点不应再被「用户未通过认证」拦截
    monkeypatch.setattr(site_endpoint, "SitesHelper", lambda: _FakeSitesHelper())
    # 域名不重复，可继续保存
    async def _fake_async_get_by_domain(db, domain):
        return None
    monkeypatch.setattr(site_endpoint.Site, "async_get_by_domain", _fake_async_get_by_domain)
    # 保存站点为无副作用的空操作
    monkeypatch.setattr(site_endpoint.Site, "create", lambda self, db: None)
    # 站点更新事件为无副作用的空操作
    async def _noop_send_event(*args, **kwargs):
        return None
    monkeypatch.setattr(site_endpoint.eventmanager, "async_send_event", _noop_send_event)

    site_in = schemas.Site(url="https://example.com")
    fake_user = SimpleNamespace(id=1, is_superuser=True, is_active=True, name="tester")
    resp = asyncio.run(site_endpoint.add_site(db=object(), site_in=site_in, _=fake_user))

    # 不应再返回「用户未通过认证」，而应通过解析/保存逻辑到达成功分支
    assert resp.success is True
    assert "未通过认证" not in (resp.message or "")


def test_plugin_auth_level_gate_open(monkeypatch):
    """插件认证闸门全开：即使站点未认证(auth_level=1)，声明 auth_level=99 的插件也应通过校验。"""
    from app.core import plugin as plugin_module
    from app.core.plugin import PluginManager

    # 即使站点认证级别为 1（未通过认证），插件闸门也应放开
    monkeypatch.setattr(
        plugin_module,
        "SitesHelper",
        lambda: SimpleNamespace(auth_level=1),
    )

    class _FakePlugin:
        auth_level = 99

    fake_plugin = _FakePlugin()
    # 经名称改写访问私有静态方法（保持与既有调用方一致）
    result = PluginManager._PluginManager__set_and_check_auth_level(plugin=fake_plugin)

    assert result is True
