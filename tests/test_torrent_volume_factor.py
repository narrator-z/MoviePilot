"""get_free_string 对字符串型 volume factor 的兜底测试。

部分索引器（如 JackettExtend）把 uploadvolumefactor / downloadvolumefactor
以字符串返回，'%.2f' 格式化要求实数，会抛出
``TypeError: must be real number, not str``。此测试锁定 float() 兜底行为。
"""

from app.domain.context import TorrentInfo


def test_get_free_string_accepts_string_factors():
    # 字符串因子不应抛错，应正常映射
    assert TorrentInfo.get_free_string("1.0", "1.0") == "普通"
    assert TorrentInfo.get_free_string("1.0", "0.0") == "免费"
    assert TorrentInfo.get_free_string("2.0", "1.0") == "2X"


def test_get_free_string_invalid_string_falls_back():
    # 无法转为实数时回退为“未知”，而非抛异常
    assert TorrentInfo.get_free_string("abc", "1.0") == "未知"
    assert TorrentInfo.get_free_string(None, "1.0") == "未知"


def test_get_free_string_numeric_still_works():
    # 保持数值型原有行为不变
    assert TorrentInfo.get_free_string(1.0, 1.0) == "普通"
    assert TorrentInfo.get_free_string(2, 0) == "2X免费"
