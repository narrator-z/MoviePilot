"""站点级插件索引器路由。

插件通过 SitesHelper.add_indexer 注册的索引器没有宿主搜索配置，
必须能在站点搜索入口被插件模块认领，否则会静默返回空结果。
"""

import pytest
from unittest.mock import AsyncMock, Mock

from app.chain.base import ChainBase
from app.schemas.types import MediaType

PLUGIN_SITE = {
    "id": "JackettExtend-crackingpatching",
    "name": "JackettExtend-crackingpatching",
    "domain": "jackett_extend.crackingpatching",
    "url": "http://jackett:9117/api/v2.0/indexers/crackingpatching/results/torznab/",
    "public": True,
    "proxy": False,
}


def _chain_without_init() -> ChainBase:
    """构造不加载真实模块和外部服务的链实例。"""
    chain = object.__new__(ChainBase)
    chain.eventmanager = Mock(check=Mock(return_value=False))
    return chain


def _chain_with_dispatcher() -> ChainBase:
    """构造仅替换分发器的链实例，用于观察模块调用路径。"""
    chain = _chain_without_init()
    chain._module_dispatcher = Mock()
    chain._module_dispatcher.async_execute_plugin_modules = AsyncMock(return_value=[])
    chain._module_dispatcher.async_execute_system_modules = AsyncMock(return_value=[])
    return chain


def test_plugin_claimed_site_short_circuits_system_indexer() -> None:
    """插件注册索引器命中插件模块时应直接返回，不再调用宿主索引器。"""
    chain = _chain_with_dispatcher()
    plugin_torrents = ["plugin-torrent"]
    chain._module_dispatcher.execute_plugin_modules = Mock(return_value=plugin_torrents)
    chain._module_dispatcher.execute_system_modules = Mock(return_value=["system-torrent"])

    result = chain.search_site_torrents(
        site=PLUGIN_SITE, keyword="Possession", mtype=MediaType.MOVIE, page=0
    )

    assert result == plugin_torrents
    chain._module_dispatcher.execute_system_modules.assert_not_called()


def test_plugin_claimed_site_receives_real_site_dict() -> None:
    """站点入口必须把真实 site 透传给插件模块，空字典会让插件拒绝认领。"""
    chain = _chain_with_dispatcher()
    chain._module_dispatcher.execute_plugin_modules = Mock(return_value=["plugin-torrent"])
    chain._module_dispatcher.execute_system_modules = Mock(return_value=[])

    chain.search_site_torrents(
        site=PLUGIN_SITE, keyword="Possession", mtype=MediaType.MOVIE, page=0
    )

    call = chain._module_dispatcher.execute_plugin_modules.call_args
    assert call.args[0] == "search_torrents"
    assert call.kwargs["site"] == PLUGIN_SITE
    assert call.kwargs["keyword"] == "Possession"
    assert call.kwargs["mtype"] == MediaType.MOVIE
    assert call.kwargs["page"] == 0


def test_unclaimed_site_falls_back_to_system_indexer() -> None:
    """插件未认领的普通站点仍由宿主索引器搜索。"""
    chain = _chain_with_dispatcher()
    system_torrents = ["system-torrent"]
    chain._module_dispatcher.execute_plugin_modules = Mock(return_value=[])
    chain._module_dispatcher.execute_system_modules = Mock(return_value=system_torrents)
    site = {"id": 1, "name": "普通站点", "domain": "example.com"}

    result = chain.search_site_torrents(site=site, keyword="Possession")

    assert result == system_torrents
    chain._module_dispatcher.execute_system_modules.assert_called_once()


def test_unclaimed_site_falls_back_when_plugin_returns_none() -> None:
    """插件返回 None 时同样视为未认领，不应短路。"""
    chain = _chain_with_dispatcher()
    chain._module_dispatcher.execute_plugin_modules = Mock(return_value=None)
    chain._module_dispatcher.execute_system_modules = Mock(return_value=["system-torrent"])

    result = chain.search_site_torrents(site=PLUGIN_SITE, keyword="Possession")

    assert result == ["system-torrent"]


@pytest.mark.asyncio
async def test_async_plugin_claimed_site_short_circuits_system_indexer() -> None:
    """异步站点入口命中插件模块时应直接返回，不再调用宿主索引器。"""
    chain = _chain_with_dispatcher()
    plugin_torrents = ["plugin-torrent"]
    chain._module_dispatcher.async_execute_plugin_modules = AsyncMock(return_value=plugin_torrents)
    chain._module_dispatcher.async_execute_system_modules = AsyncMock(return_value=["system-torrent"])

    result = await chain.async_search_site_torrents(
        site=PLUGIN_SITE, keyword="Possession", mtype=MediaType.MOVIE, page=0
    )

    assert result == plugin_torrents
    chain._module_dispatcher.async_execute_system_modules.assert_not_called()
    call = chain._module_dispatcher.async_execute_plugin_modules.call_args
    assert call.args[0] == "async_search_torrents"
    assert call.kwargs["site"] == PLUGIN_SITE


@pytest.mark.asyncio
async def test_async_unclaimed_site_falls_back_to_system_indexer() -> None:
    """异步站点入口在插件未认领时仍由宿主索引器搜索。"""
    chain = _chain_with_dispatcher()
    system_torrents = ["system-torrent"]
    chain._module_dispatcher.async_execute_plugin_modules = AsyncMock(return_value=[])
    chain._module_dispatcher.async_execute_system_modules = AsyncMock(return_value=system_torrents)

    result = await chain.async_search_site_torrents(site=PLUGIN_SITE, keyword="Possession")

    assert result == system_torrents
    chain._module_dispatcher.async_execute_system_modules.assert_called_once()
