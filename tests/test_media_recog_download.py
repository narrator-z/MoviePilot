"""download add() 放开识别强依赖（C-2）单测。

- 无显式 ID 且 TMDB/豆瓣未收录：用文件名兜底，不再返回“无法识别媒体信息”
- 有显式 ID 但识别失败：仍属真实错误，保持原报错

全部为离线单测：mock 识别链路与下载器，不发起真实网络/数据库调用。
"""
from unittest.mock import MagicMock, patch

from app.schemas import TorrentInfo


def _make_torrent(title: str) -> TorrentInfo:
    return TorrentInfo(title=title, description="")


def _fake_user():
    user = MagicMock()
    user.name = "tester"
    return user


def test_add_relaxes_recognition_when_no_id():
    from app.api.endpoints import download as dl

    torrent_in = _make_torrent("躲在超市后门抽烟的两人.S01E03.2024")
    captured = {}

    with patch("app.api.endpoints.download.MediaChain") as MockMedia, patch(
        "app.api.endpoints.download.DownloadChain"
    ) as MockDL:
        mc = MockMedia.return_value
        mc.recognize_by_meta.return_value = None
        mc.recognize_media.return_value = None

        dl_inst = MockDL.return_value

        def fake_download_single(**kwargs):
            captured["media_info"] = kwargs.get("context").media_info
            return "download_hash_123"

        dl_inst.download_single.side_effect = fake_download_single

        resp = dl.add(torrent_in=torrent_in, current_user=_fake_user())

    assert resp.success is True
    assert resp.data == {"download_id": "download_hash_123"}
    mi = captured["media_info"]
    assert mi.tmdb_id is None
    assert mi.media_id.startswith("manual:"), "兜底媒体必须带稳定身份键"


def test_add_still_fails_with_explicit_id_and_no_recognition():
    from app.api.endpoints import download as dl

    torrent_in = _make_torrent("Some Movie 2024")

    with patch("app.api.endpoints.download.MediaChain") as MockMedia, patch(
        "app.api.endpoints.download.DownloadChain"
    ):
        mc = MockMedia.return_value
        mc.recognize_media.return_value = None  # 显式 ID 提供但识别失败

        # 显式 ID 路径失败仍返回错误
        resp = dl.add(torrent_in=torrent_in, tmdbid=123, current_user=_fake_user())

    assert resp.success is False
    assert resp.message == "无法识别媒体信息"
