from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.chain.transfer import TransferChain
from app.db.models.downloadhistory import DownloadHistory


class FakeDownloadHistoryOper:
    """提供下载历史回查测试所需的内存桩。"""

    def __init__(
            self,
            histories_by_hash=None,
            histories_by_path=None,
            files_by_fullpath=None,
            files_by_savepath=None,
    ):
        """初始化各查询维度的测试数据。"""
        self.histories_by_hash = histories_by_hash or {}
        self.histories_by_path = histories_by_path or {}
        self.files_by_fullpath = files_by_fullpath or {}
        self.files_by_savepath = files_by_savepath or {}

    def get_by_hash(self, download_hash: str):
        """按下载哈希返回历史。"""
        return self.histories_by_hash.get(download_hash)

    def get_by_path(self, path: str):
        """按下载路径返回历史。"""
        return self.histories_by_path.get(path)

    def get_file_by_fullpath(self, fullpath: str):
        """按完整文件路径返回下载文件记录。"""
        return self.files_by_fullpath.get(fullpath)

    def get_files_by_savepath(self, savepath: str):
        """按保存路径返回下载文件记录。"""
        return self.files_by_savepath.get(savepath, [])


def _make_chain() -> TransferChain:
    """构造不启动后台线程的整理链实例。"""
    return object.__new__(TransferChain)


def _download_dir(**overrides):
    """构造下载目录配置桩。"""
    values = {
        "download_path": "/downloads",
        "media_type": None,
        "download_type_folder": False,
        "media_category": None,
        "download_category_folder": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_resolve_download_history_falls_back_to_parent_download_path():
    """文件记录缺失时应按种子父目录回查下载历史。"""
    expected = SimpleNamespace(download_hash="hash1", downloader="qb")
    oper = FakeDownloadHistoryOper(
        histories_by_hash={"hash1": expected},
        histories_by_path={"/downloads/season-pack": expected},
    )

    history = _make_chain()._resolve_download_history(
        downloadhis=oper,
        file_path=Path("/downloads/season-pack/Test.Show.S01E01.mkv"),
    )

    assert history is expected


def test_resolve_download_history_falls_back_to_unique_savepath_hash():
    """父目录只有一个下载哈希时应返回对应历史。"""
    expected = SimpleNamespace(download_hash="hash1", downloader="qb")
    oper = FakeDownloadHistoryOper(
        histories_by_hash={"hash1": expected},
        files_by_savepath={
            "/downloads/season-pack": [
                SimpleNamespace(download_hash="hash1"),
                SimpleNamespace(download_hash="hash1"),
            ]
        },
    )

    history = _make_chain()._resolve_download_history(
        downloadhis=oper,
        file_path=Path("/downloads/season-pack/subs/Test.Show.S01E01.zh.ass"),
    )

    assert history is expected


def test_resolve_download_history_skips_ambiguous_savepath_hashes():
    """父目录关联多个下载哈希时不应猜测下载历史。"""
    oper = FakeDownloadHistoryOper(
        histories_by_hash={
            "hash1": SimpleNamespace(download_hash="hash1", downloader="qb"),
            "hash2": SimpleNamespace(download_hash="hash2", downloader="tr"),
        },
        files_by_savepath={
            "/downloads/shared": [
                SimpleNamespace(download_hash="hash1"),
                SimpleNamespace(download_hash="hash2"),
            ]
        },
    )

    history = _make_chain()._resolve_download_history(
        downloadhis=oper,
        file_path=Path("/downloads/shared/Test.Show.S01E01.mkv"),
    )

    assert history is None


def test_resolve_download_history_stops_at_shared_download_root_path(monkeypatch):
    """共享下载根目录上的路径历史不应污染同级文件。"""
    oper = FakeDownloadHistoryOper(
        histories_by_path={
            "/downloads": SimpleNamespace(download_hash="hash1", downloader="qb")
        }
    )
    monkeypatch.setattr(
        "app.chain.transfer.DirectoryHelper.get_download_dirs",
        lambda _: [_download_dir()],
    )

    history = _make_chain()._resolve_download_history(
        downloadhis=oper,
        file_path=Path("/downloads/Ghost.Concert.mkv"),
    )

    assert history is None


def test_resolve_download_history_stops_at_shared_download_root_savepath(monkeypatch):
    """共享下载根目录上的其它文件记录不应污染当前文件。"""
    expected = SimpleNamespace(download_hash="hash1", downloader="qb")
    oper = FakeDownloadHistoryOper(
        histories_by_hash={"hash1": expected},
        files_by_savepath={
            "/downloads": [
                SimpleNamespace(
                    download_hash="hash1",
                    fullpath="/downloads/Other.Show.mkv",
                    filepath="Other.Show.mkv",
                ),
            ]
        },
    )
    monkeypatch.setattr(
        "app.chain.transfer.DirectoryHelper.get_download_dirs",
        lambda _: [_download_dir()],
    )

    history = _make_chain()._resolve_download_history(
        downloadhis=oper,
        file_path=Path("/downloads/Ghost.Concert.mkv"),
    )

    assert history is None


def test_resolve_download_history_accepts_shared_root_savepath_for_exact_file(monkeypatch):
    """共享根目录存在当前文件的明确记录时应允许命中。"""
    expected = SimpleNamespace(download_hash="hash1", downloader="qb")
    oper = FakeDownloadHistoryOper(
        histories_by_hash={"hash1": expected},
        files_by_savepath={
            "/downloads": [
                SimpleNamespace(
                    download_hash="hash1",
                    fullpath="/downloads/Ghost.Concert.mkv",
                    filepath="Ghost.Concert.mkv",
                ),
            ]
        },
    )
    monkeypatch.setattr(
        "app.chain.transfer.DirectoryHelper.get_download_dirs",
        lambda _: [_download_dir()],
    )

    history = _make_chain()._resolve_download_history(
        downloadhis=oper,
        file_path=Path("/downloads/Ghost.Concert.mkv"),
    )

    assert history is expected


def test_resolve_download_history_stops_at_type_category_download_root(monkeypatch):
    """按类型和类别生成的共享目录不应回查该目录自身的历史。"""
    oper = FakeDownloadHistoryOper(
        histories_by_path={
            "/downloads/电视剧/动漫": SimpleNamespace(
                download_hash="hash1", downloader="qb"
            )
        }
    )
    monkeypatch.setattr(
        "app.chain.transfer.DirectoryHelper.get_download_dirs",
        lambda _: [
            _download_dir(
                download_type_folder=True,
                download_category_folder=True,
            )
        ],
    )
    monkeypatch.setattr(
        "app.chain.transfer.MediaChain.media_category",
        lambda _: {"电影": [], "电视剧": ["动漫"]},
    )

    history = _make_chain()._resolve_download_history(
        downloadhis=oper,
        file_path=Path("/downloads/电视剧/动漫/Ghost.Concert.mkv"),
    )

    assert history is None


def test_get_shared_download_roots_includes_nested_category(monkeypatch):
    """多级分类的每一级目录都应成为共享下载边界。"""
    monkeypatch.setattr(
        "app.chain.transfer.DirectoryHelper.get_download_dirs",
        lambda _: [_download_dir(download_category_folder=True)],
    )
    monkeypatch.setattr(
        "app.chain.transfer.MediaChain.media_category",
        lambda _: {"电影": [], "电视剧": ["动漫/日本/季度新番"]},
    )

    roots = TransferChain._get_shared_download_roots(
        Path("/downloads/动漫/日本/季度新番/Show.S01E01.mkv")
    )

    assert roots == {
        "/downloads",
        "/downloads/动漫",
        "/downloads/动漫/日本",
        "/downloads/动漫/日本/季度新番",
    }


def test_get_shared_download_roots_excludes_torrent_subdirectory(monkeypatch):
    """分类目录下由种子创建的子目录不应成为共享下载边界。"""
    monkeypatch.setattr(
        "app.chain.transfer.DirectoryHelper.get_download_dirs",
        lambda _: [_download_dir(download_category_folder=True)],
    )
    monkeypatch.setattr(
        "app.chain.transfer.MediaChain.media_category",
        lambda _: {"电影": [], "电视剧": ["动漫/日本番剧"]},
    )

    roots = TransferChain._get_shared_download_roots(
        Path("/downloads/动漫/日本番剧/Torrent.Name/Show.S01E01.mkv")
    )

    assert "/downloads/动漫/日本番剧" in roots
    assert "/downloads/动漫/日本番剧/Torrent.Name" not in roots


def test_get_shared_download_roots_keeps_first_level_without_category_config(monkeypatch):
    """分类配置不可用时应保留原有的一级共享边界保护。"""
    monkeypatch.setattr(
        "app.chain.transfer.DirectoryHelper.get_download_dirs",
        lambda _: [_download_dir(download_category_folder=True)],
    )
    monkeypatch.setattr(
        "app.chain.transfer.MediaChain.media_category",
        lambda _: None,
    )

    roots = TransferChain._get_shared_download_roots(
        Path("/downloads/动漫/Torrent.Name/Show.S01E01.mkv")
    )

    assert roots == {"/downloads", "/downloads/动漫"}


def test_resolve_download_history_stops_at_nested_category_root(monkeypatch):
    """多级分类叶子目录中的其它任务历史不应污染当前文件。"""
    oper = FakeDownloadHistoryOper(
        histories_by_path={
            "/downloads/动漫/日本番剧": SimpleNamespace(
                download_hash="other-hash", downloader="qb"
            )
        }
    )
    monkeypatch.setattr(
        "app.chain.transfer.DirectoryHelper.get_download_dirs",
        lambda _: [_download_dir(download_category_folder=True)],
    )
    monkeypatch.setattr(
        "app.chain.transfer.MediaChain.media_category",
        lambda _: {"电影": [], "电视剧": ["动漫/日本番剧"]},
    )

    history = _make_chain()._resolve_download_history(
        downloadhis=oper,
        file_path=Path("/downloads/动漫/日本番剧/Ghost.Concert.mkv"),
    )

    assert history is None


def _make_downloadhistory_session():
    """构造内存 SQLite 会话，用于下载历史查询的单元测试，不触碰真实数据库。"""
    engine = sa.create_engine("sqlite://")
    DownloadHistory.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_get_by_mediaid_falls_back_to_title_year_when_tmdbid_null():
    """tmdbid 为空的下载历史应按标题+年份回退匹配，使豆瓣/Bangumi 订阅也能关联命中。

    这是本次修复的核心兜底：豆瓣/Bangumi 来源的订阅tmdbid为NULL，按tmdbid查询下载
    历史永远空，导致订阅详情页关联不到已下载/整理的文件（"下载了但信息丢失"）。当身份
    查询（tmdbid/doubanid 等）无结果且同时传入 title 与 year 时，应按标题+年份回退命中。
    """
    db = _make_downloadhistory_session()
    db.add(DownloadHistory(
        title="躲在超市后门抽烟的两人",
        year="2022",
        type="电影",
        path="/downloads/Torrent",
        tmdbid=None,
        doubanid=None,
        downloader="qb",
        download_hash="hash1",
        torrent_name="Torrent",
    ))
    db.commit()

    # 仅按空 tmdbid 查询时不应误命中（无身份、无标题回退）
    by_tmdb = DownloadHistory.get_by_mediaid(db, tmdbid=None)
    assert by_tmdb == []

    # 按标题+年份回退应命中同剧历史，使订阅能关联下载/整理文件
    by_title_year = DownloadHistory.get_by_mediaid(
        db, tmdbid=None, title="躲在超市后门抽烟的两人", year="2022"
    )
    assert len(by_title_year) == 1
    assert by_title_year[0].title == "躲在超市后门抽烟的两人"
    assert by_title_year[0].year == "2022"

    # 标题/年份不匹配时不应命中，避免跨剧污染
    no_match = DownloadHistory.get_by_mediaid(
        db, tmdbid=None, title="另一部电影", year="2020"
    )
    assert no_match == []
    db.close()
