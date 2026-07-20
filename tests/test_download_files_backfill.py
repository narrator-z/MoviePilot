"""
磁力链接（magnet）下载后 downloadfiles 表补写回归测试。

验证工程师修复的两个入口：
- ``DownloadChain._add_download_files_from_downloader``：从下载器回查真实文件清单，幂等写入 downloadfiles。
- ``TransferChain._ensure_download_files_for_transfer``：整理完成时兜底补写。

全部为纯单元（mock）测试，不依赖真实下载器与数据库。
"""
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import MagicMock

import app.chain.download as download_module
import app.chain.transfer as transfer_module
from app.chain.download import DownloadChain
from app.chain.transfer import TransferChain
from app.core.config import settings  # noqa: F401  仅复用真实扩展名配置
from app.core.context import Context, MediaInfo, TorrentInfo
from app.core.metainfo import MetaInfo
from app.db.models.downloadhistory import DownloadFiles
from app.schemas import TransferDirectoryConf
from app.schemas.types import MediaType


def _download_dirs():
    """构造允许下载目录配置，使 /downloads 成为合法保存路径。"""
    return [
        TransferDirectoryConf(
            name="本地下载",
            priority=1,
            storage="local",
            download_path="/downloads",
        ),
    ]


class _FakeFileOper:
    """捕获 downloadfiles 写入，并模拟幂等检查（get_files_by_hash）。"""

    def __init__(self, existing: Optional[list] = None):
        self._existing = list(existing or [])
        self.added_files: List[dict] = []
        self.add_history_called = 0
        self.add_files_called = 0

    def add(self, **_kwargs):
        self.add_history_called += 1

    def add_files(self, file_items):
        self.add_files_called += 1
        self.added_files.extend(file_items)

    def get_files_by_hash(self, _download_hash, state=None):
        return self._existing

    def get_by_hash(self, _download_hash):
        return SimpleNamespace(torrent_name="Demo.Show.2024.1080p")


class _FakeThreadHelper:
    """捕获后台任务提交，避免真正启动线程（也不执行 _run，避免 time.sleep）。"""

    submitted = []

    def submit(self, func, *args, **kwargs):
        self.submitted.append((func, args, kwargs))


class _FakeTorrentHelper:
    """模拟种子内容解析；file_list 由构造参数控制。"""

    def __init__(self, file_list):
        self._file_list = file_list

    def get_fileinfo_from_torrent_content(self, _content):
        return None, self._file_list


def _make_download_chain() -> DownloadChain:
    return DownloadChain.__new__(DownloadChain)


def test_magnet_backfill_writes_media_files_only(monkeypatch):
    """场景1：磁力链接下载，torrent_files 返回文件清单 → downloadfiles 被写入
    （媒体文件保留、非媒体被过滤、state 默认 1）。"""
    oper = _FakeFileOper()
    monkeypatch.setattr(download_module, "DownloadHistoryOper", lambda: oper)

    chain = _make_download_chain()
    chain.torrent_files = MagicMock(return_value=[
        SimpleNamespace(name="Demo.Show.S01E01.1080p.mp4"),
        SimpleNamespace(name="Demo.Show.S01E02.1080p.mkv"),
        SimpleNamespace(name="Demo.Show.S01E01.sample.txt"),
        SimpleNamespace(name="Demo.Show.nfo"),
    ])

    ok = chain._add_download_files_from_downloader(
        download_hash="magnet_hash_001",
        downloader="qbittorrent",
        save_path=Path("/downloads/demo"),
        org_string="Demo.Show.2024.1080p",
    )

    assert ok is True
    assert oper.add_files_called == 1
    # 仅 .mp4 / .mkv 媒体文件被保留，.txt / .nfo 被过滤
    assert len(oper.added_files) == 2

    for rec in oper.added_files:
        assert rec["download_hash"] == "magnet_hash_001"
        assert rec["downloader"] == "qbittorrent"
        assert rec["torrentname"] == "Demo.Show.2024.1080p"
        assert rec["savepath"] == "/downloads/demo"
        assert rec["fullpath"] == f"/downloads/demo/{rec['filepath']}"
        assert rec["filepath"] in (
            "Demo.Show.S01E01.1080p.mp4",
            "Demo.Show.S01E02.1080p.mkv",
        )
        # 写入依赖模型默认 state=1（dict 不含 state 字段）
        assert "state" not in rec
        state_default = getattr(DownloadFiles.__table__.c["state"].default, "arg", None)
        assert state_default == 1


def test_magnet_backfill_is_idempotent(monkeypatch):
    """场景2：同一 hash 已存在记录 → 不再重复写入，且不会回查下载器。"""
    oper = _FakeFileOper(existing=[SimpleNamespace(id=1)])
    monkeypatch.setattr(download_module, "DownloadHistoryOper", lambda: oper)

    chain = _make_download_chain()
    chain.torrent_files = MagicMock(return_value=[SimpleNamespace(name="x.mp4")])

    ok = chain._add_download_files_from_downloader(
        download_hash="magnet_hash_002",
        downloader="qbittorrent",
        save_path=Path("/downloads/demo"),
        org_string="Demo",
    )

    assert ok is False
    assert oper.add_files_called == 0
    chain.torrent_files.assert_not_called()


def test_magnet_backfill_silent_on_error_or_empty(monkeypatch):
    """场景3：torrent_files 抛异常或返回 None → 不抛异常、不阻断主流程、不写入。"""
    oper = _FakeFileOper()
    monkeypatch.setattr(download_module, "DownloadHistoryOper", lambda: oper)

    # 3a: 抛异常
    chain = _make_download_chain()
    chain.torrent_files = MagicMock(side_effect=RuntimeError("downloader unreachable"))
    ok = chain._add_download_files_from_downloader(
        download_hash="magnet_hash_003",
        downloader="qbittorrent",
        save_path=Path("/downloads/demo"),
        org_string="Demo",
    )
    assert ok is False
    assert oper.add_files_called == 0

    # 3b: 返回 None
    chain = _make_download_chain()
    chain.torrent_files = MagicMock(return_value=None)
    ok = chain._add_download_files_from_downloader(
        download_hash="magnet_hash_003b",
        downloader="qbittorrent",
        save_path=Path("/downloads/demo"),
        org_string="Demo",
    )
    assert ok is False
    assert oper.add_files_called == 0


def test_torrent_file_uses_original_path_and_skips_backfill(monkeypatch):
    """场景4：普通 .torrent 下载走原有 get_fileinfo_from_torrent_content 路径写入，
    不触发新的后台补写分支。"""
    _FakeThreadHelper.submitted = []
    monkeypatch.setattr(download_module, "ThreadHelper", _FakeThreadHelper)
    oper = _FakeFileOper()
    monkeypatch.setattr(download_module, "DownloadHistoryOper", lambda: oper)
    monkeypatch.setattr(
        download_module,
        "TorrentHelper",
        lambda: _FakeTorrentHelper(["Demo.Show.S01E01.1080p.mp4", "Demo.Show.S01E02.1080p.mp4"]),
    )
    monkeypatch.setattr(
        "app.helper.directory.DirectoryHelper.get_download_dirs",
        lambda _self: _download_dirs(),
    )

    chain = _make_download_chain()
    chain.download = MagicMock(return_value=("qb", "hash_torrent", "Original", "添加下载成功"))
    chain.download_added = MagicMock()
    chain.eventmanager = MagicMock()
    chain.eventmanager.send_event.return_value = None
    chain.post_message = MagicMock()
    chain.messagehelper = MagicMock()
    chain._submit_download_files_task = MagicMock()  # 监听新后台补写分支

    context = Context(
        meta_info=MetaInfo("Demo Show 2024"),
        media_info=MediaInfo(
            type=MediaType.TV,
            title="Demo Show",
            year="2024",
            tmdb_id=1,
            genre_ids=[18],
        ),
        torrent_info=TorrentInfo(
            title="Demo Show 2024",
            enclosure="https://example.com/demo.torrent",
            site_cookie="uid=1",
            site_name="TestSite",
        ),
    )

    result = chain.download_single(
        context=context,
        torrent_content=b"torrent-content",
        save_path="/downloads",
        username="tester",
    )

    assert result == "hash_torrent"
    # 原链路写入文件明细
    assert oper.add_files_called == 1
    assert len(oper.added_files) == 2
    # 新后台补写分支未被触发
    chain._submit_download_files_task.assert_not_called()


def test_magnet_triggers_background_backfill(monkeypatch):
    """场景4补充：磁力链接（种子内容解析不到文件清单）走新后台补写分支。"""
    _FakeThreadHelper.submitted = []
    monkeypatch.setattr(download_module, "ThreadHelper", _FakeThreadHelper)
    oper = _FakeFileOper()
    monkeypatch.setattr(download_module, "DownloadHistoryOper", lambda: oper)
    monkeypatch.setattr(download_module, "TorrentHelper", lambda: _FakeTorrentHelper([]))  # 磁力链：空文件清单
    monkeypatch.setattr(
        "app.helper.directory.DirectoryHelper.get_download_dirs",
        lambda _self: _download_dirs(),
    )

    chain = _make_download_chain()
    chain.download = MagicMock(return_value=("qb", "hash_magnet", "Original", "添加下载成功"))
    chain.download_added = MagicMock()
    chain.eventmanager = MagicMock()
    chain.eventmanager.send_event.return_value = None
    chain.post_message = MagicMock()
    chain.messagehelper = MagicMock()
    chain._submit_download_files_task = MagicMock()

    context = Context(
        meta_info=MetaInfo("Demo Movie 2024"),
        media_info=MediaInfo(
            type=MediaType.MOVIE,
            title="Demo Movie",
            year="2024",
            tmdb_id=1,
            genre_ids=[18],
        ),
        torrent_info=TorrentInfo(
            title="Demo Movie 2024",
            enclosure="magnet:?xt=urn:btih:abcdef",
            site_cookie="uid=1",
            site_name="TestSite",
        ),
    )

    result = chain.download_single(
        context=context,
        torrent_content="magnet:?xt=urn:btih:abcdef",
        save_path="/downloads",
        username="tester",
    )

    assert result == "hash_magnet"
    # 原链路无文件可写
    assert oper.add_files_called == 0
    # 触发后台补写
    chain._submit_download_files_task.assert_called_once()
    kwargs = chain._submit_download_files_task.call_args.kwargs
    assert kwargs["download_hash"] == "hash_magnet"
    assert kwargs["downloader"] == "qb"
    assert kwargs["save_path"] == Path("/downloads")


def test_transfer_completion_triggers_download_files_backfill(monkeypatch):
    """场景5：整理完成时 __mark_torrent_completed_if_done 触发
    _ensure_download_files_for_transfer 并成功补写 downloadfiles。"""
    oper = _FakeFileOper(existing=[])  # 无已存在记录
    monkeypatch.setattr(transfer_module, "DownloadHistoryOper", lambda: oper)
    backfill = MagicMock(return_value=True)
    monkeypatch.setattr(
        download_module.DownloadChain, "_add_download_files_from_downloader", backfill
    )

    chain = TransferChain.__new__(TransferChain)
    chain.jobview = MagicMock()
    chain.jobview.is_torrent_done.return_value = True
    chain.transfer_completed = MagicMock()
    chain.list_torrents = MagicMock(
        return_value=[SimpleNamespace(save_path="/downloads/movie", path=None)]
    )

    # __mark_torrent_completed_if_done 为双下划线私有方法，存在名称改写（name mangling）。
    chain._TransferChain__mark_torrent_completed_if_done("hash_transfer", "qbittorrent")

    chain.transfer_completed.assert_called_once_with(
        hashs="hash_transfer", downloader="qbittorrent"
    )
    backfill.assert_called_once()
    kwargs = backfill.call_args.kwargs
    assert kwargs["download_hash"] == "hash_transfer"
    assert kwargs["downloader"] == "qbittorrent"
    assert kwargs["save_path"] == Path("/downloads/movie")
    assert kwargs["org_string"] == "Demo.Show.2024.1080p"
