import hashlib
import re
from typing import Any, Optional, Tuple, Union

from app.domain.context import MediaInfo

# fork 物理文件同时承担 v3 兼容门面：下列符号直接复用 canonical 实现，
# 保证旧 import 路径拿到的符号与 canonical 是同一对象（compat overlay 语义）。
# 仅 4 个符号需要同一性（见 tests/test_legacy_import_compat.py）；其余工具
# （normalize_media_source/parse_media_key/is_media_source_*）保留 fork 字符串
# 语义实现，避免 canonical 枚举返回值破坏 fork 链路代码。
from app.domain.media import is_music_media_source  # noqa: F401
from app.runtime.config import settings
from app.schemas.media import (  # noqa: F401  (身份原语，is 同一对象)
    MEDIA_SOURCE_ALIASES as _SCHEMA_MEDIA_SOURCE_ALIASES,
)
from app.schemas.media import (
    MEDIA_SOURCE_PREFIXES as _SCHEMA_MEDIA_SOURCE_PREFIXES,
)
from app.schemas.media import (
    build_media_key as _schema_build_media_key,
)
from app.schemas.media import (
    resolve_media_identity as _schema_resolve_media_identity,
)
from app.schemas.types import (
    MUSIC_ENTITY_TYPES,
    MUSIC_SUBSCRIBABLE_TYPES,
    MediaType,
)

# canonical 符号以同一对象对外（满足 is 断言）
MEDIA_SOURCE_ALIASES = _SCHEMA_MEDIA_SOURCE_ALIASES
MEDIA_SOURCE_PREFIXES = _SCHEMA_MEDIA_SOURCE_PREFIXES
build_media_key = _schema_build_media_key
resolve_media_identity = _schema_resolve_media_identity

# fork 内部字符串别名表：normalize_media_source 保持字符串语义
# （canonical 版返回 MediaSource 枚举，fork 链路代码按字符串比较）
_FORK_SOURCE_ALIASES = {
    "tmdb": "themoviedb",
    "audio_db": "theaudiodb",
    "douban_music": "doubanmusic",
}


def normalize_media_source(source: Optional[Any]) -> Optional[str]:
    """规范化媒体数据源名称，兼容外部使用的 ``tmdb`` 前缀与插件扩展源。

    支持 MediaSource 枚举与字符串输入；枚举按 value 取源名字符串。
    """
    if not source:
        return None
    # v3 MediaInfo.media_source 可能是 MediaSource 枚举，枚举按 value 归一
    if hasattr(source, "value") and isinstance(getattr(source, "value", None), str):
        source = source.value
    normalized = str(source).strip().casefold()
    return _FORK_SOURCE_ALIASES.get(normalized, normalized or None)


def parse_media_key(media_key: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """解析带来源前缀的媒体键，返回规范化数据源与原生 ID。"""
    if not media_key or ":" not in str(media_key):
        return None, None
    prefix, media_id = str(media_key).split(":", 1)
    source = normalize_media_source(prefix)
    media_id = media_id.strip()
    if not source or not media_id:
        return None, None
    return source, media_id


MEDIA_SOURCE_ID_FIELDS = {
    "themoviedb": ("tmdb_id", "tmdbid"),
    "douban": ("douban_id", "doubanid"),
    "bangumi": ("bangumi_id", "bangumiid"),
    "anilist": ("anilist_id", "anilistid"),
    "musicbrainz": ("media_id",),
    "theaudiodb": ("media_id", "theaudiodb_id"),
    "doubanmusic": ("media_id", "douban_id", "doubanid"),
}

MUSIC_MEDIA_SOURCE_ORDER = ("musicbrainz", "theaudiodb", "doubanmusic")
MUSIC_MEDIA_SOURCES = frozenset(MUSIC_MEDIA_SOURCE_ORDER)


def normalize_music_type(
        value: Optional[object],
        *,
        allow_artist: bool = True,
) -> Optional[str]:
    """规范化音乐实体类型，非法值返回 None。"""
    normalized = str(value or "").strip().lower()
    allowed = MUSIC_ENTITY_TYPES if allow_artist else MUSIC_SUBSCRIBABLE_TYPES
    return normalized if normalized in allowed else None


def is_media_source_selected(source: Optional[str], source_key: str) -> bool:
    """
    判断请求级搜索数据源列表（逗号分隔，可为多个）中是否包含指定数据源。

    :param source: 请求级搜索数据源，逗号分隔多个来源，空表示不作限制
    :param source_key: 当前模块对应的数据源标识
    :return: 是否包含
    """
    if not source:
        return True
    normalized_key = normalize_media_source(source_key) or source_key
    return normalized_key in [
        normalize_media_source(item) for item in str(source).split(",")
    ]


def is_media_source_enabled(source: Optional[str], source_key: str) -> bool:
    """
    判断媒体搜索时数据源是否启用：请求级 source（逗号分隔多数据源）优先，
    未指定时回退到全局 SEARCH_SOURCE 配置，两者均未配置时全部启用。

    :param source: 请求级搜索数据源，逗号分隔多个来源
    :param source_key: 当前模块对应的数据源标识
    :return: 是否启用
    """
    if source:
        return is_media_source_selected(source, source_key)
    if settings.SEARCH_SOURCE:
        return is_media_source_selected(settings.SEARCH_SOURCE, source_key)
    return True


def _resolve_media_identity_fork(
        media: Any = None,
        source: Optional[str] = None,
        media_id: Optional[Any] = None,
        tmdbid: Optional[Any] = None,
        doubanid: Optional[Any] = None,
        bangumiid: Optional[Any] = None,
        anilistid: Optional[Any] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    从统一媒体对象、通用身份或兼容 ID 中解析主媒体身份。

    显式 ``source/media_id`` 优先；未指定来源时按 TMDB、豆瓣、Bangumi、
    AniList 的兼容顺序选择首个有效 ID。
    """
    normalized_source = normalize_media_source(source)
    if normalized_source and media_id is not None and str(media_id).strip():
        return normalized_source, str(media_id).strip()

    values = {
        "themoviedb": tmdbid,
        "douban": doubanid,
        "bangumi": bangumiid,
        "anilist": anilistid,
    }
    if media is not None:
        normalized_source = normalized_source or normalize_media_source(
            getattr(media, "source", None) or getattr(media, "media_source", None)
        )
        object_media_id = getattr(media, "media_id", None)
        if normalized_source and object_media_id is not None and str(object_media_id).strip():
            return normalized_source, str(object_media_id).strip()
        for media_source, fields in MEDIA_SOURCE_ID_FIELDS.items():
            for field in fields:
                value = getattr(media, field, None)
                if value is not None and str(value).strip():
                    values[media_source] = value
                    break

        legacy_source, legacy_media_id = parse_media_key(
            getattr(media, "mediaid", None)
        )
        if not normalized_source and legacy_source and legacy_media_id:
            return legacy_source, legacy_media_id

    if normalized_source:
        value = values.get(normalized_source)
        return (
            normalized_source,
            str(value).strip() if value is not None and str(value).strip() else None,
        )

    for media_source in MEDIA_SOURCE_ID_FIELDS:
        value = values.get(media_source)
        if value is not None and str(value).strip():
            return media_source, str(value).strip()
    return None, None


def manual_media_key(name: Optional[str], year: Optional[Union[str, int]] = None) -> str:
    """
    为无 TMDB/豆瓣等外部 ID 的“手动/文件名识别”媒体生成稳定身份键。

    键由归一化后的 ``name + year`` 取 SHA1 前 16 位构成，避免与已识别媒体串扰，
    并保证同一内容在不同次下载/整理中得到一致的去重键（决策 Q2）。
    返回形如 ``manual:<sha1_hex_16>``。
    """
    norm = re.sub(r"\s+", "", str(name or "").strip().lower())
    key = f"{norm}|{str(year or '').strip()}"
    return f"manual:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def build_filename_mediainfo(meta, media_type: Optional[MediaType] = None) -> MediaInfo:
    """
    用下载文件名解析出的 MetaInfo 直接构造一个没有外部 ID 的 MediaInfo（tmdb_id=None）。

    用于整理/下载兜底：让 TMDB/豆瓣未收录内容（短剧等）也能走通整理与下载目录路由。

    - 类型决策（决策 Q1）：若类型为 TV/UNKNOWN 且文件名也无集号（begin_episode 为 None），
      整包当电影处理，以绕过 transhandler 对 TV 文件的集数硬拒。
    - 身份键（决策 Q2）：media_id = manual_media_key(name, year)，保证去重稳定。
    - 命名中的 S/E 由文件自身 MetaInfo 在 transhandler 阶段驱动，此处仅透传 title/season 等展示信息。
    """
    name = getattr(meta, "name", None) or ""
    mtype = media_type or getattr(meta, "type", None) or MediaType.UNKNOWN
    # 当电影处理：TV/UNKNOWN 且文件名无集号 → 电影
    if mtype in (MediaType.TV, MediaType.UNKNOWN) and getattr(meta, "begin_episode", None) is None:
        mtype = MediaType.MOVIE
    year = getattr(meta, "year", None)
    season = None
    begin_season = getattr(meta, "begin_season", None)
    if begin_season:
        try:
            season = int(begin_season)
        except (TypeError, ValueError):
            season = None
    mediainfo = MediaInfo(
        media_source=None,
        scrape_source=None,
        type=mtype.value,
        title=name,
        year=str(year) if year else None,
        season=season,
        tmdb_id=None,
        media_id=manual_media_key(name, year),
    )
    # v3 的 __post_init__ 会把无来源的 media_id 清成 None（统一身份要求来源+ID 成对）。
    # 手动键（manual:hash）是 fork 整理去重用的稳定身份，即使无真实来源也必须保留。
    mediainfo.media_id = manual_media_key(name, year)
    return mediainfo
