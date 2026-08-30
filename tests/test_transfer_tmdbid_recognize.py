"""整理识别默认带 tmdbid、无 ID 才 fallback 文件名,并复用同 tmdbid 标题/类别保证目录一致。

这些测试只验证 TransferChain 的识别 ID 收集逻辑与一致性护栏,不触发真实网络/DB。
"""
from types import SimpleNamespace

from app.core.context import MediaInfo

from app.chain.transfer import TransferChain
from app.schemas.types import MediaType


def _make_chain():
    """构造一个不依赖完整 DI 的 TransferChain 实例。"""
    chain = object.__new__(TransferChain)
    chain.jobview = SimpleNamespace(
        migrate_task=lambda _t: False,
        running_task=lambda _t: None,
        remove_task=lambda _t: None,
        try_remove_job=lambda _t: None,
    )
    return chain


def _patch_subscribe(monkeypatch, subs):
    """将 SubscribeOper().list() 替换为返回给定订阅列表。"""

    class _FakeSubscribeOper:
        def list(self):
            return subs

    monkeypatch.setattr(
        "app.db.subscribe_oper.SubscribeOper",
        _FakeSubscribeOper,
    )


def test_resolve_subscription_tmdbid_matches_by_name(monkeypatch) -> None:
    """给定 task.meta.name 与订阅列表(名称匹配),应返回该订阅的 tmdbid。"""
    chain = _make_chain()
    _patch_subscribe(
        monkeypatch,
        [SimpleNamespace(name="金特务：本色回归", tmdbid=296206)],
    )
    task = SimpleNamespace(
        mediainfo=SimpleNamespace(tmdb_id=None),
        meta=SimpleNamespace(name="金特务：本色回归 (2026)"),
    )

    tmdbid = chain._resolve_subscription_tmdbid(task, None)

    assert tmdbid == 296206


def test_resolve_subscription_tmdbid_no_match_returns_none(monkeypatch) -> None:
    """无任何匹配订阅时应返回 None。"""
    chain = _make_chain()
    _patch_subscribe(monkeypatch, [])
    task = SimpleNamespace(
        mediainfo=SimpleNamespace(tmdb_id=None),
        meta=SimpleNamespace(name="Unknown Show 2026"),
    )

    assert chain._resolve_subscription_tmdbid(task, None) is None


def test_collect_recognize_ids_falls_back_to_subscription(monkeypatch) -> None:
    """download_history 无 ID 但订阅可匹配时,应返回带 tmdbid 的 dict。"""
    chain = _make_chain()
    _patch_subscribe(
        monkeypatch,
        [SimpleNamespace(name="金特务：本色回归", tmdbid=296206)],
    )
    task = SimpleNamespace(
        mediainfo=SimpleNamespace(tmdb_id=None),
        meta=SimpleNamespace(name="金特务：本色回归 (2026)"),
    )
    download_history = SimpleNamespace(
        media_id=None,
        tmdbid=None,
        doubanid=None,
        bangumiid=None,
        anilistid=None,
        media_source=None,
        episode_group=None,
        title=None,
    )

    ids = chain._collect_recognize_ids(task, download_history)

    assert ids == {"media_source": "themoviedb", "media_id": "296206"}


def test_collect_recognize_ids_prefers_download_history_id() -> None:
    """download_history 带有 tmdbid 时,应原样返回其 id dict,不动用订阅兜底。"""
    chain = _make_chain()
    download_history = SimpleNamespace(
        media_id=None,
        tmdbid=296206,
        doubanid=None,
        bangumiid=None,
        anilistid=None,
        media_source="themoviedb",
        episode_group=None,
        title=None,
    )

    ids = chain._collect_recognize_ids(None, download_history)

    assert ids == {
        "media_source": "themoviedb",
        "media_id": None,
        "episode_group": None,
    }


def test_collect_recognize_ids_no_id_returns_none(monkeypatch) -> None:
    """download_history 与订阅都拿不到 ID 时,应返回 None(走文件名兜底)。"""
    chain = _make_chain()
    _patch_subscribe(monkeypatch, [])
    task = SimpleNamespace(
        mediainfo=SimpleNamespace(tmdb_id=None),
        meta=SimpleNamespace(name="Unknown Show 2026"),
    )
    download_history = SimpleNamespace(
        media_id=None,
        tmdbid=None,
        doubanid=None,
        bangumiid=None,
        anilistid=None,
        media_source=None,
        episode_group=None,
        title=None,
    )

    assert chain._collect_recognize_ids(task, download_history) is None


def test_consistency_guard_reuses_title_and_category(monkeypatch) -> None:
    """同 tmdbid 的整理历史存在时,mediainfo 的标题与类别应被复用,保证目录一致。"""
    chain = _make_chain()
    history = SimpleNamespace(title="金特务：本色回归", category="日韩剧")
    fake_transferhis = SimpleNamespace(
        get_by_media_identity=lambda **_kw: history,
    )
    fake_media = SimpleNamespace(
        supplement_tmdb_info=lambda media, _meta: media,
    )
    monkeypatch.setattr(
        "app.chain.transfer.TransferHistoryOper",
        lambda: fake_transferhis,
    )
    monkeypatch.setattr(
        "app.chain.transfer.MediaChain",
        lambda: fake_media,
    )
    mediainfo = MediaInfo(
        tmdb_id=296206,
        type=MediaType.MOVIE,
        title="Agent Kim Reactivated",
    )
    task = SimpleNamespace(
        mediainfo=mediainfo,
        meta=SimpleNamespace(name="Agent Kim Reactivated 2026"),
        fileitem=SimpleNamespace(name="f.mkv", path="/downloads/f.mkv"),
        target_directory=SimpleNamespace(library_storage="local"),
        transfer_batch_id=None,
        library_category_folder=False,
        preview=True,
    )

    # 触发整理(在护栏之后因 migrate_task 返回 False 提前返回,不触碰真实整理)
    chain._TransferChain__handle_transfer(task)

    assert task.mediainfo.title == "金特务：本色回归"
    assert task.mediainfo.category == "日韩剧"


def test_consistency_guard_preserves_custom_category(monkeypatch) -> None:
    """当 mediainfo 已带自定义 category 时,护栏不应用历史 category 覆盖它(边角 1a)。"""
    chain = _make_chain()
    history = SimpleNamespace(title="金特务：本色回归", category="日韩剧")
    fake_transferhis = SimpleNamespace(
        get_by_media_identity=lambda **_kw: history,
    )
    fake_media = SimpleNamespace(
        supplement_tmdb_info=lambda media, _meta: media,
    )
    monkeypatch.setattr(
        "app.chain.transfer.TransferHistoryOper",
        lambda: fake_transferhis,
    )
    monkeypatch.setattr(
        "app.chain.transfer.MediaChain",
        lambda: fake_media,
    )
    mediainfo = MediaInfo(
        tmdb_id=296206,
        type=MediaType.MOVIE,
        title="Agent Kim Reactivated",
    )
    # 模拟用户已在 download_history.media_category 设置自定义类别
    mediainfo.category = "我的自定义类别"
    task = SimpleNamespace(
        mediainfo=mediainfo,
        meta=SimpleNamespace(name="Agent Kim Reactivated 2026"),
        fileitem=SimpleNamespace(name="f.mkv", path="/downloads/f.mkv"),
        target_directory=SimpleNamespace(library_storage="local"),
        transfer_batch_id=None,
        library_category_folder=False,
        preview=True,
    )

    chain._TransferChain__handle_transfer(task)

    # 标题仍复用历史(一致),但自定义类别必须被保留,不被历史 category 覆盖
    assert task.mediainfo.title == "金特务：本色回归"
    assert task.mediainfo.category == "我的自定义类别"
