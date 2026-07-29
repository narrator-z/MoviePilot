"""回归测试：_collect_recognize_ids 返回的 ID key 必须与 recognize_media 形参一致。

防止再次出现 `media_id`(带下划线) 误用导致
`ChainBase.recognize_media() got an unexpected keyword argument 'media_id'` 这类 TypeError。
"""
import inspect
from types import SimpleNamespace

from app.chain.transfer import TransferChain


def test_collect_recognize_ids_returns_recognize_media_compatible_keys():
    """下载记录携带 media_id 时，返回 dict 必须含 ``mediaid``(无下划线)，
    且所有 key 都能被 recognize_media 接受。"""
    # 用 SimpleNamespace 充当 self，仅提供方法内部实际依赖的项
    fake_self = SimpleNamespace()
    fake_self._resolve_subscription_tmdbid = lambda task, dh: None

    dh = SimpleNamespace(
        media_id="bgm123",
        tmdbid=None,
        doubanid=None,
        bangumiid=None,
        anilistid=None,
        media_source="bangumi",
        episode_group=None,
    )
    ids = TransferChain._collect_recognize_ids(fake_self, None, dh)
    assert ids is not None
    # 关键回归点：必须是 mediaid 而非 media_id
    assert "mediaid" in ids
    assert "media_id" not in ids
    assert ids["mediaid"] == "bgm123"
    assert ids["source"] == "bangumi"

    # 所有展开后传给 recognize_media 的 key 都必须是合法形参，否则会 TypeError
    valid_params = set(inspect.signature(TransferChain.recognize_media).parameters)
    for key in ids:
        assert key in valid_params, (
            f"{key} 不是 recognize_media 的合法参数，"
            f"经 **ids 展开后将触发 TypeError"
        )
