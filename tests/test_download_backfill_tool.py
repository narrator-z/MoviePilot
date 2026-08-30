"""
下载文件历史回填工具（app.chain.download_backfill）单元测试。

验证旧版容器“下载”列恒为 0 的回填修复：
- 对缺明细的下载记录，从下载器回查文件清单并幂等写入 downloadfiles（仅媒体/字幕/音频）。
- 已存在明细的记录跳过；无 Hash / 无保存路径的记录跳过。
- downloadhistory 未记下载器时，回退遍历全部已启用下载器。
- 下载器异常/无清单时不抛异常、不阻断主流程、不写入。

全部为纯单元（mock）测试，不依赖真实下载器与数据库。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import app.chain.download_backfill as backfill_module
from app.chain.download_backfill import DownloadBackfill, backfill_download_files


class _FakeBackfillOper:
    """捕获 downloadfiles 写入，并模拟幂等检查（get_files_by_hash）与历史分页迭代。"""

    def __init__(self):
        self._records: list = []
        self._files_by_hash: dict = {}
        self.added_files: list = []

    def set_histories(self, records):
        self._records = list(records)

    def set_existing(self, download_hash, items):
        self._files_by_hash[download_hash] = list(items)

    def list_by_page(self, page: int = 1, count: int = 200):
        start = (page - 1) * count
        return self._records[start:start + count]

    def get_files_by_hash(self, download_hash, state=None):
        return list(self._files_by_hash.get(download_hash, []))

    def add_files(self, file_items):
        self.added_files.extend(file_items)
        for item in file_items:
            self._files_by_hash.setdefault(item["download_hash"], []).append(item)


class _FakeDownloaderHelper:
    """模拟已启用下载器列表（仅名称参与回填回退逻辑）。"""

    def __init__(self, names):
        self._names = names

    def get_configs(self):
        return {name: object() for name in self._names}


def _history(download_hash, path="/downloads/movie", downloader="qbittorrent",
             torrent_name="Demo.Show.2024.1080p"):
    return SimpleNamespace(
        download_hash=download_hash,
        path=path,
        downloader=downloader,
        torrent_name=torrent_name,
    )


def _patch_oper(monkeypatch, oper):
    monkeypatch.setattr(backfill_module, "DownloadHistoryOper", lambda: oper)
    return oper


def _patch_helper(monkeypatch, names):
    monkeypatch.setattr(backfill_module, "DownloaderHelper", lambda: _FakeDownloaderHelper(names))


def test_backfill_writes_media_files_only(monkeypatch):
    """场景1：记录有 downloader，torrent_files 返回清单 → 仅媒体文件被写入、state=1。"""
    oper = _FakeBackfillOper()
    oper.set_histories([_history("hash_001")])
    _patch_oper(monkeypatch, oper)
    _patch_helper(monkeypatch, ["qbittorrent", "transmission"])

    monkeypatch.setattr(
        DownloadBackfill,
        "torrent_files",
        MagicMock(return_value=[
            SimpleNamespace(name="Demo.Show.S01E01.1080p.mp4"),
            SimpleNamespace(name="Demo.Show.S01E02.1080p.mkv"),
            SimpleNamespace(name="Demo.Show.S01E01.sample.txt"),
            SimpleNamespace(name="Demo.Show.nfo"),
        ]),
    )

    stats = backfill_download_files()

    assert stats["scanned"] == 1
    assert stats["written"] == 1
    assert stats["files_written"] == 2
    assert len(oper.added_files) == 2
    for rec in oper.added_files:
        assert rec["download_hash"] == "hash_001"
        assert rec["downloader"] == "qbittorrent"
        assert rec["state"] == 1
        assert rec["savepath"] == "/downloads/movie"
        assert rec["fullpath"] == f"/downloads/movie/{rec['filepath']}"
        assert rec["filepath"] in ("Demo.Show.S01E01.1080p.mp4", "Demo.Show.S01E02.1080p.mkv")


def test_backfill_skips_existing_idempotent(monkeypatch):
    """场景2：同一 hash 已存在 downloadfiles 明细 → 跳过且不回查下载器。"""
    oper = _FakeBackfillOper()
    oper.set_histories([_history("hash_002")])
    oper.set_existing("hash_002", [SimpleNamespace(id=1)])
    _patch_oper(monkeypatch, oper)
    _patch_helper(monkeypatch, ["qbittorrent"])

    torrent_files = MagicMock(return_value=[SimpleNamespace(name="x.mp4")])
    monkeypatch.setattr(DownloadBackfill, "torrent_files", torrent_files)

    stats = backfill_download_files()

    assert stats["skipped_existing"] == 1
    assert stats["written"] == 0
    assert oper.added_files == []
    torrent_files.assert_not_called()


def test_backfill_skips_no_hash(monkeypatch):
    """场景3：无 download_hash 的记录 → 跳过，不回查下载器。"""
    oper = _FakeBackfillOper()
    oper.set_histories([_history("")])
    _patch_oper(monkeypatch, oper)
    _patch_helper(monkeypatch, ["qbittorrent"])

    torrent_files = MagicMock(return_value=[SimpleNamespace(name="x.mp4")])
    monkeypatch.setattr(DownloadBackfill, "torrent_files", torrent_files)

    stats = backfill_download_files()

    assert stats["skipped_no_hash"] == 1
    assert oper.added_files == []
    torrent_files.assert_not_called()


def test_backfill_falls_back_to_enabled_downloaders(monkeypatch):
    """场景4：记录未记下载器 → 回退遍历已启用下载器，命中即写入。"""
    oper = _FakeBackfillOper()
    oper.set_histories([_history("hash_004", downloader="")])
    _patch_oper(monkeypatch, oper)
    _patch_helper(monkeypatch, ["qbittorrent", "transmission"])

    # 第一个下载器无此种子，第二个下载器返回清单
    torrent_files = MagicMock(side_effect=[
        None,
        [SimpleNamespace(name="Demo.Show.S01E01.1080p.mkv")],
    ])
    monkeypatch.setattr(DownloadBackfill, "torrent_files", torrent_files)

    stats = backfill_download_files()

    assert stats["written"] == 1
    assert stats["files_written"] == 1
    assert oper.added_files[0]["downloader"] == "transmission"
    # 仅第二个下载器被实际调用并返回清单
    assert torrent_files.call_count == 2


def test_backfill_silent_on_downloader_error(monkeypatch):
    """场景5：下载器抛异常 / 返回 None → 计入失败、不抛异常、不写入。"""
    oper = _FakeBackfillOper()
    oper.set_histories([
        _history("hash_0501"),
        _history("hash_0502"),
        _history("hash_0503"),
    ])
    _patch_oper(monkeypatch, oper)
    _patch_helper(monkeypatch, ["qbittorrent"])

    # 第1条抛异常，第2条返回 None，第3条无候选（无下载器）
    torrent_files = MagicMock(side_effect=[
        RuntimeError("downloader unreachable"),
        None,
    ])
    monkeypatch.setattr(DownloadBackfill, "torrent_files", torrent_files)

    # 让第3条无可用下载器：清空已启用列表
    _patch_helper(monkeypatch, [])

    stats = backfill_download_files()

    assert stats["failed"] == 2  # 抛异常 + 返回 None
    assert stats["skipped_no_downloader"] == 1  # 无下载器
    assert oper.added_files == []
    assert stats["written"] == 0


def test_backfill_is_repeatable(monkeypatch):
    """场景6：同一数据集二次回填应全部跳过（幂等），不再写入。"""
    oper = _FakeBackfillOper()
    oper.set_histories([_history("hash_006")])
    _patch_oper(monkeypatch, oper)
    _patch_helper(monkeypatch, ["qbittorrent"])

    monkeypatch.setattr(
        DownloadBackfill,
        "torrent_files",
        MagicMock(return_value=[SimpleNamespace(name="Demo.Show.S01E01.1080p.mp4")]),
    )

    first = backfill_download_files()
    assert first["written"] == 1

    # 第二次运行：首轮写入的明细已存在，应全部跳过
    second = backfill_download_files()
    assert second["skipped_existing"] == 1
    assert second["written"] == 0
    # 文件总数不应因二次运行而增加
    assert len(oper.added_files) == 1


def test_backfill_respects_limit(monkeypatch):
    """场景7：limit 限制扫描数量，超出部分不被处理。"""
    oper = _FakeBackfillOper()
    oper.set_histories([
        _history("hash_0701"),
        _history("hash_0702"),
        _history("hash_0703"),
    ])
    _patch_oper(monkeypatch, oper)
    _patch_helper(monkeypatch, ["qbittorrent"])

    monkeypatch.setattr(
        DownloadBackfill,
        "torrent_files",
        MagicMock(return_value=[SimpleNamespace(name="a.mp4")]),
    )

    stats = backfill_download_files(limit=2)

    assert stats["scanned"] == 2
    assert stats["written"] == 2


def _history_with_note(download_hash, note, path="/downloads/movie", downloader="qbittorrent",
                       torrent_name="Demo.Show.2024.1080p"):
    """构造带 note 字段的下载历史记录（用于验证来源过滤）。"""
    return SimpleNamespace(
        download_hash=download_hash,
        path=path,
        downloader=downloader,
        torrent_name=torrent_name,
        note=note,
    )


def test_backfill_filters_invalid_source(monkeypatch):
    """场景8：note 缺少有效 source 的记录被跳过，不回查下载器、不写入；
    note 为空或非空且含 source 的记录视为有效来源、参与回填。"""
    oper = _FakeBackfillOper()
    oper.set_histories([
        _history_with_note("hash_valid", {"source": "Subscribe|abc"}),
        _history_with_note("hash_invalid", {"type": "Other"}),  # dict 但无 source
        _history_with_note("hash_non_dict", "raw-string-note"),  # 非 dict 视为无效来源
        _history("hash_no_note"),  # 无 note 视为有效来源
    ])
    _patch_oper(monkeypatch, oper)
    _patch_helper(monkeypatch, ["qbittorrent"])

    torrent_files = MagicMock(return_value=[SimpleNamespace(name="Demo.Show.S01E01.1080p.mp4")])
    monkeypatch.setattr(DownloadBackfill, "torrent_files", torrent_files)

    stats = backfill_download_files()

    # 仅 hash_valid / hash_no_note 参与回填
    assert stats["skipped_invalid_source"] == 2
    assert stats["written"] == 2
    assert stats["files_written"] == 2
    # 仅有效来源触发回查（2 次），无效来源不触达下载器
    assert torrent_files.call_count == 2
    for rec in oper.added_files:
        assert rec["download_hash"] in ("hash_valid", "hash_no_note")
