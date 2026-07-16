"""services/website/video_utils.py — parse_video_url 多平台影片連結解析。

契約測試：描述子形狀（provider/video_id/embed_url）是前後端共同契約，
website/src 的 embed 元件直接消費這些欄位 — 改欄位名/embed 格式必先改這裡。
"""
import urllib.parse

from services.website.video_utils import _extract_youtube_id, parse_video_url


class TestYouTube:
    """四種常見 YouTube URL 形態都要解出同一個描述子。"""

    def _assert_yt(self, url, expected_id):
        d = parse_video_url(url)
        assert d == {
            "provider": "youtube",
            "video_id": expected_id,
            "embed_url": f"https://www.youtube-nocookie.com/embed/{expected_id}",
        }

    def test_watch_url(self):
        self._assert_yt("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ")

    def test_short_url(self):
        self._assert_yt("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ")

    def test_embed_url(self):
        self._assert_yt("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ")

    def test_shorts_url(self):
        self._assert_yt("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ")

    def test_extract_youtube_id_reexport_behavior(self):
        # 搬家後行為不變（notion_service 舊 import 點也吃這份）
        assert _extract_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert _extract_youtube_id("not a url") is None


class TestVimeo:
    def test_vimeo_url(self):
        d = parse_video_url("https://vimeo.com/12345678")
        assert d == {
            "provider": "vimeo",
            "video_id": "12345678",
            "embed_url": "https://player.vimeo.com/video/12345678",
        }


class TestFacebook:
    def test_page_video_url(self):
        url = "https://www.facebook.com/YueJinArtMuseum/videos/1132182508265760/"
        d = parse_video_url(url)
        assert d["provider"] == "facebook"
        assert d["video_id"] is None
        # embed = plugins/video.php 包 URL-encoded 原始連結 + show_text=false
        expected_href = urllib.parse.quote(url, safe="")
        assert d["embed_url"] == (
            "https://www.facebook.com/plugins/video.php"
            f"?href={expected_href}&show_text=false"
        )
        assert d["embed_url"].startswith("https://www.facebook.com/plugins/video.php?href=")
        assert d["embed_url"].endswith("&show_text=false")

    def test_fb_watch_short_url(self):
        d = parse_video_url("https://fb.watch/abc123/")
        assert d["provider"] == "facebook"
        assert d["video_id"] is None
        assert "plugins/video.php" in d["embed_url"]
        assert "show_text=false" in d["embed_url"]

    def test_facebook_watch_url(self):
        d = parse_video_url("https://www.facebook.com/watch?v=1132182508265760")
        assert d["provider"] == "facebook"
        assert d["video_id"] is None
        assert "plugins/video.php" in d["embed_url"]
        assert "show_text=false" in d["embed_url"]


class TestGenericAndInvalid:
    def test_random_https_url_is_link(self):
        d = parse_video_url("https://example.com/some/video-page")
        assert d == {"provider": "link", "video_id": None, "embed_url": None}

    def test_empty_string_is_none(self):
        assert parse_video_url("") is None

    def test_whitespace_is_none(self):
        assert parse_video_url("   ") is None

    def test_garbage_text_is_none(self):
        assert parse_video_url("這不是網址 just some text") is None
