"""订阅完成真实入库守卫单元测试。

验证 finish_subscribe_or_not / __is_truly_completed 不再仅凭媒体服务器存在性
检查(如飞牛影视误报)或下载记录就删除订阅转历史，而是要求 TransferHistory 中
确有 status=True 的成功转存记录覆盖所有应追集(TV)/资源(电影)。

覆盖两类真实误删场景：
- 电影仅下载未转存成功(status=True 缺失) -> 不应完成
- 电视剧缺某集无成功转存 -> 不应完成
"""
from types import SimpleNamespace
from unittest.mock import patch

from app.chain.subscribe import SubscribeChain
from app.schemas.types import MediaType


def _make_chain():
    # 绕过 __init__ 重依赖，仅用于调用纯逻辑私有方法
    return object.__new__(SubscribeChain)


def _fake_subscribe(tmdbid=100, name="测试片", season=None, total_episode=0,
                     start_episode=1, best_version=False):
    return SimpleNamespace(
        tmdbid=tmdbid, name=name, season=season, total_episode=total_episode,
        start_episode=start_episode, best_version=best_version,
        type=MediaType.TV.value, manual_total_episode=False,
    )


def _fake_meta(tv: bool):
    if tv:
        return SimpleNamespace(type=MediaType.TV, seasons={}, title_year="测试片")
    return SimpleNamespace(type=MediaType.MOVIE, seasons=None, title_year="测试片")


def _transfer_row(episodes, status=True, mtype=MediaType.TV.value, tmdbid=100, season="S01"):
    return SimpleNamespace(episodes=episodes, status=status, type=mtype, tmdbid=tmdbid,
                          seasons=season, title="测试片")


def test_tv_complete_when_all_episodes_transferred():
    chain = _make_chain()
    sub = _fake_subscribe(tmdbid=100, season=1, total_episode=8)
    meta = _fake_meta(tv=True)
    meta.seasons = {1: [None] * 8}
    rows = [_transfer_row(f"E0{i}") for i in range(1, 9)]
    with patch("app.chain.subscribe.TransferHistory.list_by", return_value=rows):
        assert chain._SubscribeChain__is_truly_completed(sub, meta, meta) is True


def test_tv_not_complete_when_episode_missing():
    chain = _make_chain()
    sub = _fake_subscribe(tmdbid=100, season=1, total_episode=8)
    meta = _fake_meta(tv=True)
    meta.seasons = {1: [None] * 8}
    # 缺 E08
    rows = [_transfer_row(f"E0{i}") for i in range(1, 8)]
    with patch("app.chain.subscribe.TransferHistory.list_by", return_value=rows):
        assert chain._SubscribeChain__is_truly_completed(sub, meta, meta) is False


def test_tv_range_episode_parsed():
    chain = _make_chain()
    sub = _fake_subscribe(tmdbid=100, season=1, total_episode=8)
    meta = _fake_meta(tv=True)
    meta.seasons = {1: [None] * 8}
    # 打包下载 E01-E08
    with patch("app.chain.subscribe.TransferHistory.list_by", return_value=[_transfer_row("E01-E08")]):
        assert chain._SubscribeChain__is_truly_completed(sub, meta, meta) is True


def test_tv_no_transfer_history_not_complete():
    chain = _make_chain()
    sub = _fake_subscribe(tmdbid=100, season=1, total_episode=8)
    meta = _fake_meta(tv=True)
    meta.seasons = {1: [None] * 8}
    with patch("app.chain.subscribe.TransferHistory.list_by", return_value=[]):
        assert chain._SubscribeChain__is_truly_completed(sub, meta, meta) is False


def test_movie_complete_when_transferred():
    chain = _make_chain()
    sub = _fake_subscribe(tmdbid=200)
    meta = _fake_meta(tv=False)
    with patch("app.chain.subscribe.TransferHistory.list_by",
               return_value=[_transfer_row("E01", mtype=MediaType.MOVIE.value, tmdbid=200)]), \
         patch("app.chain.subscribe.TransferHistory.list_by_title", return_value=[]):
        assert chain._SubscribeChain__is_truly_completed(sub, meta, meta) is True


def test_movie_not_complete_when_no_transfer():
    chain = _make_chain()
    sub = _fake_subscribe(tmdbid=200)
    meta = _fake_meta(tv=False)
    with patch("app.chain.subscribe.TransferHistory.list_by", return_value=[]), \
         patch("app.chain.subscribe.TransferHistory.list_by_title", return_value=[]):
        assert chain._SubscribeChain__is_truly_completed(sub, meta, meta) is False


def test_tv_unknown_total_not_complete():
    chain = _make_chain()
    sub = _fake_subscribe(tmdbid=100, season=1, total_episode=0)
    meta = _fake_meta(tv=True)
    meta.seasons = {}
    with patch("app.chain.subscribe.TransferHistory.list_by", return_value=[]):
        assert chain._SubscribeChain__is_truly_completed(sub, meta, meta) is False
