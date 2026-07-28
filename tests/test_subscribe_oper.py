from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.context import MediaInfo
from app.db.models.subscribe import Subscribe
from app.db.models.subscribehistory import SubscribeHistory
from app.db.subscribe_oper import SubscribeOper
from app.schemas.types import MediaType


def _make_session():
    """构造只含 subscribe 表的隔离内存库会话，避免依赖真实数据库。"""
    engine = create_engine("sqlite://")
    Subscribe.__table__.create(engine)
    SessionFactory = sessionmaker(bind=engine)
    return SessionFactory(), engine


def test_add_history_converts_boolean_integer_flags(monkeypatch):
    """
    写入订阅历史前应把布尔开关转为整型，兼容 PostgreSQL 的严格类型检查。
    """
    captured = {}

    def fake_create(self, _db):
        """
        截获待写入模型，避免测试依赖具体数据库方言的类型宽松行为。
        """
        captured.update({
            "id": self.id,
            "best_version": self.best_version,
            "best_version_full": self.best_version_full,
            "search_imdbid": self.search_imdbid,
        })

    monkeypatch.setattr(SubscribeHistory, "create", fake_create)

    SubscribeOper().add_history(
        id=100,
        name="Test Movie",
        type="电影",
        best_version=False,
        best_version_full=True,
        search_imdbid=False,
        unknown_field=True,
    )

    assert captured == {
        "id": None,
        "best_version": 0,
        "best_version_full": 1,
        "search_imdbid": 0,
    }


def test_add_dedup_merges_douban_into_existing_tmdb():
    """
    为已有 TMDB 订阅再建豆瓣订阅时，应合并豆瓣号进既有订阅、不新建幽灵订阅。
    """
    session, _ = _make_session()
    session.execute(Subscribe.__table__.insert(), {
        "name": "躲在超市后门抽烟的两人",
        "year": "2022",
        "type": "电视剧",
        "tmdbid": 296286,
        "season": 1,
        "state": "R",
    })
    session.commit()
    existing_id = session.execute(
        select(Subscribe.id)
    ).scalar_one()

    mediainfo = MediaInfo(
        title="躲在超市后门抽烟的两人",
        year="2022",
        type=MediaType.TV,
        tmdb_id=None,
        douban_id="12345",
    )
    sid, msg = SubscribeOper(db=session).add(mediainfo, season=1)
    session.commit()

    assert sid == existing_id
    assert msg == "订阅已存在"
    rows = session.query(Subscribe).all()
    assert len(rows) == 1
    assert rows[0].doubanid == "12345"
    assert rows[0].tmdbid == 296286


def test_add_dedup_by_title_year():
    """
    仅以标题+年份+季再建同剧订阅时，应命中既有同剧订阅而非新建幽灵订阅。
    """
    session, _ = _make_session()
    session.execute(Subscribe.__table__.insert(), {
        "name": "测试剧",
        "year": "2022",
        "type": "电视剧",
        "tmdbid": 296286,
        "season": 1,
        "state": "R",
    })
    session.commit()
    existing_id = session.execute(
        select(Subscribe.id)
    ).scalar_one()

    mediainfo = MediaInfo(
        title="测试剧",
        year="2022",
        type=MediaType.TV,
        tmdb_id=None,
        douban_id=None,
    )
    sid, msg = SubscribeOper(db=session).add(mediainfo, season=1)
    session.commit()

    assert sid == existing_id
    assert msg == "订阅已存在"
    assert session.query(Subscribe).count() == 1


def test_add_creates_when_truly_different_show():
    """
    不同剧应正常新建订阅，确认跨身份去重不会误合并。
    """
    session, _ = _make_session()
    session.execute(Subscribe.__table__.insert(), {
        "name": "测试剧",
        "year": "2022",
        "type": "电视剧",
        "tmdbid": 296286,
        "season": 1,
        "state": "R",
    })
    session.commit()
    existing_id = session.execute(
        select(Subscribe.id)
    ).scalar_one()

    mediainfo = MediaInfo(
        title="另一部剧",
        year="2023",
        type=MediaType.TV,
        tmdb_id=999999,
        douban_id=None,
    )
    sid, msg = SubscribeOper(db=session).add(mediainfo, season=1)
    session.commit()

    assert sid != existing_id
    assert msg == "新增订阅成功"
    rows = session.query(Subscribe).all()
    assert len(rows) == 2
    assert {row.name for row in rows} == {"测试剧", "另一部剧"}

