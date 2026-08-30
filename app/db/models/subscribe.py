import time
from typing import Any, Optional

from sqlalchemy import Integer, String, Float, JSON, Index, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import get_id_column, Base
from app.db.decorators import db_query, db_update, async_db_query, async_db_update
from app.db.models._constraints import media_identity_constraint
from app.schemas.types import MUSIC_ENTITY_RECORDING, MediaSource


class Subscribe(Base):
    """
    订阅表
    """
    id = get_id_column()
    # 标题
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # 年份
    year: Mapped[Optional[str]] = mapped_column(String)
    # 类型
    type: Mapped[Optional[str]] = mapped_column(String)
    # 搜索关键字
    keyword: Mapped[Optional[str]] = mapped_column(String)
    media_source: Mapped[Optional[str]] = mapped_column(String, index=True)
    media_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    # 音乐实体类型：recording 单曲、album 专辑
    music_type: Mapped[Optional[str]] = mapped_column(String)
    # 专辑预期总曲目数，供整专资源完整性判断
    total_tracks: Mapped[Optional[int]] = mapped_column(Integer)
    # 季号
    season: Mapped[Optional[int]] = mapped_column(Integer)
    # 海报
    poster: Mapped[Optional[str]] = mapped_column(String)
    # 背景图
    backdrop: Mapped[Optional[str]] = mapped_column(String)
    # 评分，float
    vote: Mapped[Optional[float]] = mapped_column(Float)
    # 简介
    description: Mapped[Optional[str]] = mapped_column(String)
    # 过滤规则
    filter: Mapped[Optional[str]] = mapped_column(String)
    # 包含
    include: Mapped[Optional[str]] = mapped_column(String)
    # 排除
    exclude: Mapped[Optional[str]] = mapped_column(String)
    # 质量
    quality: Mapped[Optional[str]] = mapped_column(String)
    # 分辨率
    resolution: Mapped[Optional[str]] = mapped_column(String)
    # 特效
    effect: Mapped[Optional[str]] = mapped_column(String)
    # 音乐音质等级：hires/lossless/lossy，可用正则组合
    audio_quality: Mapped[Optional[str]] = mapped_column(String)
    # 音频格式，可用正则组合
    audio_format: Mapped[Optional[str]] = mapped_column(String)
    # 最低码率（bps）
    min_bitrate: Mapped[Optional[int]] = mapped_column(Integer)
    # 最低位深（bit）
    min_bit_depth: Mapped[Optional[int]] = mapped_column(Integer)
    # 最低采样率（Hz）
    min_sample_rate: Mapped[Optional[int]] = mapped_column(Integer)
    # 总集数
    total_episode: Mapped[Optional[int]] = mapped_column(Integer)
    # 开始集数
    start_episode: Mapped[Optional[int]] = mapped_column(Integer)
    # 缺失集数
    lack_episode: Mapped[Optional[int]] = mapped_column(Integer)
    # 附加信息
    note: Mapped[Optional[Any]] = mapped_column(JSON)
    # 状态：N-新建 R-订阅中 P-待定 S-暂停
    state: Mapped[str] = mapped_column(String, nullable=False, index=True, default='N')
    # 最后更新时间
    last_update: Mapped[Optional[str]] = mapped_column(String)
    # 创建时间
    date: Mapped[Optional[str]] = mapped_column(String)
    # 订阅用户
    username: Mapped[Optional[str]] = mapped_column(String, index=True)
    # 订阅站点
    sites: Mapped[Optional[Any]] = mapped_column(JSON, default=list)
    # 下载器
    downloader: Mapped[Optional[str]] = mapped_column(String)
    # 是否洗版
    best_version: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    # 是否只洗全集整包，开启后电视剧洗版不按单集下载
    best_version_full: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    # 当前优先级
    current_priority: Mapped[Optional[int]] = mapped_column(Integer)
    # 当前音乐版本格式
    current_audio_format: Mapped[Optional[str]] = mapped_column(String)
    # 当前音乐版本码率（bps）
    current_bitrate: Mapped[Optional[int]] = mapped_column(Integer)
    # 当前音乐版本位深（bit）
    current_bit_depth: Mapped[Optional[int]] = mapped_column(Integer)
    # 当前音乐版本采样率（Hz）
    current_sample_rate: Mapped[Optional[int]] = mapped_column(Integer)
    # 洗版时已下载剧集的优先级状态，格式：{"1": 90, "2": 100}
    episode_priority: Mapped[Optional[Any]] = mapped_column(JSON)
    # 保存路径
    save_path: Mapped[Optional[str]] = mapped_column(String)
    # 是否使用 imdbid 搜索
    search_imdbid: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    # 是否手动修改过总集数 0否 1是
    manual_total_episode: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    # 自定义识别词
    custom_words: Mapped[Optional[str]] = mapped_column(String)
    # 自定义媒体类别
    media_category: Mapped[Optional[str]] = mapped_column(String)
    # 过滤规则组
    filter_groups: Mapped[Optional[Any]] = mapped_column(JSON, default=list)
    # 选择的剧集组
    episode_group: Mapped[Optional[str]] = mapped_column(String)

    __table_args__ = (
        media_identity_constraint("subscribe"),
        Index('ix_subscribe_type_date', 'type', 'date'),
        Index('ix_subscribe_media_identity', 'media_source', 'media_id'),
    )

    @classmethod
    def _identity_condition(
            cls,
            media_source: Optional[MediaSource] = None,
            media_id: Optional[str] = None,
            music_type: Optional[str] = None,
    ):
        """按统一媒体身份优先级构造订阅查询条件。"""
        if not media_source or media_id is None or not str(media_id).strip():
            return None
        condition = (
            (cls.media_source == str(media_source))
            & (cls.media_id == str(media_id).strip())
        )
        if music_type == MUSIC_ENTITY_RECORDING:
            return condition & or_(cls.music_type == music_type, cls.music_type.is_(None))
        if music_type:
            return condition & (cls.music_type == music_type)
        return condition

    @classmethod
    @db_query
    def exists(
            cls, db: Session, media_source: MediaSource, media_id: str,
            season: Optional[int] = None,
            episode_group: Optional[str] = None,
            music_type: Optional[str] = None,
    ):
        """按媒体身份、季号与剧集组查询已有订阅。"""
        condition = cls._identity_condition(
            media_source, media_id, music_type
        )
        if condition is None:
            return None
        statement = select(cls).where(condition)
        if season is not None:
            statement = statement.where(cls.season == season)
        statement = statement.where(cls.episode_group == episode_group)
        return db.execute(statement).scalars().first()

    @classmethod
    @async_db_query
    async def async_exists(
            cls, db: AsyncSession, media_source: MediaSource, media_id: str,
            season: Optional[int] = None,
            episode_group: Optional[str] = None,
            music_type: Optional[str] = None,
    ):
        """异步按媒体身份、季号与剧集组查询已有订阅。"""
        condition = cls._identity_condition(
            media_source, media_id, music_type
        )
        if condition is None:
            return None
        query = select(cls).filter(condition)
        if season is not None:
            query = query.filter(cls.season == season)
        query = query.filter(cls.episode_group == episode_group)
        result = await db.execute(query)
        return result.scalars().first()

    @classmethod
    @db_query
    def exists_by_username(
            cls, db: Session, username: str, media_source: MediaSource, media_id: str,
            season: Optional[int] = None,
            episode_group: Optional[str] = None,
            music_type: Optional[str] = None,
    ):
        """
        按订阅 owner、媒体身份、季号与剧集组查询订阅行。
        """
        if not username:
            return None
        condition = cls._identity_condition(
            media_source, media_id, music_type
        )
        if condition is None:
            return None
        statement = select(cls).where(cls.username == username, condition)
        if season is not None:
            statement = statement.where(cls.season == season)
        statement = statement.where(cls.episode_group == episode_group)
        return db.execute(statement).scalars().first()

    @classmethod
    @async_db_query
    async def async_exists_by_username(
            cls, db: AsyncSession, username: str, media_source: MediaSource,
            media_id: str, season: Optional[int] = None,
            episode_group: Optional[str] = None,
            music_type: Optional[str] = None,
    ):
        """
        异步按订阅 owner、媒体身份、季号与剧集组查询订阅行。
        """
        if not username:
            return None
        condition = cls._identity_condition(
            media_source, media_id, music_type
        )
        if condition is None:
            return None
        query = select(cls).filter(cls.username == username, condition)
        if season is not None:
            query = query.filter(cls.season == season)
        query = query.filter(cls.episode_group == episode_group)
        result = await db.execute(query)
        return result.scalars().first()

    @classmethod
    @db_query
    def get_by_state(cls, db: Session, state: str):
        # 如果 state 为空或 None，返回所有订阅
        statement = select(cls)
        if state:
            # 如果传入的状态不为空，拆分成多个状态
            statement = statement.where(cls.state.in_(state.split(',')))
        return list(db.execute(statement).scalars().all())

    @classmethod
    @async_db_query
    async def async_get_by_state(cls, db: AsyncSession, state: str):
        # 如果 state 为空或 None，返回所有订阅
        if not state:
            result = await db.execute(select(cls))
        else:
            # 如果传入的状态不为空，拆分成多个状态
            result = await db.execute(
                select(cls).filter(cls.state.in_(state.split(',')))
            )
        return list(result.scalars().all())

    @classmethod
    @db_query
    def get_by_title(cls, db: Session, title: str, season: Optional[int] = None):
        statement = select(cls).where(cls.name == title)
        if season is not None:
            statement = statement.where(cls.season == season)
        return db.execute(statement).scalars().first()

    @classmethod
    @async_db_query
    async def async_get_by_title(cls, db: AsyncSession, title: str, season: Optional[int] = None):
        if season is not None:
            result = await db.execute(
                select(cls).filter(cls.name == title, cls.season == season)
            )
        else:
            result = await db.execute(
                select(cls).filter(cls.name == title)
            )
        return result.scalars().first()

    @classmethod
    @async_db_query
    async def async_list_by_title(cls, db: AsyncSession, title: str, season: Optional[int] = None):
        """
        异步按标题查询候选订阅列表。
        """
        if season is not None:
            result = await db.execute(
                select(cls).filter(cls.name == title, cls.season == season)
            )
        else:
            result = await db.execute(
                select(cls).filter(cls.name == title)
            )
        return list(result.scalars().all())

    @classmethod
    @db_query
    def list_by_media_identity(
            cls, db: Session, media_source: MediaSource, media_id: str,
            music_type: Optional[str] = None,
    ):
        """同步按统一媒体身份查询候选订阅列表。"""
        condition = cls._identity_condition(
            media_source=media_source,
            media_id=media_id,
            music_type=music_type,
        )
        if condition is None:
            return []
        return list(db.execute(select(cls).where(condition)).scalars().all())

    @classmethod
    @async_db_query
    async def async_list_by_media_identity(
            cls, db: AsyncSession, media_source: MediaSource, media_id: str,
            music_type: Optional[str] = None,
    ):
        """异步按统一媒体身份查询候选订阅列表。"""
        condition = cls._identity_condition(
            media_source=media_source,
            media_id=media_id,
            music_type=music_type,
        )
        if condition is None:
            return []
        result = await db.execute(select(cls).filter(condition))
        return list(result.scalars().all())

    @classmethod
    @db_query
    def get_by_mediaid(cls, db: Session, mediaid: str):
        return db.query(cls).filter(cls.mediaid == mediaid).first()

    @classmethod
    @db_query
    def find_same_media(
            cls, db: Session, name: Optional[str] = None, year: Optional[str] = None,
            season: Optional[int] = None, tmdbid: Optional[int] = None,
            doubanid: Optional[str] = None, bangumiid: Optional[int] = None,
            media_source: Optional[str] = None, media_id: Optional[str] = None,
            username: Optional[str] = None, episode_group: Optional[str] = None,
    ):
        """
        按跨身份同剧口径查询既有订阅，用于创建订阅时的去重。

        v3 统一媒体身份为 media_source/media_id（无 tmdbid/doubanid 分列），
        同剧判定收敛为：标题 + 年份 + 季兜底（叠加 episode_group/username 作用域）。

        所有分支均叠加 ``episode_group`` 作用域（与 ``exists`` 同口径），
        主季（NULL）与自定义剧集组视为不同订阅，不得互相合并。
        owner_scope 场景需保留 ``username`` 作用域（与 ``exists_by_username`` 同口径）。
        返回的既有可能与请求身份不完全相同，但属于同一部剧，由调用方负责合并身份字段。
        """

        def _scope(query):
            """叠加季号、剧集组与用户作用域，避免跨剧集组误合并。"""
            if season is not None:
                query = query.filter(cls.season == season)
            query = query.filter(cls.episode_group == episode_group)
            if username:
                query = query.filter(cls.username == username)
            return query

        # v3 统一媒体身份（media_source/media_id 单列，无 tmdbid/doubanid 分列）：
        # 跨身份同剧去重收敛为「标题 + 年份 + 季」兜底（叠加作用域），
        # 精确 media_source/media_id 命中由 exists 覆盖。
        if name:
            query = db.query(cls).filter(cls.name == name)
            if year:
                query = query.filter(cls.year == year)
            existing = _scope(query).first()
            if existing:
                return existing
        return None

    @classmethod
    @async_db_query
    async def async_find_same_media(
            cls, db: AsyncSession, name: Optional[str] = None, year: Optional[str] = None,
            season: Optional[int] = None, tmdbid: Optional[int] = None,
            doubanid: Optional[str] = None, bangumiid: Optional[int] = None,
            media_source: Optional[str] = None, media_id: Optional[str] = None,
            username: Optional[str] = None, episode_group: Optional[str] = None,
    ):
        """
        异步按跨身份同剧口径查询既有订阅（供 ``async_add`` 使用）。

        判定与 ``find_same_media`` 一致：标题+年份+季兜底（叠加 episode_group/username 作用域）。
        命中返回同一部剧的可能不同身份的既有订阅。
        """

        def _scope(query):
            """叠加季号、剧集组与用户作用域，避免跨剧集组误合并。"""
            if season is not None:
                query = query.filter(cls.season == season)
            query = query.filter(cls.episode_group == episode_group)
            if username:
                query = query.filter(cls.username == username)
            return query

        # v3 统一媒体身份：跨身份同剧去重收敛为「标题 + 年份 + 季」兜底（叠加作用域）
        if name:
            query = select(cls).filter(cls.name == name)
            if year:
                query = query.filter(cls.year == year)
            result = await db.execute(_scope(query))
            existing = result.scalars().first()
            if existing:
                return existing
        return None

    @classmethod
    @async_db_query
    async def async_get_by_mediaid(cls, db: AsyncSession, mediaid: str):
        result = await db.execute(
            select(cls).filter(cls.mediaid == mediaid)
        )
        return result.scalars().first()

    @classmethod
    @async_db_query
    async def async_list_by_mediaid(cls, db: AsyncSession, mediaid: str):
        """
        异步按自定义媒体 ID 查询候选订阅列表。
        """
        result = await db.execute(
            select(cls).filter(cls.mediaid == mediaid)
        )
        return result.scalars().all()

    @classmethod
    @db_query
    def get_by(
            cls, db: Session, type: str, media_source: MediaSource, media_id: str,
            season: Optional[str] = None,
            music_type: Optional[str] = None,
    ):
        """
        根据条件查询订阅
        """
        condition = cls._identity_condition(
            media_source, media_id, music_type
        )
        if condition is None:
            return None
        statement = select(cls).where(condition, cls.type == type)
        if season is not None:
            statement = statement.where(cls.season == season)
        return db.execute(statement).scalars().first()

    @classmethod
    @async_db_query
    async def async_get_by(
            cls, db: AsyncSession, type: str, media_source: MediaSource, media_id: str,
            season: Optional[str] = None,
            music_type: Optional[str] = None,
    ):
        """
        根据条件查询订阅
        """
        condition = cls._identity_condition(
            media_source, media_id, music_type
        )
        if condition is None:
            return None
        query = select(cls).filter(condition, cls.type == type)
        if season is not None:
            query = query.filter(cls.season == season)
        result = await db.execute(query)
        return result.scalars().first()

    @db_update
    def delete_by_media_identity(
            self, db: Session, media_source: MediaSource, media_id: str,
            season: Optional[int] = None,
    ) -> bool:
        """按规范媒体身份删除订阅。"""
        model = type(self)
        statement = delete(model).where(
            model.media_source == media_source,
            model.media_id == str(media_id),
        )
        if season is not None:
            statement = statement.where(model.season == season)
        db.execute(statement, execution_options={"synchronize_session": False})
        return True

    @async_db_update
    async def async_delete_by_media_identity(
            self, db: AsyncSession, media_source: MediaSource, media_id: str,
            season: Optional[int] = None,
    ) -> bool:
        """异步按规范媒体身份删除订阅。"""
        rows = await self.async_list_by_media_identity(
            db, media_source=media_source, media_id=media_id
        )
        for row in rows:
            if season is None or row.season == season:
                await row.async_delete(db, row.id)
        return True

    @classmethod
    @db_query
    def list_by_username(cls, db: Session, username: str, state: Optional[str] = None, mtype: Optional[str] = None):
        statement = select(cls).where(cls.username == username)
        if state:
            statement = statement.where(cls.state == state)
        if mtype:
            statement = statement.where(cls.type == mtype)
        return list(db.execute(statement).scalars().all())

    @classmethod
    @async_db_query
    async def async_list_by_username(cls, db: AsyncSession, username: str, state: Optional[str] = None,
                                     mtype: Optional[str] = None):
        if mtype:
            if state:
                result = await db.execute(
                    select(cls).filter(cls.state == state, cls.username == username, cls.type == mtype)
                )
            else:
                result = await db.execute(
                    select(cls).filter(cls.username == username, cls.type == mtype)
                )
        else:
            if state:
                result = await db.execute(
                    select(cls).filter(cls.state == state, cls.username == username)
                )
            else:
                result = await db.execute(
                    select(cls).filter(cls.username == username)
                )
        return list(result.scalars().all())

    @classmethod
    @db_query
    def list_by_type(cls, db: Session, mtype: str, days: int):
        return list(db.execute(
            select(cls).where(
                cls.type == mtype,
                cls.date >= time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(max(0, time.time() - 86400 * int(days))))
            )
        ).scalars().all())

    @classmethod
    @async_db_query
    async def async_list_by_type(cls, db: AsyncSession, mtype: str, days: int):
        result = await db.execute(
            select(cls).filter(
                cls.type == mtype,
                cls.date >= time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(max(0, time.time() - 86400 * int(days))))
            )
        )
        return list(result.scalars().all())


    # ==================== 旧身份字段兼容 ====================
    # v3 将 tmdbid/doubanid/bangumiid/anilistid 收敛为统一的 media_source/media_id，
    # 但 fork 链路代码仍会读写旧字段名。以下 property 从统一身份派生（读），
    # 写回时映射到 media_source/media_id（v3 无独立分列），让旧访问点保持可用。
    # 注意：不参与 SQL 查询；ORM 查询请使用 media_source/media_id。

    @property
    def tmdbid(self):
        """媒体源为 themoviedb 时返回原生 ID，否则 None。"""
        if self.media_source == MediaSource.TMDB.value:
            try:
                return int(str(self.media_id).strip())
            except (TypeError, ValueError):
                return None
        return None

    @tmdbid.setter
    def tmdbid(self, value):
        """写 tmdbid 即把统一身份切到 themoviedb 源（fork 链路 update 兼容）。"""
        if value is None:
            return
        self.media_source = MediaSource.TMDB.value
        self.media_id = str(value)

    @property
    def doubanid(self):
        """媒体源为 douban 时返回原生 ID，否则 None。"""
        if self.media_source == MediaSource.Douban.value:
            return self.media_id
        return None

    @doubanid.setter
    def doubanid(self, value):
        """写 doubanid 即把统一身份切到 douban 源。"""
        if value is None:
            return
        self.media_source = MediaSource.Douban.value
        self.media_id = str(value)

    @property
    def bangumiid(self):
        """媒体源为 bangumi 时返回原生 ID，否则 None。"""
        if self.media_source == MediaSource.Bangumi.value:
            return self.media_id
        return None

    @bangumiid.setter
    def bangumiid(self, value):
        """写 bangumiid 即把统一身份切到 bangumi 源。"""
        if value is None:
            return
        self.media_source = MediaSource.Bangumi.value
        self.media_id = str(value)

    @property
    def anilistid(self):
        """媒体源为 anilist 时返回原生 ID，否则 None。"""
        if self.media_source == MediaSource.AniList.value:
            return self.media_id
        return None

    @anilistid.setter
    def anilistid(self, value):
        """写 anilistid 即把统一身份切到 anilist 源。"""
        if value is None:
            return
        self.media_source = MediaSource.AniList.value
        self.media_id = str(value)

    @property
    def mediaid(self):
        """旧字段 mediaid 与 media_id 同值。"""
        return self.media_id

    @mediaid.setter
    def mediaid(self, value):
        if value is None:
            return
        self.media_id = str(value)

    @property
    def imdbid(self):
        """v3 无独立 imdbid 列；统一身份非 TMDB 时无值，保留接口兼容。"""
        return None

    @imdbid.setter
    def imdbid(self, value):
        """v3 无独立 imdbid 列，写操作忽略。"""

    @property
    def tvdbid(self):
        """v3 无独立 tvdbid 列；统一身份非 TMDB 时无值，保留接口兼容。"""
        return None

    @tvdbid.setter
    def tvdbid(self, value):
        """v3 无独立 tvdbid 列，写操作忽略。"""
