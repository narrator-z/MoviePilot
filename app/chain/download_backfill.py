"""
下载文件历史回填工具（修复旧版容器订阅页“下载”列恒为 0）。

背景：旧版 MoviePilot 在添加下载时未向 downloadfiles 表写入文件明细，而订阅页“下载”
列依赖 downloadfiles 表有数据才能统计（详见 app/chain/subscribe.py 的读取逻辑），
导致历史订阅的下载数恒为 0；而“入库”列走 transferhistory，因此入库显示正常。

本模块提供幂等、可独立运行的回填能力：遍历全量 downloadhistory，对每条缺少
downloadfiles 明细的下载记录，从对应下载器回查种子文件清单并写入 downloadfiles，
使老订阅的“下载”列恢复正常统计。回填逻辑与 DownloadChain._add_download_files_from_downloader
保持一致（仅写入音视频/字幕/音频类文件、state=1、路径以保存目录为根拼接）。

设计目标：用户可在运行容器（含未部署“磁力补写”新代码的旧版）中将本文件放入 app/chain/
后直接调用 backfill_download_files()，仅复用既有的 DownloaderHelper / DownloadHistoryOper /
ChainBase.torrent_files，不依赖未部署的新代码。
"""
from pathlib import Path
from typing import Dict, List, Optional

from app.application.downloader import DownloaderHelper
from app.chain import ChainBase
from app.db.oper.downloadhistory import DownloadHistoryOper
from app.runtime.config import settings
from app.runtime.log import logger


class DownloadBackfill(ChainBase):
    """
    下载文件历史回填。

    遍历 downloadhistory 全量记录，对缺少 downloadfiles 明细的记录，从对应下载器回查
    种子文件清单并幂等写入，修复旧版容器“下载”列恒为 0 的问题。
    """

    # 单页扫描数量，避免一次性加载过多记录占用内存
    _PAGE_SIZE: int = 200

    def get_enabled_downloader_names(self) -> List[str]:
        """
        获取所有已启用下载器名称列表。

        当 downloadhistory 未记录下载器时，回填逻辑会回退遍历全部已启用下载器尝试回查
        文件清单，因此这里仅取名称（键）即可。
        """
        return list(DownloaderHelper().get_configs().keys())

    @staticmethod
    def _is_source_valid(history) -> bool:
        """
        判断下载记录的来源是否有效（仅回填有效来源的下载）。

        downloadhistory 的 note 字段以 {"source": ...} 记录下载来源（如 Subscribe|...、
        Manual 等）。旧版本可能未记录 note，为避免漏填合法下载，note 为空时不过滤；
        仅当 note 明确存在但缺少有效 source 时才视为无效来源。
        """
        note = getattr(history, "note", None)
        if note is None:
            return True
        if isinstance(note, dict):
            return bool(note.get("source"))
        return False

    def iter_download_histories(self):
        """
        分页迭代全量下载历史记录。

        每条记录内部通过独立的数据库会话读取，返回的对象为游离态，但仅需读取标量属性
        （download_hash / path / downloader / torrent_name），不影响回填正确性。
        """
        page = 1
        while True:
            records = DownloadHistoryOper().list_by_page(page=page, count=self._PAGE_SIZE)
            if not records:
                break
            for record in records:
                yield record
            if len(records) < self._PAGE_SIZE:
                break
            page += 1

    def backfill(self, limit: int = 0) -> Dict[str, int]:
        """
        执行下载文件历史回填（幂等）。

        遍历全量 downloadhistory，对缺少 downloadfiles 明细的记录，从对应下载器回查种子
        文件清单并写入 downloadfiles；已存在记录或无可回查下载器时跳过，下载器异常不中断
        整体流程（单条失败仅计入失败统计）。

        :param limit: 最多扫描的记录数，0 表示不限制（用于调试/分批回填）
        :return: 回填统计字典
        """
        stats = {
            "scanned": 0,
            "written": 0,
            "files_written": 0,
            "skipped_existing": 0,
            "skipped_no_hash": 0,
            "skipped_invalid_source": 0,
            "skipped_no_path": 0,
            "skipped_no_downloader": 0,
            "failed": 0,
        }
        enabled_downloaders = self.get_enabled_downloader_names()

        for history in self.iter_download_histories():
            if limit and stats["scanned"] >= limit:
                break
            stats["scanned"] += 1

            download_hash = (history.download_hash or "").strip()
            # 无 Hash 的记录无法在下载器侧定位种子，直接跳过
            if not download_hash:
                stats["skipped_no_hash"] += 1
                continue

            # 来源无效的记录（如缺少有效 source）不参与回填，避免污染下载统计
            if not self._is_source_valid(history):
                stats["skipped_invalid_source"] += 1
                continue

            # 幂等：该 Hash 已有文件明细则跳过，避免重复写入
            if DownloadHistoryOper().get_files_by_hash(download_hash):
                stats["skipped_existing"] += 1
                continue

            # 无保存路径则无法拼接完整文件路径，跳过
            if not history.path:
                stats["skipped_no_path"] += 1
                continue

            # 优先使用记录中的下载器；为空则回退遍历所有已启用下载器尝试
            candidates = [history.downloader] if history.downloader else enabled_downloaders
            candidates = [name for name in candidates if name]

            written_files = 0
            failed = False
            for downloader in candidates:
                try:
                    result = self._backfill_one(history, downloader)
                except Exception as err:  # 防御性兜底：绝不让单条异常中断整体回填
                    logger.error(
                        f"回填下载文件异常（hash={download_hash}, downloader={downloader}）：{err}"
                    )
                    failed = True
                    continue
                if result is None:
                    # 下载器连不上 / 异常
                    failed = True
                    continue
                if result > 0:
                    written_files += result
                    break  # 已成功写入，无需再尝试其它下载器

            if written_files > 0:
                stats["written"] += 1
                stats["files_written"] += written_files
            elif failed:
                stats["failed"] += 1
            else:
                # 所有候选下载器均无文件清单（种子已被清除 / 磁力暂无清单）
                stats["skipped_no_downloader"] += 1

        return stats

    def _backfill_one(self, history, downloader: str) -> Optional[int]:
        """
        对单条下载记录，从指定下载器回查文件清单并写入 downloadfiles。

        :param history: 下载历史记录（仅需 download_hash / path / torrent_name）
        :param downloader: 下载器名称
        :return: 写入的文件数（>0 成功）；0 表示下载器无该种子文件清单；None 表示下载器异常
        """
        try:
            torrent_files = self.torrent_files(history.download_hash, downloader)
        except Exception as err:
            logger.debug(
                f"从下载器回查文件清单失败（hash={history.download_hash}, downloader={downloader}）：{err}"
            )
            return None

        if not torrent_files:
            return 0

        # qbittorrent 返回列表，transmission 等可能返回带 .data 属性的对象
        if isinstance(torrent_files, list):
            files = torrent_files
        else:
            files = getattr(torrent_files, "data", []) or []

        if not files:
            return 0

        file_items = self._build_file_items(history, downloader, files)
        if not file_items:
            return 0

        DownloadHistoryOper().add_files(file_items)
        logger.info(
            f"回填 {len(file_items)} 个下载文件记录"
            f"（hash={history.download_hash}, downloader={downloader}）"
        )
        return len(file_items)

    @staticmethod
    def _build_file_items(history, downloader: str, files) -> List[dict]:
        """
        将下载器返回的种子文件清单转换为 downloadfiles 写入项。

        仅保留音视频/字幕/音频类文件（与 DownloadChain 写入逻辑一致），路径以下载历史保存
        目录为根拼接（下载器返回的文件名已做路径映射）。

        :param history: 下载历史记录（提供保存路径与种子名称）
        :param downloader: 下载器名称
        :param files: 下载器返回的文件对象列表（每个含 name 属性）
        :return: 可写入 downloadfiles 的字典列表
        """
        save_path = Path(history.path)
        media_exts = settings.RMT_MEDIAEXT + settings.RMT_SUBEXT + settings.RMT_AUDIOEXT
        items: List[dict] = []
        for file in files:
            file_name = getattr(file, "name", None)
            if not file_name:
                continue
            suffix = Path(file_name).suffix
            if not suffix or suffix.lower() not in media_exts:
                continue
            items.append({
                "download_hash": history.download_hash,
                "downloader": downloader,
                "fullpath": (save_path / file_name).as_posix(),
                "savepath": save_path.as_posix(),
                "filepath": file_name,
                "torrentname": history.torrent_name or "",
                "state": 1,
            })
        return items


def backfill_download_files(limit: int = 0) -> Dict[str, int]:
    """
    入口函数：对全量下载历史执行 downloadfiles 幂等回填，并打印统计。

    用户可在运行容器中执行：python -m app.chain.download_backfill
    （或 from app.chain.download_backfill import backfill_download_files; backfill_download_files()）

    :param limit: 最多扫描的记录数，0 表示不限制
    :return: 回填统计字典
    """
    stats = DownloadBackfill().backfill(limit=limit)
    _print_stats(stats)
    return stats


def _print_stats(stats: Dict[str, int]) -> None:
    """
    将回填统计以可读形式打印到标准输出与日志。

    :param stats: backfill 返回的统计字典
    """
    summary = (
        f"扫描 {stats['scanned']} 条，成功写入 {stats['written']} 条"
        f"（{stats['files_written']} 个文件）；"
        f"跳过：已有明细 {stats['skipped_existing']} / 无Hash {stats['skipped_no_hash']} /"
        f"无效来源 {stats['skipped_invalid_source']} /"
        f"无路径 {stats['skipped_no_path']} / 无下载器 {stats['skipped_no_downloader']}；"
        f"失败 {stats['failed']} 条"
    )
    logger.info(f"下载文件回填完成：{summary}")
    print(
        "==== 下载文件历史回填统计 ====\n"
        f"扫描记录数      : {stats['scanned']}\n"
        f"成功写入记录数  : {stats['written']}\n"
        f"成功写入文件数  : {stats['files_written']}\n"
        f"跳过-已有明细   : {stats['skipped_existing']}\n"
        f"跳过-无Hash     : {stats['skipped_no_hash']}\n"
        f"跳过-无效来源   : {stats['skipped_invalid_source']}\n"
        f"跳过-无保存路径 : {stats['skipped_no_path']}\n"
        f"跳过-无下载器   : {stats['skipped_no_downloader']}\n"
        f"失败-下载器异常 : {stats['failed']}\n"
        "=============================="
    )


if __name__ == "__main__":
    backfill_download_files()
