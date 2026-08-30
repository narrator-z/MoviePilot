import unittest

from app.core.context import MediaInfo

from app.chain.scraping import ScrapingChain
from app.schemas.types import MediaType


def _make_mediainfo(douban_id=None, bangumi_id=None, tmdb_id=None, mtype=MediaType.TV):
    return MediaInfo(
        type=mtype,
        title="测试剧",
        year="2024",
        douban_id=douban_id,
        bangumi_id=bangumi_id,
        tmdb_id=tmdb_id,
    )


TV_NFO = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<tvshow>\n"
    "  <title>测试剧</title>\n"
    "  <year>2024</year>\n"
    "</tvshow>\n"
)

MOVIE_NFO = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<movie>\n"
    "  <title>测试电影</title>\n"
    "  <year>2024</year>\n"
    "</movie>\n"
)

RESOLVED = {"id": 123456, "external_ids": {"imdb_id": "tt987654"}}


class NfoTmdbIdEnrichTest(unittest.TestCase):
    def _chain(self, resolved):
        chain = object.__new__(ScrapingChain)
        chain.get_tmdbinfo_by_doubanid = lambda doubanid, mtype=None: resolved
        chain.get_tmdbinfo_by_bangumiid = lambda bangumiid: resolved
        return chain

    def test_enrich_tv_douban_injects_ids(self):
        chain = self._chain(RESOLVED)
        mediainfo = _make_mediainfo(douban_id="37441858")
        out = chain._enrich_nfo_with_resolved_ids(TV_NFO.encode("utf-8"), mediainfo)
        out = out.decode("utf-8") if isinstance(out, bytes) else out
        self.assertIn("<tmdbid>123456</tmdbid>", out)
        self.assertIn('<uniqueid type="tmdb"', out)
        self.assertIn("123456", out)
        self.assertIn("<imdbid>tt987654</imdbid>", out)
        self.assertIn('<uniqueid type="imdb"', out)
        # tmdb 为默认，imdb 出现时应降级 tmdb 的 default
        self.assertIn('default="true"', out)

    def test_enrich_movie_douban_injects_ids(self):
        chain = self._chain(RESOLVED)
        mediainfo = _make_mediainfo(douban_id="123", mtype=MediaType.MOVIE)
        out = chain._enrich_nfo_with_resolved_ids(MOVIE_NFO.encode("utf-8"), mediainfo)
        out = out.decode("utf-8") if isinstance(out, bytes) else out
        self.assertIn("<tmdbid>123456</tmdbid>", out)
        self.assertIn("<imdbid>tt987654</imdbid>", out)

    def test_enrich_bangumi_injects_ids(self):
        chain = self._chain(RESOLVED)
        mediainfo = _make_mediainfo(bangumi_id=456)
        out = chain._enrich_nfo_with_resolved_ids(TV_NFO.encode("utf-8"), mediainfo)
        out = out.decode("utf-8") if isinstance(out, bytes) else out
        self.assertIn("<tmdbid>123456</tmdbid>", out)

    def test_enrich_skips_when_tmdb_id_present(self):
        chain = self._chain(RESOLVED)
        mediainfo = _make_mediainfo(douban_id="37441858", tmdb_id=999)
        out = chain._enrich_nfo_with_resolved_ids(TV_NFO.encode("utf-8"), mediainfo)
        out = out.decode("utf-8") if isinstance(out, bytes) else out
        self.assertNotIn("<tmdbid>", out)
        self.assertNotIn("<imdbid>", out)

    def test_enrich_skips_when_no_douban_bangumi_id(self):
        chain = self._chain(RESOLVED)
        mediainfo = _make_mediainfo()
        out = chain._enrich_nfo_with_resolved_ids(TV_NFO.encode("utf-8"), mediainfo)
        out = out.decode("utf-8") if isinstance(out, bytes) else out
        self.assertNotIn("<tmdbid>", out)

    def test_enrich_skips_when_resolver_returns_none(self):
        chain = self._chain(None)
        mediainfo = _make_mediainfo(douban_id="37441858")
        out = chain._enrich_nfo_with_resolved_ids(TV_NFO.encode("utf-8"), mediainfo)
        out = out.decode("utf-8") if isinstance(out, bytes) else out
        self.assertNotIn("<tmdbid>", out)

    def test_enrich_resilient_to_malformed_xml(self):
        chain = self._chain(RESOLVED)
        mediainfo = _make_mediainfo(douban_id="37441858")
        bad = "<tvshow><title>未闭合".encode("utf-8")
        out = chain._enrich_nfo_with_resolved_ids(bad, mediainfo)
        # 异常应回退到原始内容，不抛错
        self.assertEqual(out, bad)


if __name__ == "__main__":
    unittest.main()
