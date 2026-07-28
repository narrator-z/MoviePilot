from typing import Any, Optional, Tuple, Union

import hashlib
import re

from app.core.context import MediaInfo
from app.schemas.types import MediaType


MEDIA_SOURCE_ALIASES = {
    "tmdb": "themoviedb",
    "themoviedb": "themoviedb",
    "douban": "douban",
    "bangumi": "bangumi",
    "anilist": "anilist",
}

MEDIA_SOURCE_PREFIXES = {
    "themoviedb": "tmdb",
    "douban": "douban",
    "bangumi": "bangumi",
    "anilist": "anilist",
}

MEDIA_SOURCE_ID_FIELDS = {
    "themoviedb": ("tmdb_id", "tmdbid"),
    "douban": ("douban_id", "doubanid"),
    "bangumi": ("bangumi_id", "bangumiid"),
    "anilist": ("anilist_id", "anilistid"),
}


def normalize_media_source(source: Optional[str]) -> Optional[str]:
    """规范化媒体数据源名称，兼容外部使用的 ``tmdb`` 前缀。"""
    if not source:
        return None
    normalized = str(source).strip().casefold()
    return MEDIA_SOURCE_ALIASES.get(normalized, normalized or None)


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


def resolve_media_identity(
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


def build_media_key(source: Optional[str], media_id: Optional[Any]) -> str:
    """构造 API 使用的带来源前缀媒体键。"""
    normalized_source = normalize_media_source(source)
    if not normalized_source or media_id is None or not str(media_id).strip():
        return ""
    prefix = MEDIA_SOURCE_PREFIXES.get(normalized_source, normalized_source)
    return f"{prefix}:{str(media_id).strip()}"


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
    return MediaInfo(
        source=None,
        scrape_source=None,
        type=mtype,
        title=name,
        year=str(year) if year else None,
        season=season,
        tmdb_id=None,
        media_id=manual_media_key(name, year),
    )
