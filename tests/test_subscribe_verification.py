"""
订阅完成守卫的独立交叉验证测试。

覆盖有效总集数推断、媒体库二次核验的各条守卫分支，以及强制完成的统一出口。
"""

from types import SimpleNamespace
from unittest.mock import Mock

from app.application.subscription.contract import SubscriptionSnapshot
from app.chain.subscribe import verification
from app.domain.context import MediaInfo
from app.schemas.types import MediaType


def _tv_subscribe(**overrides):
    """
    构造电视剧订阅快照。

    :param overrides: 覆盖默认字段的关键字参数
    :return: 电视剧订阅快照
    """
    values = dict(
        id=1,
        name="演示剧集",
        type=MediaType.TV.value,
        season=1,
        total_episode=3,
        start_episode=1,
        manual_total_episode=True,
    )
    values.update(overrides)
    return SubscriptionSnapshot(**values)


def _media(seasons):
    """
    构造带季集清单的媒体信息。

    :param seasons: 季号到剧集号列表的映射
    :return: 媒体信息
    """
    return MediaInfo(title="演示剧集", seasons=seasons)


def test_resolve_effective_total_episode_branches():
    """
    有效总集数按媒体类型、手动标记、季号与媒体库集数分支返回。
    """
    movie = SubscriptionSnapshot(id=1, name="电影", type=MediaType.MOVIE.value, total_episode=8)
    assert verification.resolve_effective_total_episode(movie, _media({})) == 8

    assert verification.resolve_effective_total_episode(
        _tv_subscribe(), _media({1: list(range(1, 13))})
    ) == 3

    no_season = _tv_subscribe(season=None, manual_total_episode=False)
    assert verification.resolve_effective_total_episode(no_season, _media({})) == 3

    auto = _tv_subscribe(manual_total_episode=False, total_episode=8)
    assert verification.resolve_effective_total_episode(auto, _media({1: list(range(1, 13))})) == 12
    assert verification.resolve_effective_total_episode(auto, _media({1: [1, 2, 3]})) == 8


def test_media_library_satisfies_subscription_guards(monkeypatch):
    """
    媒体库二次核验在放行条件与各类误判下给出正确结论。
    """
    media = _media({1: list(range(1, 13))})
    movie = SubscriptionSnapshot(id=1, name="电影", type=MediaType.MOVIE.value)
    assert verification.media_library_satisfies_subscription(movie, media) is True
    assert verification.media_library_satisfies_subscription(
        _tv_subscribe(total_episode=0), media
    ) is True
    assert verification.media_library_satisfies_subscription(
        _tv_subscribe(start_episode=20), media
    ) is True

    class _FakeChain:
        """提供可注入的媒体库存在性查询结果。"""

        def __init__(self, exists):
            """保存待返回的查询事实。"""
            self._exists = exists

        def media_exists(self, mediainfo):
            """返回注入的媒体库事实，忽略入参。"""
            return self._exists

    subscribe = _tv_subscribe(total_episode=3)

    monkeypatch.setattr(verification, "DownloadChain", lambda: _FakeChain(None))
    assert verification.media_library_satisfies_subscription(subscribe, media) is False

    monkeypatch.setattr(
        verification,
        "DownloadChain",
        lambda: _FakeChain(SimpleNamespace(seasons={1: []})),
    )
    assert verification.media_library_satisfies_subscription(subscribe, media) is False

    monkeypatch.setattr(
        verification,
        "DownloadChain",
        lambda: _FakeChain(SimpleNamespace(seasons={1: [1, 2]})),
    )
    assert verification.media_library_satisfies_subscription(subscribe, media) is False

    monkeypatch.setattr(
        verification,
        "DownloadChain",
        lambda: _FakeChain(SimpleNamespace(seasons={1: [1, 2, 3, 4]})),
    )
    assert verification.media_library_satisfies_subscription(subscribe, media) is False

    monkeypatch.setattr(
        verification,
        "DownloadChain",
        lambda: _FakeChain(SimpleNamespace(seasons={1: [1, 2, 3]})),
    )
    assert verification.media_library_satisfies_subscription(subscribe, media) is True


def test_finish_or_continue_subscription(monkeypatch):
    """
    核验未通过时继续订阅，通过后才强制完成订阅。
    """
    subscribe = _tv_subscribe()
    media = _media({})
    owner = SimpleNamespace(finish_subscribe_or_not=Mock())
    no_exists = {"tmdb:1": {}}

    monkeypatch.setattr(verification, "media_library_satisfies_subscription", lambda s, m: False)
    finished, result = verification.finish_or_continue_subscription(owner, subscribe, None, media, no_exists)
    assert finished is False
    assert result is no_exists
    owner.finish_subscribe_or_not.assert_not_called()

    monkeypatch.setattr(verification, "media_library_satisfies_subscription", lambda s, m: True)
    finished, result = verification.finish_or_continue_subscription(owner, subscribe, None, media, no_exists)
    assert finished is True
    assert result is no_exists
    owner.finish_subscribe_or_not.assert_called_once()
