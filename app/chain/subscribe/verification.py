"""订阅 force 完成守卫：以媒体库返回的事实集号独立交叉验证。

独立于 SubscribeChain 各 Owner 之外的模块级实现，避免把守卫逻辑塞进
超大类触发复杂度棘轮（类行数低水位只降不升）。
"""

from typing import TYPE_CHECKING, Dict, Tuple, Union

from app.application.subscription.contract import SubscriptionSnapshot
from app.chain.download import DownloadChain
from app.domain.context import MediaInfo
from app.domain.meta.metabase import MetaBase
from app.runtime.log import logger
from app.schemas.mediaserver import NotExistMediaInfo as _SchemaNotExistMediaInfo
from app.schemas.types import MediaType

if TYPE_CHECKING:  # pragma: no cover
    from app.chain.subscribe.query import SubscribeQueryOwner

NotExistMap = Dict[Union[str, int], Dict[int, _SchemaNotExistMediaInfo]]


def resolve_effective_total_episode(subscribe: SubscriptionSnapshot, mediainfo: MediaInfo) -> int:
    """
    只读计算完成前有效总集数，不触发事件、不写回订阅。

    主流程会通过 ``__refresh_total_episode_before_completion`` 持久化增长后的总集数；
    该查询接口只需要同样避免旧 total 造成误判，因此仅使用当前 mediainfo 中更大的
    季集数作为临时目标范围。
    """
    current_total = subscribe.total_episode or 0
    if subscribe.type != MediaType.TV.value:
        return current_total
    if subscribe.manual_total_episode:
        return current_total
    if subscribe.season is None:
        return current_total
    media_total = len((mediainfo.seasons or {}).get(subscribe.season) or [])
    if media_total > current_total:
        return media_total
    return current_total


def media_library_satisfies_subscription(subscribe: SubscriptionSnapshot, mediainfo: MediaInfo) -> bool:
    """
    force 完成前的保守守卫：二次核验媒体库实际返回的集号集合是否真正覆盖订阅预期。

    仅在媒体库已报「全部存在」（exist_flag=True）时调用。即便如此，仍可能因两类
    误判而过早完成订阅，这里用媒体库返回的「事实集号」做独立交叉验证：
      1) 媒体库误报：集号对上但文件实为其它 / 部分导入 —— 预期集号未全部落在返回集合内；
      2) 订阅总集数偏低：媒体库已存在超过预期总集数的集 —— 说明预期总集数不够，应继续订阅。

    非电视剧、缺季号或无有效总集数时直接放行，不影响电影/音乐的既有完成逻辑。
    """
    if subscribe.type != MediaType.TV.value or subscribe.season is None:
        return True
    effective_total = resolve_effective_total_episode(subscribe, mediainfo)
    if not effective_total:
        return True
    season = subscribe.season
    season_episodes = mediainfo.seasons.get(season) or []
    start = subscribe.start_episode or (min(season_episodes) if season_episodes else 1)
    expected = set(range(start, effective_total + 1))
    if not expected:
        return True

    # 直接取媒体库该标题/季实际存在的集号集合（与 get_no_exists_info 同源，但只读事实）
    exists = DownloadChain().media_exists(mediainfo=mediainfo)
    if not exists:
        logger.debug(f"{mediainfo.title_year} 二次核验时媒体库查询失败，谨慎起见不强制完成")
        return False
    present = set((exists.seasons or {}).get(season) or [])
    if not present:
        logger.debug(f"{mediainfo.title_year} 媒体库未返回该季集号，谨慎起见不强制完成")
        return False

    # 守卫 1：预期集号必须全部在媒体库返回集合内
    missing_in_library = expected - present
    if missing_in_library:
        logger.info(
            f"{mediainfo.title_year} 第{season}季 预期集号 {sorted(expected)} 与媒体库实际集号 "
            f"{sorted(present)} 不一致，缺失 {sorted(missing_in_library)}，不强制完成"
        )
        return False
    # 守卫 2：媒体库集号上限不得超过订阅预期总集数（防止总集数偏低误判完成）
    if max(present) > effective_total:
        logger.info(
            f"{mediainfo.title_year} 第{season}季 媒体库最大集号 {max(present)} 超过订阅预期总集数 "
            f"{effective_total}，总集数可能偏低，继续订阅以补齐"
        )
        return False
    return True


def finish_or_continue_subscription(
    owner: "SubscribeQueryOwner",
    subscribe: SubscriptionSnapshot,
    meta: MetaBase,
    mediainfo: MediaInfo,
    no_exists: NotExistMap,
) -> Tuple[bool, NotExistMap]:
    """exist_flag=True 时的统一出口：媒体库二次核验通过才 force 完成订阅。"""
    if not media_library_satisfies_subscription(subscribe, mediainfo):
        logger.info(f"{mediainfo.title_year} 媒体库存在性二次核验未通过，继续订阅 ...")
        return False, no_exists
    logger.info(f"{mediainfo.title_year} 已全部下载")
    owner.finish_subscribe_or_not(subscribe=subscribe, meta=meta, mediainfo=mediainfo, force=True)
    return True, no_exists
