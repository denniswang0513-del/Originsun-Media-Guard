# -*- coding: utf-8 -*-
"""每一章一集 podcast（§9.13；owner 2026-09-19）。

  「你能根據這些內容製作聊天式的podcast嗎？」「書本每一章的內容」「每一章一個podcast」
  「內容深度深一點」「我希望podcast 儲存的路徑是D槽」

這支測試的重點有三個，全部都是實測踩出來的：
  1. 稿子的長度要**跟著原章節走** —— 固定長度會讓短章節被硬撐（173 字寫成 5,815 字）
  2. 深度靠**兩趟**（先拆段列素材、再一段一段寫），不是叫它寫長一點
  3. 檔案在那本書自己的資料夾底下（＝跟著 D 槽的書架根目錄走）
"""
import pytest

from core import knowledge_logic as kl
from tests.unit._srcscan import code_only, func_body, js_func_body, repo_src

SRV = "services/knowledge_podcast.py"
ROUTER = "routers/api_knowledge.py"
PUBLIC = "routers/api_knowledge_public.py"
JS = "frontend/js/knowledge/podcast.js"


# ── 1. 長度跟著章節走 ──────────────────────────────────────
def test_a_short_chapter_does_not_get_a_twenty_minute_episode():
    """🔴 2026-09-19 實測：173 字的測試章節被寫成 5,815 字（**33 倍**）。
    規則寫了「不准掰」，但一直逼它產字，它就會重複或延伸 —— 從源頭限長比較實在。"""
    short = kl.podcast_budget(800, 6) * 6
    long = kl.podcast_budget(5045, 7) * 7
    assert short < long, "短章節的稿子要比長章節短"
    # 總長最多就是「下限」與「章節長度 × 上限倍數」裡大的那個（除法的餘數給一點寬容）
    assert short <= max(kl.PODCAST_MIN_CHARS, 800 * kl.PODCAST_EXPAND_MAX) + 60, short


def test_a_normal_chapter_keeps_its_length():
    """鯨豚第二章 5,045 字 → 實際寫出 5,565 字（1.1 倍）＝正常的比例。"""
    total = kl.podcast_budget(5045, 7) * 7
    assert 4500 <= total <= kl.PODCAST_TARGET_CHARS + 200


def test_a_very_long_chapter_is_still_capped():
    """9,871 字那一章不要變成 40 分鐘。"""
    assert kl.podcast_budget(9871, 7) * 7 <= kl.PODCAST_TARGET_CHARS + 200


def test_a_chapter_too_short_is_refused_outright():
    body = code_only(func_body(repo_src(SRV), "async def _write_script("))
    assert "kl.PODCAST_MIN_CHAPTER" in body
    assert "太短" in func_body(repo_src(SRV), "async def _write_script(")


# ── 2. 深度靠兩趟 ──────────────────────────────────────────
def test_the_script_is_written_in_two_passes():
    """🔴 一次寫完整集實測 3,711 字／13.8 分鐘，而且把那一章大量的具體東西
    （人名、年份、頁碼、外文原詞）整批跳過。拆段之後 5,565 字，抽查 12 項全中。"""
    body = func_body(repo_src(SRV), "async def _write_script(")
    assert "podcast_outline_prompt" in body, "第一趟：拆段"
    assert "podcast_section_prompt" in body, "第二趟：一段一段寫"
    assert "for i, sec in enumerate(outline)" in body


def test_the_outline_asks_for_concrete_things():
    p = kl.podcast_outline_prompt("內容")
    assert "人名" in p and "年份" in p and "頁碼" in p
    assert "只能寫筆記裡有的" in p


def test_each_section_must_use_every_fact_and_push_back():
    p = kl.podcast_section_prompt({"point": "x", "facts": ["甲", "乙"]},
                                  body="內容", budget=800, opening="o", closing="c")
    assert "每一項都要出現在對談裡" in p
    assert "追問三次" in p, "深度不是講得多，是講得進去"
    assert "・甲" in p and "・乙" in p
    assert "一個字都不准自己補" in p


def test_the_first_line_says_it_is_not_the_original_book():
    """同 PDF、分享頁的版權界線 —— 這份會被轉出去。"""
    assert "不是原書內容" in kl.podcast_opening("書名", "第 2 章", True)
    assert "不是原書內容" not in kl.podcast_opening("書名", "第 2 章", False)


# ── 3. 稿子解析擋得住亂回 ──────────────────────────────────
def test_a_made_up_speaker_is_dropped():
    rows = kl.parse_podcast_script(
        '前言 [{"who":"主持人","text":"甲"},{"who":"路人","text":"乙"},'
        '{"who":"來賓","text":" 丙 "},{"who":"來賓","text":""}] 尾巴')
    assert rows == [{"who": "主持人", "text": "甲"}, {"who": "來賓", "text": "丙"}]


@pytest.mark.parametrize("bad", ["", "沒有陣列", "[壞的 json", "{}", "[1, 2, 3]"])
def test_rubbish_becomes_an_empty_script_not_a_crash(bad):
    assert kl.parse_podcast_script(bad) == []


# ── 4. 檔案位置 ───────────────────────────────────────────
def test_the_files_live_next_to_the_book():
    """owner 2026-09-19「我希望podcast 儲存的路徑是D槽」——
    放進那本書自己的資料夾就自動跟著書架的根目錄（D:\\Originsun-Knowledge\\books）走，
    而且刪書一起刪、dev 與正式站天然分開。"""
    body = func_body(repo_src(SRV), "def podcast_dir(")
    assert "ks.book_dir(book_id)" in body, "🔴 id 只能從 book_dir 變成路徑"
    assert "kl.PODCAST_DIR" in body


@pytest.mark.parametrize("bad", ["../x.mp3", "ch1.mp3", "ch05.wav", "ch05.exe", ""])
def test_a_made_up_filename_never_becomes_a_path(bad):
    assert not kl.is_valid_podcast(bad)


def test_the_name_is_built_from_the_chapter_number():
    assert kl.podcast_name(5) == "ch05.mp3" and kl.podcast_name(5, "json") == "ch05.json"
    assert kl.podcast_name(0) == "" and kl.podcast_name(100) == "" and kl.podcast_name("x") == ""


def test_the_progress_lives_only_in_memory():
    """同編譯：進度不落檔。行程重啟＝沒有半成品的鬼狀態卡在那裡。"""
    from services import knowledge_podcast as kpod
    assert isinstance(kpod._making, dict)
    assert "_making" in repo_src(SRV) and "write" not in func_body(repo_src(SRV), "def podcast_state(")


def test_deleting_a_book_clears_its_progress():
    """不清的話那本書的鍵會永遠留在記憶體裡，重上傳同一個 id 會直接 409。"""
    assert "kpod.cancel_all(book_id)" in func_body(repo_src(ROUTER), "async def delete_book(")


# ── 5. 權限 ───────────────────────────────────────────────
def test_every_podcast_endpoint_is_guarded():
    src = repo_src(ROUTER)
    for fn in ("async def podcast_state(", "async def podcast_make_all(",
               "async def podcast_make_one(", "async def podcast_audio(",
               "async def podcast_script("):
        assert "_guard(request)" in func_body(src, fn), fn


def test_making_one_needs_the_master():
    """claude CLI 與 ffmpeg 都只在主控上 —— NAS 按了要馬上講，不要等背景跑完才說。"""
    src = repo_src(ROUTER)
    for fn in ("async def podcast_make_all(", "async def podcast_make_one("):
        assert "_require_claude()" in func_body(src, fn), fn


def test_the_audio_is_not_cached_anywhere():
    assert "no_store_file(" in func_body(repo_src(ROUTER), "async def podcast_audio(")
    assert "no_store_file(" in func_body(repo_src(PUBLIC), "async def shared_podcast(")


def test_the_public_side_needs_its_own_tick():
    """owner 勾了「可以聽 podcast」才給；預設關著（內容是原書的整理）。"""
    assert "podcast" in kl.SHARE_ABILITIES
    assert "podcast" not in kl.SHARE_DEFAULT
    body = func_body(repo_src(SRV), "def public_podcast(")
    assert 'shares(kshare.shared_parts(book_id), "podcast")' in body
    assert "kshare.public_podcast" not in repo_src(PUBLIC), "公開那支只從 knowledge_podcast 拿路徑"


# ── 6. 畫面 ───────────────────────────────────────────────
def test_the_private_player_carries_the_token():
    """🔴 音檔是私有的：`<audio src="/api/…">` 送不了 Authorization（同章節的圖）。"""
    body = js_func_body(repo_src(JS), "export async function fillPodcastAudio(")
    assert "bearerHeader()" in body and "createObjectURL" in body


def test_the_polling_stops_when_nothing_is_running():
    body = js_func_body(repo_src(JS), "export function watchPodcast(")
    assert "stopPodcastWatch()" in body
    assert "clearInterval(S.podcastTimer)" in repo_src("frontend/js/knowledge/ctx.js"), \
        "離開頁面要把輪詢收掉（同其他四條）"


def test_remaking_asks_first():
    body = js_func_body(repo_src(JS), "export async function makePodcast(")
    assert "confirm(" in body, "重產會蓋掉舊的那一集"


def test_the_module_is_in_the_import_map():
    assert "/js/knowledge/podcast.js" in repo_src("frontend/knowledge.html")
