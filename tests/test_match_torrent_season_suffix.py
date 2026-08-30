"""match_torrent 的季集后缀兜底匹配测试。

部分索引器（如 Jackett）返回的种子仅写剧名（``Fallout``），而 TMDB 给的
媒体原标题常带季（``Fallout Season 2``），归一化后二者无法直接字符串相交，
导致剧集订阅 0 命中。补丁在强校验（类型/年份/季集拓扑）通过后再做一次去
季集后缀的兜底比较。
"""

from app.application.torrent import TorrentHelper, _strip_season_suffix
from app.domain.context import MediaInfo, TorrentInfo
from app.domain.metainfo import MetaInfo
from app.schemas.types import MediaType


def _media(
        title: str = "辐射 第二季",
        original_title: str = "Fallout Season 2",
        names: list = None,
        year: str = None,
) -> MediaInfo:
    m = MediaInfo()
    m.type = MediaType.TV
    m.title = title
    m.original_title = original_title
    m.names = names or ["异尘余生 第二季"]
    m.year = year
    return m


def _torrent_meta(title: str) -> MetaInfo:
    return MetaInfo(title=title)


def _torrent(title: str) -> TorrentInfo:
    t = TorrentInfo()
    t.title = title
    t.site_name = "jackett_extend.narrator-z"
    return t


def test_strip_season_suffix_basic():
    assert _strip_season_suffix("Fallout Season 2") == "Fallout"
    assert _strip_season_suffix("Fallout Season 2 S02E01") == "Fallout"
    assert _strip_season_suffix("Fallout S02E01") == "Fallout"
    assert _strip_season_suffix("") == ""


def test_match_torrent_season_suffix_fallback():
    """原标题带季、种子仅剧名：兜底匹配应命中。"""
    media = _media()
    meta = _torrent_meta("Fallout.S02E01.1080p.WEB-DL.x264-GROUP")
    torrent = _torrent("Fallout.S02E01.1080p.WEB-DL.x264-GROUP")
    assert TorrentHelper.match_torrent(media, meta, torrent) is True


def test_match_torrent_season_suffix_explicit_season_title():
    """种子标题显式写 Season 2：兜底匹配应命中。"""
    media = _media()
    meta = _torrent_meta("Fallout Season 2 S02E01 720p WEBRip")
    torrent = _torrent("Fallout Season 2 S02E01 720p WEBRip")
    assert TorrentHelper.match_torrent(media, meta, torrent) is True


def test_match_torrent_rejects_wrong_series():
    """不同剧名不应误判命中。"""
    media = _media()
    meta = _torrent_meta("Breaking.Bad.S02E01.1080p")
    torrent = _torrent("Breaking.Bad.S02E01.1080p")
    assert TorrentHelper.match_torrent(media, meta, torrent) is False


def test_match_torrent_names_fallback():
    """译名路径也应走兜底匹配。"""
    media = _media(names=["异尘余生 第二季"])
    meta = _torrent_meta("Fallout.S02E01.1080p")
    torrent = _torrent("Fallout.S02E01.1080p")
    assert TorrentHelper.match_torrent(media, meta, torrent) is True
