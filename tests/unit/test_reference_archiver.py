"""services/reference_archiver.py — 封存 runner 的純函式（URL 改寫 + 失敗分類）。

Vimeo 主站抽取器 2026-08 起打不到 OAuth token（Vimeo 撤銷 yt-dlp 內建憑證，
最新 stable 也 401）—— 下載一律改走 player 端點；這裡鎖住改寫規則與
「OAuth 401 要判 extractor（觸發自我更新重試）」的分類契約。
"""
from services.reference_archiver import _classify_failure, _vimeo_player_url


class TestVimeoPlayerUrl:
    def test_public_video(self):
        assert _vimeo_player_url("https://vimeo.com/76979871") == \
            "https://player.vimeo.com/video/76979871"

    def test_unlisted_with_hash(self):
        # 私密雜湊要轉成 ?h=（player 端點的驗證參數）
        assert _vimeo_player_url("https://vimeo.com/960087402/71d3a3c405") == \
            "https://player.vimeo.com/video/960087402?h=71d3a3c405"

    def test_query_and_www_variants(self):
        assert _vimeo_player_url("https://www.vimeo.com/960087402?share=copy") == \
            "https://player.vimeo.com/video/960087402"
        assert _vimeo_player_url("https://vimeo.com/960087402/71d3a3c405/") == \
            "https://player.vimeo.com/video/960087402?h=71d3a3c405"

    def test_non_vimeo_and_non_video_untouched(self):
        # 沒中回 None → 呼叫端用原 URL，別誤傷 showcase/channel 或其他平台
        assert _vimeo_player_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None
        assert _vimeo_player_url("https://vimeo.com/showcase/123") is None
        assert _vimeo_player_url("https://player.vimeo.com/video/123") is None


class TestClassifyFailure:
    def test_oauth_401_is_extractor(self):
        # Vimeo 撤銷憑證的實際 stderr（2026-08）—— 要走自我更新重試路徑
        assert _classify_failure(
            "ERROR: [vimeo] 960087402: Failed to fetch macos OAuth token: "
            "HTTP Error 401: Unauthorized") == "extractor"

    def test_dead_beats_extractor(self):
        assert _classify_failure("Private video. Unable to extract") == "dead"

    def test_plain_401_stays_transient(self):
        assert _classify_failure("HTTP Error 401: Unauthorized") == "transient"
