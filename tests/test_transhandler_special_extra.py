"""
验证整理处理器对特殊附属文件的命名与转移。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.application.transfer.execution import TransferSettlementResult
from app.application.transfer.workflow import TransferPlanningInput
from app.chain.transfer.execution import _DurableTransferStepRunner
from app.domain.context import MediaInfo
from app.domain.meta.metavideo import MetaVideo
from app.modules.filemanager.transhandler import TransHandler
from app.schemas.file import FileItem
from app.schemas.transfer import TransferInfo
from app.schemas.types import MediaType
from tests.test_transfer_job_manager import FakeMedia, make_task, make_transfer_chain


def _transfer_without_episode(file_name: str) -> tuple[TransferInfo, MagicMock, MagicMock]:
    """
    整理一个未识别出集数的剧集视频文件
    """
    fileitem = FileItem(
        storage="local",
        path=f"/downloads/{file_name}",
        type="file",
        name=file_name,
        basename=file_name.removesuffix(".mkv"),
        extension="mkv",
        size=1024,
    )
    meta = MetaVideo(file_name)
    meta.begin_episode = None
    mediainfo = MediaInfo()
    mediainfo.type = MediaType.TV
    source_oper = MagicMock()
    target_oper = MagicMock()

    handler = TransHandler()
    planning_input = TransferPlanningInput(
        source_fileitem=fileitem.model_dump(mode="json"),
        meta=meta.to_dict(),
        mediainfo=mediainfo.to_dict(),
        target_storage="local",
        target_path="/library",
        requested_transfer_type="copy",
        media_type=MediaType.TV.value,
        need_scrape=False,
        need_rename=True,
        need_notify=True,
        preview=False,
    )
    try:
        checkpoint = handler.plan_transfer(
            planning_input,
            meta=meta,
            mediainfo=mediainfo,
            source_oper=source_oper,
            target_storage="local",
            target_path=Path("/library"),
            transfer_type="copy",
            need_scrape=False,
            need_rename=True,
            need_notify=True,
            overwrite_mode=None,
            episodes_info=None,
            preview=False,
        )
    except ValueError as error:
        result = TransferInfo(
            success=False,
            message=str(error),
            fileitem=fileitem,
            fail_list=[fileitem.path],
            transfer_type="copy",
            need_notify=True,
        )
    else:
        result = handler.execute_transfer_plan(
            checkpoint,
            meta=meta,
            mediainfo=mediainfo,
            source_oper=source_oper,
            target_oper=target_oper,
        )
    return result, source_oper, target_oper


@pytest.mark.parametrize(
    "file_name",
    [
        "Test.Show.NCOP.mkv",
        "Test.Show.Menu.mkv",
        "Test.Show.Featurette.mkv",
        *[
            f"[DMG&KNA&LKSUB&LoliHouse] Re Zero kara Hajimeru Isekai Seikatsu - SP{episode} "
            "[WebRip 1080p HEVC-10bit AAC ASSx2].mkv"
            for episode in range(26, 39)
        ],
    ],
)
def test_tv_special_extra_without_episode_skips_transfer(file_name: str) -> None:
    """
    未识别出集数的特典应保留跳过原因并返回未入库，且不执行存储操作。
    """
    result, source_oper, target_oper = _transfer_without_episode(file_name)

    assert result.success is False
    assert result.message == "未识别到文件集数，识别为特典/附加视频文件"
    assert result.failure_stage == "planning"
    assert "SxxExx" in result.recovery_action
    assert result.target_item is None
    assert result.fail_list == [f"/downloads/{file_name}"]
    assert result.need_notify is False
    assert result.fileitem.path == f"/downloads/{file_name}"
    assert source_oper.mock_calls == []
    assert target_oper.mock_calls == []


def test_regular_tv_file_without_episode_remains_failure() -> None:
    """
    普通剧集视频未识别出集数时仍应返回失败并保留通知
    """
    result, source_oper, target_oper = _transfer_without_episode("Test.Show.Bonus.mkv")

    assert result.success is False
    assert result.message == "未识别到文件集数"
    assert result.need_notify is True
    assert result.fail_list == ["/downloads/Test.Show.Bonus.mkv"]
    assert source_oper.mock_calls == []
    assert target_oper.mock_calls == []


def _plan_tv_file(file_path: str, file_name: str, *, need_rename: bool):
    """构造一个未识别出集数的剧集文件并直接调用 plan_transfer。"""
    fileitem = FileItem(
        storage="local",
        path=file_path,
        type="file",
        name=file_name,
        basename=file_name.removesuffix(".mkv"),
        extension="mkv",
        size=1024,
    )
    meta = MetaVideo(file_name)
    meta.begin_episode = None
    mediainfo = MediaInfo()
    mediainfo.type = MediaType.TV
    handler = TransHandler()
    planning_input = TransferPlanningInput(
        source_fileitem=fileitem.model_dump(mode="json"),
        meta=meta.to_dict(),
        mediainfo=mediainfo.to_dict(),
        target_storage="local",
        target_path="/library",
        requested_transfer_type="copy",
        media_type=MediaType.TV.value,
        need_scrape=False,
        need_rename=need_rename,
        need_notify=True,
        preview=False,
    )
    return handler.plan_transfer(
        planning_input,
        meta=meta,
        mediainfo=mediainfo,
        source_oper=MagicMock(),
        target_storage="local",
        target_path=Path("/library"),
        transfer_type="copy",
        need_scrape=False,
        need_rename=need_rename,
        need_notify=True,
        overwrite_mode=None,
        episodes_info=None,
        preview=False,
    )


def test_tv_file_recovers_episode_from_parent_folder() -> None:
    """文件名无集数但父目录带 SxxExx 时，应从父目录恢复集数而非拒收。"""
    checkpoint = _plan_tv_file(
        "/downloads/Show.S01E02/Test.Show.1080p.mkv",
        "Test.Show.1080p.mkv",
        need_rename=False,
    )

    assert checkpoint.resolved_meta["begin_episode"] == 2
    assert checkpoint.skip_reason is None


def test_tv_file_recovers_episode_from_grandparent_folder() -> None:
    """祖父目录带「第N话」时也应能恢复集数。"""
    checkpoint = _plan_tv_file(
        "/downloads/Anime/第3话/Test.Show.mkv",
        "Test.Show.mkv",
        need_rename=False,
    )

    assert checkpoint.resolved_meta["begin_episode"] == 3
    assert checkpoint.skip_reason is None


def test_tv_file_without_episode_and_no_folder_hint_still_fails() -> None:
    """父目录也无集数线索的普通剧集文件仍应拒收，避免误整理。"""
    import pytest

    with pytest.raises(Exception) as exc:
        _plan_tv_file(
            "/downloads/noepisode/Test.Show.Bonus.mkv",
            "Test.Show.Bonus.mkv",
            need_rename=False,
        )
    assert "未识别到文件集数" in str(exc.value)
@pytest.mark.parametrize("legacy", [False, True])
def test_skipped_extra_settles_as_unsuccessful_history(monkeypatch, legacy: bool) -> None:
    """
    特典的零操作执行结果经普通及兼容结算均写未入库历史，且不触发成功副作用。
    """
    result, _, _ = _transfer_without_episode("Test.Show.SP33.mkv")
    result.transfer_type = "move"
    task = make_task(1)
    task.fileitem = result.fileitem
    task.meta = MetaVideo("Test.Show.SP33.mkv")
    task.mediainfo = FakeMedia()
    task.bind_admission_task_id("special-extra")
    task.bind_execution_lease(owner_id="worker", lease_token="lease")
    runner = object.__new__(_DurableTransferStepRunner)
    runner._task_id = "special-extra"
    runner._lease_token = "lease"
    runner._operation_ids = []
    runner._command = MagicMock()
    runner._command.checkpoint.side_effect = lambda **kwargs: SimpleNamespace(
        checkpoint=kwargs["checkpoint"]
    )
    checkpoint = runner.checkpoint(result)
    assert checkpoint.payload["outcome"] == "failed"
    assert checkpoint.operation_ids == ()
    assert checkpoint.skip_reason == result.message
    task.bind_execution_checkpoint(checkpoint)

    chain = make_transfer_chain()
    chain.jobview.add_task(task)
    chain.transfer_history_repository.get_by_src.return_value = None
    chain.queue_failed_transfer_notification = MagicMock()
    chain._schedule_automatic_ai_takeover = MagicMock()
    chain._TransferChain__forget_owned_lease = MagicMock()
    chain._record_scrape_target = MagicMock()
    chain.notify_successful_transfer = MagicMock()
    chain._cleanup_successful_transfer = MagicMock()
    chain.eventmanager = MagicMock()
    staging = MagicMock()
    staging.replace.return_value = SimpleNamespace(id=1)

    def settle(**kwargs):
        """
        执行真实历史字段投影，只替换数据库及事件写入边界。
        """
        assert kwargs["settlement"].outcome == "failed"
        kwargs["stage_history"](staging)
        return TransferSettlementResult(history_id=1, settlement_revision=1, pending_deleted=True)

    chain.durable_event_writer.transfer_result.side_effect = settle
    monkeypatch.setattr("app.chain.transfer.settlement.record_transfer_failure", lambda *args, **kwargs: None)
    if legacy:
        chain._TransferChain__settle_legacy_transfer_result(task, result)
    else:
        state, message = chain._TransferChain__default_callback(task, result)
        assert state is False
        assert message == result.message
    history = staging.replace.call_args.args[0]
    assert history.status is False
    assert history.errmsg == result.message
    assert history.failure_stage == "planning"
    assert history.recovery_action == result.recovery_action
    assert history.dest is None
    assert task.terminal_settled
    chain.eventmanager.send_event.assert_not_called()
    chain._record_scrape_target.assert_not_called()
    chain.notify_successful_transfer.assert_not_called()
    chain._cleanup_successful_transfer.assert_not_called()
