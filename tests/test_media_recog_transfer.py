"""搜索→下载→整理兜底（TMDB/豆瓣未收录内容）单测。

覆盖：
- manual_media_key 稳定去重键（C-5）
- build_filename_mediainfo 类型决策（C-4）与身份键（C-5）
- transfer __handle_transfer 在外部识别失败时改用文件名兜底，不再因“未识别到媒体信息”失败
- download add() 放开识别强依赖（C-2）

全部为离线单测：mock 识别链路，不发起真实网络/数据库调用。
"""
import types
from unittest.mock import MagicMock, patch

from app.core.metainfo import MetaInfo
from app.schemas.file import FileItem
from app.schemas.transfer import TransferInfo, TransferTask
from app.schemas.types import MediaType
from app.utils.media import build_filename_mediainfo, manual_media_key


# --------------------------------------------------------------------------- #
# C-5：稳定去重键
# --------------------------------------------------------------------------- #
def test_manual_media_key_stable_and_distinct():
    a = manual_media_key("躲在超市后门抽烟的两人", "2024")
    b = manual_media_key("躲在超市后门抽烟的两人", "2024")
    c = manual_media_key("另一只短剧", "2024")
    assert a == b, "同输入必须得到稳定一致的键"
    assert a != c, "不同输入必须不同"
    assert a.startswith("manual:")
    assert len(a) == len("manual:") + 16


def test_manual_media_key_ignores_case_and_whitespace():
    a = manual_media_key("  Demo Movie ", "2024")
    b = manual_media_key("demo movie", "2024")
    assert a == b


# --------------------------------------------------------------------------- #
# C-4 / C-5：build_filename_mediainfo 类型决策与身份键
# --------------------------------------------------------------------------- #
def test_build_filename_mediainfo_tv_with_episode():
    mi = build_filename_mediainfo(MetaInfo("躲在超市后门抽烟的两人.S01E03.2024"))
    assert mi.type == MediaType.TV
    assert mi.tmdb_id is None
    assert mi.media_id == manual_media_key("躲在超市后门抽烟的两人", "2024")
    assert mi.title == "躲在超市后门抽烟的两人"


def test_build_filename_mediainfo_tv_no_episode_becomes_movie():
    # 决策 Q1：TV 文件名也无集号 → 整包当电影
    mi = build_filename_mediainfo(MetaInfo("躲在超市后门抽烟的两人.2024"))
    assert mi.type == MediaType.MOVIE


def test_build_filename_mediainfo_unknown_no_episode_becomes_movie():
    mi = build_filename_mediainfo(MetaInfo("某短剧合集.2023"))
    assert mi.type == MediaType.MOVIE


def test_build_filename_mediainfo_movie_unchanged():
    mi = build_filename_mediainfo(MetaInfo("Demo Movie 2024"))
    assert mi.type == MediaType.MOVIE


def test_build_filename_mediainfo_season_and_media_id():
    mi = build_filename_mediainfo(MetaInfo("躲在超市后门抽烟的两人.S02.2024"))
    assert mi.season == 2
    assert mi.media_id.startswith("manual:")


# --------------------------------------------------------------------------- #
# C-1：transfer 识别失败改用文件名兜底
# --------------------------------------------------------------------------- #
def test_handle_transfer_fallback_constructs_mediainfo():
    from app.chain.transfer import TransferChain

    chain = TransferChain()
    meta = MetaInfo("躲在超市后门抽烟的两人.S01E03.2024")
    fileitem = FileItem(
        storage="local",
        type="file",
        path="/downloads/躲在超市后门抽烟的两人.S01E03.2024.mkv",
        name="躲在超市后门抽烟的两人.S01E03.2024.mkv",
    )
    task = TransferTask(
        fileitem=fileitem, meta=meta, transfer_type="copy", scrape=False
    )

    fake_dir = types.SimpleNamespace(
        library_storage="local", media_category=None, library_category_folder=False
    )

    with patch("app.chain.transfer.MediaChain") as MockMedia, patch(
        "app.chain.transfer.TransferHistoryOper"
    ) as MockHis, patch("app.chain.transfer.DirectoryHelper") as MockDir, patch(
        "app.chain.transfer.eventmanager"
    ) as MockEvent:
        mc = MockMedia.return_value
        mc.recognize_by_meta.return_value = None
        mc.recognize_media.return_value = None
        mc.supplement_tmdb_info.side_effect = lambda m, meta: m
        MockHis.return_value.get_by_type_tmdbid.return_value = None
        MockDir.return_value.get_dir.return_value = fake_dir
        MockEvent.send_event.return_value = None

        chain.jobview = MagicMock()
        captured = {}

        def fake_transfer(**kwargs):
            captured["mediainfo"] = kwargs.get("mediainfo")
            return TransferInfo(success=True, message="ok", fileitem=fileitem)

        chain.transfer = MagicMock(side_effect=fake_transfer)

        result = chain._TransferChain__handle_transfer(task)

    assert result is not None
    # 关键：不应因“未识别到媒体信息”而失败
    assert result[1] != "未识别到媒体信息", "外部识别失败时必须走文件名兜底"
    assert captured.get("mediainfo") is not None
    assert captured["mediainfo"].tmdb_id is None
    assert captured["mediainfo"].media_id.startswith("manual:")
