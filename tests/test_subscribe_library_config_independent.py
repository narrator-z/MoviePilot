"""
订阅「文件统计」入库列 —— 配置无关回归测试。

验证工程师修复：``SubscribeChain.subscribe_files_info`` 的"入库"列不再依赖
媒体库目录配置（旧路径经 ``media_files`` -> ``get_dest_dir`` 按目录设置计算扫描根），
改为**直接基于 ``TransferHistory`` 真实落盘记录**提取文件路径与存储。

全部为纯单元（mock）测试，不连接真实数据库 / 网络（conftest 已隔离 CONFIG_DIR 并
加载网络守卫）。

覆盖场景：
- 场景1（核心·电视剧）：入库列从 TransferHistory 取到真实路径与存储。
- 场景2（目录配置无关·关键）：故意把目录配置置错/置空，入库列仍来自 TransferHistory。
- 场景3（files 形态兼容）：files 为字符串路径列表、对象列表时仍能正确提取。
- 场景4（files 空回退 dest）：files=[] 回退 dest；dest 为目录且无集号时不报错。
- 场景5（电影）：type=电影 订阅，episodes[0].library 含真实 dest 路径。
- 场景6（季过滤 & 无 dest 跳过）：季不匹配 / dest 为空 的记录被跳过。
- 场景7（回归·下载列不受影响）：下载列仍走 get_files_by_hash。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

# 必须在 import app 之前完成后端引导（隔离 CONFIG_DIR / 建表 / 网络守卫）。
from tests.conftest import prepare_backend

prepare_backend()

import app.chain.subscribe as subscribe_module  # noqa: E402
from app.chain.subscribe import SubscribeChain  # noqa: E402
from app.db.models.subscribe import Subscribe  # noqa: E402
from app.schemas.types import MediaType  # noqa: E402


# --------------------------------------------------------------------------- #
# 构造助手
# --------------------------------------------------------------------------- #
def _make_subscribe(mtype: str, tmdbid: int = 123, season: int = None,
                    name: str = "剧", total_episode: int = 4,
                    start_episode: int = 1):
    """构造 DB 层 Subscribe（含 to_dict，供 subscribe_files_info 末端重建 schema）。"""
    return Subscribe(
        name=name,
        type=mtype,
        tmdbid=tmdbid,
        season=season,
        total_episode=total_episode,
        start_episode=start_episode,
    )


def _make_download_history(download_hash: str, torrent_name: str = "剧 2024",
                           torrent_site: str = "TestSite"):
    return SimpleNamespace(
        download_hash=download_hash,
        torrent_name=torrent_name,
        torrent_site=torrent_site,
    )


def _make_download_file(filepath: str, fullpath: str = None,
                        downloader: str = "qbittorrent"):
    return SimpleNamespace(
        filepath=filepath,
        fullpath=fullpath or filepath,
        downloader=downloader,
    )


def _make_transfer(download_hash: str, dest: str, dest_storage: str = "local",
                   files=None):
    """构造一条整理记录。dest/files/dest_storage/download_hash 即被新逻辑读取的字段。"""
    return SimpleNamespace(
        download_hash=download_hash,
        dest=dest,
        dest_storage=dest_storage,
        files=files,
    )


def _episodes(*nums):
    """构造 TmdbChain.tmdb_episodes 的伪装返回值。

    需覆盖 subscribe_files_info 实际读取的属性：name / episode_number /
    overview / still_path（对应源码 3556-3558 行）。
    """
    return [
        SimpleNamespace(name=f"E{n}", episode_number=n, overview="", still_path=None)
        for n in nums
    ]


class _FakeDownloadOper:
    """伪装 DownloadHistoryOper：记录调用、回放预设的下载历史与下载文件。"""

    def __init__(self, histories, download_files=None):
        self._histories = list(histories)
        self._download_files = list(download_files or [])
        self.get_by_mediaid_called = False
        self.get_files_by_hash_called = 0
        self.last_hash = None

    def get_by_mediaid(self, tmdbid=None, doubanid=None, bangumiid=None, anilistid=None, media_source=None, media_id=None, title=None, year=None, **kwargs):
        self.get_by_mediaid_called = True
        return self._histories

    def get_files_by_hash(self, download_hash, state=None):
        self.get_files_by_hash_called += 1
        self.last_hash = download_hash
        return self._download_files


class _FakeTransferOper:
    """伪装 TransferHistoryOper：按 download_hash 回放整理记录。"""

    def __init__(self, by_hash=None):
        self._by_hash = dict(by_hash or {})
        self.list_by_hash_called = 0

    def list_by_hash(self, download_hash):
        self.list_by_hash_called += 1
        return self._by_hash.get(download_hash, [])


def _setup(monkeypatch, *, histories, download_files=None, transfers_by_hash=None,
           tmdb_episodes=None):
    """装配 SubscribeChain 与全部外部依赖 mock，返回 (chain, download_oper, transfer_oper)。"""
    download_oper = _FakeDownloadOper(histories, download_files)
    transfer_oper = _FakeTransferOper(transfers_by_hash or {})

    monkeypatch.setattr(subscribe_module, "DownloadHistoryOper", lambda: download_oper)
    monkeypatch.setattr(subscribe_module, "TransferHistoryOper", lambda: transfer_oper)

    fake_tmdb = MagicMock()
    fake_tmdb.tmdb_episodes.return_value = list(tmdb_episodes or [])
    monkeypatch.setattr(subscribe_module, "TmdbChain", lambda: fake_tmdb)

    # 绕过真实媒体识别（不连网、不查 TMDB）。v3 的 subscribe_files_info 经
    # MediaChain().recognize_media 识别，故直接替换 MediaChain 让其返回真值。
    fake_media = MagicMock()
    fake_media.recognize_media.return_value = MagicMock()
    monkeypatch.setattr(subscribe_module, "MediaChain", lambda: fake_media)

    chain = SubscribeChain.__new__(SubscribeChain)
    return chain, download_oper, transfer_oper


# --------------------------------------------------------------------------- #
# 依赖存在性守卫（锁定修复的关键 import）
# --------------------------------------------------------------------------- #
def test_transferhistory_oper_is_imported():
    """修复点：subscribe.py 必须已经 import TransferHistoryOper。"""
    assert hasattr(subscribe_module, "TransferHistoryOper"), \
        "subscribe.py 未导入 TransferHistoryOper，修复可能未应用"


# --------------------------------------------------------------------------- #
# 场景1：核心·电视剧入库列从 TransferHistory 取真实路径与存储
# --------------------------------------------------------------------------- #
def test_scenario1_tv_library_from_transferhistory(monkeypatch):
    sub = _make_subscribe(MediaType.TV.value, tmdbid=123, season=1, name="剧")
    history = _make_download_history("ABC")
    real_path = "/media/shows/电视剧/日韩剧/剧/Season 1/剧.S01E01.mkv"
    transfer = _make_transfer(
        download_hash="ABC",
        dest=real_path,
        dest_storage="local",
        files=[{"path": real_path}],
    )

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        transfers_by_hash={"ABC": [transfer]},
        tmdb_episodes=_episodes(1, 2, 3, 4),
    )

    result = chain.subscribe_files_info(sub)
    assert result is not None

    library = result.episodes[1].library
    assert library, "电视剧 S01E01 的入库列应为非空"
    assert library[0].file_path == real_path
    assert library[0].storage == "local"
    # 证明实现确实查询了 TransferHistory
    assert tr.list_by_hash_called == 1


# --------------------------------------------------------------------------- #
# 场景2：目录配置无关（关键价值点）—— 配置错误/为空，入库列仍来自 TransferHistory
# --------------------------------------------------------------------------- #
def test_scenario2_library_independent_of_directory_config(monkeypatch):
    # 故意把媒体库目录配置制造为"错误/空"，模拟配置与实盘不符。
    fake_dir = MagicMock()
    fake_dir.get_library_dirs.return_value = []
    fake_dir.get_dest_dir.return_value = None
    monkeypatch.setattr("app.helper.directory.DirectoryHelper", fake_dir)

    sub = _make_subscribe(MediaType.TV.value, tmdbid=123, season=1, name="剧")
    history = _make_download_history("ABC")
    real_path = "/media/shows/电视剧/日韩剧/剧/Season 1/剧.S01E01.mkv"
    transfer = _make_transfer(
        download_hash="ABC",
        dest=real_path,
        dest_storage="local",
        files=[{"path": real_path}],
    )

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        transfers_by_hash={"ABC": [transfer]},
        tmdb_episodes=_episodes(1, 2, 3, 4),
    )

    result = chain.subscribe_files_info(sub)

    # 即便目录配置为空/错误，入库列仍基于 TransferHistory 真实记录
    assert result.episodes[1].library
    assert result.episodes[1].library[0].file_path == real_path
    assert result.episodes[1].library[0].storage == "local"
    # 证明实现根本没有去查阅目录配置（旧实现才会依赖它）
    fake_dir.assert_not_called()


# --------------------------------------------------------------------------- #
# 场景3：files 形态兼容（字符串列表 / 对象列表）
# --------------------------------------------------------------------------- #
def test_scenario3_files_as_string_list(monkeypatch):
    sub = _make_subscribe(MediaType.TV.value, tmdbid=123, season=1, name="剧")
    history = _make_download_history("ABC")
    files = [
        "/media/shows/电视剧/日韩剧/剧/Season 1/剧.S01E02.mkv",
        "/media/shows/电视剧/日韩剧/剧/Season 1/剧.S01E03.mkv",
    ]
    transfer = _make_transfer(
        download_hash="ABC",
        dest="/media/shows/电视剧/日韩剧/剧/Season 1/剧.S01E02.mkv",
        dest_storage="local",
        files=files,
    )

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        transfers_by_hash={"ABC": [transfer]},
        tmdb_episodes=_episodes(1, 2, 3, 4),
    )

    result = chain.subscribe_files_info(sub)
    assert result.episodes[2].library[0].file_path == files[0]
    assert result.episodes[3].library[0].file_path == files[1]


def test_scenario3b_files_as_object_list(monkeypatch):
    """files 为带 .path 属性的对象列表时，用 getattr(f, 'path', None) 提取。"""

    class _FileItem:
        def __init__(self, path: str):
            self.path = path

    sub = _make_subscribe(MediaType.TV.value, tmdbid=123, season=1, name="剧")
    history = _make_download_history("ABC")
    obj = _FileItem("/media/shows/电视剧/日韩剧/剧/Season 1/剧.S01E05.mkv")
    transfer = _make_transfer(
        download_hash="ABC",
        dest="/media/shows/电视剧/日韩剧/剧/Season 1/剧.S01E05.mkv",
        dest_storage="remote",
        files=[obj],
    )

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        transfers_by_hash={"ABC": [transfer]},
        tmdb_episodes=_episodes(1, 2, 3, 4, 5),
    )

    result = chain.subscribe_files_info(sub)
    assert result.episodes[5].library[0].file_path == obj.path
    assert result.episodes[5].library[0].storage == "remote"


# --------------------------------------------------------------------------- #
# 场景4：files 空回退 dest；dest 为目录且无集号时不得抛异常
# --------------------------------------------------------------------------- #
def test_scenario4_files_empty_fallback_to_dest(monkeypatch):
    sub = _make_subscribe(MediaType.TV.value, tmdbid=123, season=1, name="剧")
    history = _make_download_history("ABC")
    real_file = "/media/shows/电视剧/日韩剧/剧/Season 1/剧.S01E04.mkv"
    season_dir = "/media/shows/剧/Season 1"  # 目录形态，MetaInfo 取不到集号
    # transfer1：files=[] -> 回退 dest（真实文件，映射到 ep4）
    # transfer2：files=[] + dest 为目录 -> 不报错、不映射具体集
    transfers = [
        _make_transfer(download_hash="ABC", dest=real_file, dest_storage="local", files=[]),
        _make_transfer(download_hash="ABC", dest=season_dir, dest_storage="local", files=[]),
    ]

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        transfers_by_hash={"ABC": transfers},
        tmdb_episodes=_episodes(1, 2, 3, 4),
    )

    result = chain.subscribe_files_info(sub)  # 不因 season_dir 抛异常
    assert result.episodes[4].library, "files=[] 时应回退到 dest"
    assert result.episodes[4].library[0].file_path == real_file


# --------------------------------------------------------------------------- #
# 场景5：电影订阅，episodes[0].library 含真实 dest 路径
# --------------------------------------------------------------------------- #
def test_scenario5_movie_library(monkeypatch):
    sub = _make_subscribe(MediaType.MOVIE.value, tmdbid=456, name="电影")
    history = _make_download_history("MOV")
    real_path = "/media/movies/电影/电影.2024.1080p.mkv"
    transfer = _make_transfer(
        download_hash="MOV",
        dest=real_path,
        dest_storage="local",
        files=[{"path": real_path}],
    )

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        transfers_by_hash={"MOV": [transfer]},
        tmdb_episodes=[],  # 电影分支不查 tmdb_episodes
    )

    result = chain.subscribe_files_info(sub)
    assert result.episodes[0].library, "电影入库列应为非空"
    assert result.episodes[0].library[0].file_path == real_path
    assert result.episodes[0].library[0].storage == "local"


# --------------------------------------------------------------------------- #
# 场景6：季过滤 & 无 dest 跳过
# --------------------------------------------------------------------------- #
def test_scenario6_season_filter(monkeypatch):
    # 订阅 season=2；S01 的整理记录应被季过滤跳过，S02 的应保留。
    sub = _make_subscribe(MediaType.TV.value, tmdbid=123, season=2, name="剧")
    history = _make_download_history("ABC")
    s01 = _make_transfer(
        download_hash="ABC",
        dest="/media/shows/电视剧/剧/Season 1/剧.S01E01.mkv",
        dest_storage="local",
        files=[{"path": "/media/shows/电视剧/剧/Season 1/剧.S01E01.mkv"}],
    )
    s02 = _make_transfer(
        download_hash="ABC",
        dest="/media/shows/电视剧/剧/Season 2/剧.S02E01.mkv",
        dest_storage="local",
        files=[{"path": "/media/shows/电视剧/剧/Season 2/剧.S02E01.mkv"}],
    )

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        transfers_by_hash={"ABC": [s01, s02]},
        tmdb_episodes=_episodes(1, 2),
    )

    result = chain.subscribe_files_info(sub)
    # 季过滤验证：S01 记录被跳过，episodes[1] 仅保留 S02 记录（不含 S01）。
    # 两条记录集号均为 1，若季过滤失效则会同时出现 2 条。
    assert len(result.episodes[1].library) == 1
    assert result.episodes[1].library[0].file_path.endswith("剧.S02E01.mkv")


def test_scenario6_skip_empty_dest(monkeypatch):
    # dest 为空的整理记录应被跳过；同批有效记录仍正常映射，且不抛异常。
    sub = _make_subscribe(MediaType.TV.value, tmdbid=123, season=1, name="剧")
    history = _make_download_history("ABC")
    empty_dest = _make_transfer(download_hash="ABC", dest="", dest_storage="local", files=[])
    valid = _make_transfer(
        download_hash="ABC",
        dest="/media/shows/电视剧/剧/Season 1/剧.S01E01.mkv",
        dest_storage="local",
        files=[{"path": "/media/shows/电视剧/剧/Season 1/剧.S01E01.mkv"}],
    )

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        transfers_by_hash={"ABC": [empty_dest, valid]},
        tmdb_episodes=_episodes(1),
    )

    result = chain.subscribe_files_info(sub)
    # 仅 1 条有效记录映射到 episode 1
    assert len(result.episodes[1].library) == 1
    assert result.episodes[1].library[0].file_path.endswith("剧.S01E01.mkv")


# --------------------------------------------------------------------------- #
# 场景7：回归·下载列仍走 get_files_by_hash（本修复未触碰它）
# --------------------------------------------------------------------------- #
def test_scenario7_download_column_uses_get_files_by_hash(monkeypatch):
    sub = _make_subscribe(MediaType.TV.value, tmdbid=123, season=1, name="剧")
    history = _make_download_history("ABC")
    download_file = _make_download_file("剧.S01E01.1080p.mkv")
    transfer = _make_transfer(
        download_hash="ABC",
        dest="/media/shows/电视剧/剧/Season 1/剧.S01E01.mkv",
        dest_storage="local",
        files=[{"path": "/media/shows/电视剧/剧/Season 1/剧.S01E01.mkv"}],
    )

    chain, dl, tr = _setup(
        monkeypatch,
        histories=[history],
        download_files=[download_file],
        transfers_by_hash={"ABC": [transfer]},
        tmdb_episodes=_episodes(1),
    )

    result = chain.subscribe_files_info(sub)

    # 下载列仍有数据（走 get_files_by_hash 分支）
    assert result.episodes[1].download, "下载列不应受入库修复影响"
    assert result.episodes[1].download[0].file_path == "剧.S01E01.1080p.mkv"
    # 证明本次调用确实查询了下载文件明细
    assert dl.get_files_by_hash_called >= 1
    assert dl.last_hash == "ABC"
