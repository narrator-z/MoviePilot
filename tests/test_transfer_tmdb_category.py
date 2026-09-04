import threading
from types import SimpleNamespace
from unittest.mock import Mock

from app.application.transfer.workflow import TransferTask
from app.chain.transfer.facade import TransferChain
from app.domain.context import MediaInfo
from app.domain.metainfo import MetaInfo
from app.schemas.file import FileItem
from app.schemas.system import TransferDirectoryConf
from app.schemas.types import MediaSource, MediaType


def test_transfer_rejects_partial_or_invalid_explicit_identity() -> None:
    """整理公共入口不得把半套身份或格式非法来源传入后台任务。"""
    chain = object.__new__(TransferChain)
    fileitem = FileItem(
        storage="local",
        path="/downloads/Test.Movie.2024.mkv",
        type="file",
    )

    partial_state, partial_message = chain.do_transfer(
        fileitem=fileitem,
        media_source=MediaSource.TMDB,
    )
    invalid_state, invalid_message = chain.do_transfer(
        fileitem=fileitem,
        media_source="plugin source:invalid",
        media_id="1234",
    )

    assert not partial_state
    assert "media_source" in partial_message
    assert not invalid_state
    assert "media_source" in invalid_message


def test_transfer_resolves_complete_identity_before_building_tasks(monkeypatch) -> None:
    """整理入口收到完整身份时应精确识别，失败后不得退化为标题识别。"""
    chain = object.__new__(TransferChain)
    fileitem = FileItem(
        storage="local",
        path="/downloads/Test.Movie.2024.mkv",
        type="file",
    )
    recognize = monkeypatch.setattr
    calls = []

    class FakeMediaChain:
        """记录精确识别参数并返回空结果。"""

        def recognize_media(self, **kwargs):
            """模拟显式身份识别失败。"""
            calls.append(kwargs)
            return None

    recognize("app.chain.transfer.workflow.MediaChain", FakeMediaChain)

    state, message = chain.do_transfer(
        fileitem=fileitem,
        media_source="tmdb",
        media_id="1234",
        mtype=MediaType.MOVIE,
    )

    assert not state
    assert "未识别到媒体信息" in message
    assert calls == [{
        "mtype": MediaType.MOVIE,
        "media_source": MediaSource.TMDB,
        "media_id": "1234",
        "music_type": None,
    }]


def test_transfer_degrades_to_library_root_when_automatic_category_missing_tmdb(
    monkeypatch,
) -> None:
    """启用自动类别目录且缺少 TMDB 分类时，不再硬拒收，而是降级到媒体库根目录继续整理。

    这是 fork 的定制行为：douban 独占等拿不到 TMDB 辅助信息的影片，
    不应整条丢失，而是落到媒体库根目录（无分类子目录）。
    """
    chain = object.__new__(TransferChain)
    chain.jobview = SimpleNamespace(
        try_remove_job=lambda _task: None,
        running_task=Mock(),
    )
    chain._transfer_admissions = Mock()
    chain._worker_owner_id = "category-owner"
    chain._owned_leases = {
        "task-before-category": ("lease-before-category", float("inf"))
    }
    chain._worker_state_lock = threading.RLock()
    chain.durable_event_writer = Mock()
    chain.runtime_config = SimpleNamespace(
        scrape_follow_tmdb=True,
        ai_agent_enable=True,
        ai_agent_retry_transfer=True,
    )
    chain.queue_failed_transfer_notification = Mock()
    chain._TransferChain__mark_torrent_completed_if_done = Mock()
    record_transfer_failure = Mock()
    add_transfer_fail = Mock()
    monkeypatch.setattr(
        "app.chain.transfer.settlement.record_transfer_failure",
        record_transfer_failure,
    )
    monkeypatch.setattr("app.chain.transfer.settlement.add_transfer_fail", add_transfer_fail)
    chain._transfer_admissions.checkpoint_plan.side_effect = (
        lambda **kwargs: SimpleNamespace(checkpoint=kwargs["checkpoint"])
    )
    # 续传路径所需的最小桩：storage oper 选择与计划执行
    chain._TransferChain__select_storage_oper = Mock(return_value=Mock())
    chain._plan_checkpoint_and_execute = Mock(
        return_value=SimpleNamespace(success=True, message="")
    )
    chain._finish_scrape_batch_task = Mock()
    chain._TransferChain__build_durable_step_runner = Mock(return_value=Mock())
    chain.transfer_history_repository = SimpleNamespace()
    monkeypatch.setattr(
        "app.chain.transfer.execution.MediaChain",
        lambda: SimpleNamespace(
            supplement_tmdb_info=lambda media, _meta: media,
        ),
    )
    monkeypatch.setattr("app.chain.transfer.filter.MediaChain", lambda: SimpleNamespace(
            supplement_tmdb_info=lambda media, _meta: media,
        ))
    task = TransferTask(
        fileitem=FileItem(
            storage="local",
            path="/downloads/Test.Movie.2024.mkv",
            type="file",
            name="Test.Movie.2024.mkv",
            extension="mkv",
            size=1024,
        ),
        meta=MetaInfo("Test Movie 2024"),
        mediainfo=MediaInfo(
            media_source=MediaSource.AniList,
            media_id="1234",
            anilist_id=1234,
            type=MediaType.MOVIE,
            title="Test Movie",
            year="2024",
        ),
        target_directory=TransferDirectoryConf(
            library_storage="local",
            library_path="/library",
            library_category_folder=True,
        ),
        library_category_folder=True,
        preview=False,
    )
    task.bind_admission_task_id("task-before-category")
    task.bind_execution_lease(
        owner_id="category-owner",
        lease_token="lease-before-category",
    )

    state, message = chain._TransferChain__handle_transfer(task)

    # 不再硬拒收：整理继续并以成功返回
    assert state is True
    assert message == ""
    # 降级为媒体库根目录（关闭分类子目录）
    assert task.library_category_folder is False
    assert task.target_directory.library_category_folder is False
    # 媒体身份保持不变
    assert task.mediainfo.media_source == MediaSource.AniList
    assert task.mediainfo.media_id == "1234"
    # 不应记录为失败 / 通知
    assert (
        task.plan_checkpoint is None
        or task.plan_checkpoint.rejection_error is None
    )
    chain._transfer_admissions.record_planning_failure.assert_not_called()
    record_transfer_failure.assert_not_called()
    add_transfer_fail.assert_not_called()
    chain.queue_failed_transfer_notification.assert_not_called()
    chain._TransferChain__mark_torrent_completed_if_done.assert_not_called()
