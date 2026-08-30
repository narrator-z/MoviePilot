"""finish_subscribe_or_not 媒体服务器(飞牛)复核闸门单元测试。

验证普通订阅完成需同时满足两道闸门：
- 第一道：我们自己的成功转存记录(status=True) —— 见 test_subscribe_complete_guard
- 第二道：媒体服务器(飞牛影视等)确报已入库

并覆盖边界：
- 已配置媒体服务器但库里没有(飞牛没有的) -> 不应完成（修复点）
- 未配置任何媒体服务器 -> 退化为仅以转存记录为准，仍然完成（保持旧行为，不误删订阅）
- 转存记录本身不齐 -> 不应完成
"""
from types import SimpleNamespace
from unittest.mock import patch

from app.chain.subscribe import SubscribeChain
from app.schemas.types import MediaType, ModuleType


class _FakeModuleManager:
    """模拟 ModuleManager：has_server 控制是否“已配置媒体服务器”。"""

    def __init__(self, has_server: bool):
        self._has_server = has_server

    def get_running_type_modules(self, module_type):
        if self._has_server and module_type == ModuleType.MediaServer:
            yield object()


def _make_chain(has_server: bool) -> SubscribeChain:
    chain = object.__new__(SubscribeChain)
    chain.modulemanager = _FakeModuleManager(has_server)
    return chain


def _fake_subscribe(best_version=False):
    return SimpleNamespace(
        tmdbid=30003, name="测试电影", season=None, total_episode=1,
        start_episode=1, best_version=best_version, type=MediaType.MOVIE.value,
        manual_total_episode=False, current_priority=None, episode_priority={},
        note=[], lack_episode=1,
    )


def _run(chain, *, transfer_ok: bool, fn_present: bool):
    finished = []

    def _finish(**_kwargs):
        finished.append(1)

    # 真实跑 __fn_confirms_present（含媒体服务器配置判定），隔离其它副作用方法
    with patch.object(chain, "_SubscribeChain__finish_subscribe", side_effect=_finish), \
         patch.object(chain, "_SubscribeChain__is_truly_completed", return_value=transfer_ok), \
         patch.object(chain, "resolve_subscribe_missing", return_value=(fn_present, {})):
        chain.finish_subscribe_or_not(
            subscribe=_fake_subscribe(),
            meta=SimpleNamespace(type=MediaType.MOVIE),
            mediainfo=SimpleNamespace(title_year="测试电影 (2026)", tmdb_id=30003,
                                      douban_id=None, bangumi_id=None, anilist_id=None,
                                      seasons=None),
            downloads=None,
            lefts={},
        )
    return finished


def test_complete_when_transferred_and_fn_present():
    """已配置飞牛且库里确报存在 + 转存成功 -> 完成。"""
    chain = _make_chain(has_server=True)
    assert _run(chain, transfer_ok=True, fn_present=True) == [1]


def test_no_complete_when_fn_missing_even_if_transferred():
    """飞牛没有的(库未确认) + 转存成功 -> 不应完成（核心修复点）。"""
    chain = _make_chain(has_server=True)
    assert _run(chain, transfer_ok=True, fn_present=False) == []


def test_no_complete_when_transfer_incomplete():
    """转存记录不齐 -> 不应完成（与 fn 无关）。"""
    chain = _make_chain(has_server=True)
    assert _run(chain, transfer_ok=False, fn_present=True) == []


def test_complete_when_no_media_server_configured():
    """未配置任何媒体服务器 -> 退化为仅以转存记录为准，仍完成（不误删订阅）。"""
    chain = _make_chain(has_server=False)
    # 无论 resolve_subscribe_missing 返回什么（这里模拟库缺失），都应放行
    assert _run(chain, transfer_ok=True, fn_present=False) == [1]
    assert _run(chain, transfer_ok=True, fn_present=True) == [1]
