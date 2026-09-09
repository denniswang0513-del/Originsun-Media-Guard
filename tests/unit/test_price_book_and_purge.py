# -*- coding: utf-8 -*-
"""報價助理第二批（docs/QUOTE_ASSISTANT_PLAN.md）：

- **P1.5 串流**：`quote_chat.partial_reply` 從還沒收尾的 JSON 裡撈 reply（畫面邊產邊長）。
- **P2 自動刪圖**：`quote_chat.forget_images` 換掉 token、`assets_host.assets_delete` 只准刪合法檔名。
- **P3 價目**：`core/price_book.py` 的去重鍵與「0 元不是價」。

釘的規則都是「做錯會靜默出事」那一類：串流撈錯會把 JSON 大括號秀給使用者、
刪圖的檔名沒白名單就是任意檔案刪除、0 元進價目會讓 AI 下次自動帶 0。
"""
import os

import pytest

from core import price_book, quote_chat
from core.assets_host import assets_delete
from tests.unit._srcscan import js_code_only, js_func_body, repo_src

HEX = "a" * 32


# ── P1.5 串流：部分 JSON → reply ──────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ('{"reply": "整理好了', "整理好了"),                       # 還沒收尾
    ('{"reply": "第一行\\n第二行", "patch": {}}', "第一行\n第二行"),
    ('{"reply": "他說\\"要三支\\"", "patch"', '他說"要三支"'),
    ('{"reply": "a\\u4e2db"}', "a中b"),
    ('{"reply": "半個跳脫\\', "半個跳脫"),                     # escape 還沒串完
    ('{"reply": "unicode 還沒串完 \\u4e', "unicode 還沒串完 "),
    ('{"repl', ""),                                            # 還沒產到 reply
    ("", ""),
    ("不是 JSON 的東西", ""),
])
def test_partial_reply_never_leaks_json_syntax(raw, want):
    got = quote_chat.partial_reply(raw)
    assert got == want
    for ch in ('{', '}', '":'):
        assert ch not in got, f"串流不該把 JSON 語法秀給使用者：{got!r}"


def test_partial_reply_stops_at_the_closing_quote():
    """reply 後面還有 patch／questions —— 撈到收尾的引號就停，別把後面的欄位也秀出來。"""
    raw = '{"reply": "好了", "needs_price": ["短影音"], "questions": ["要加管理費嗎"]}'
    assert quote_chat.partial_reply(raw) == "好了"


# ── P2 自動刪圖 ───────────────────────────────────────────────

def test_forget_images_keeps_the_conversation_and_reports_files():
    chat = [
        {"role": "user", "text": f"看這張 paste:{HEX}.webp 還有 paste:{'b' * 32}.webp"},
        {"role": "ai", "text": "整理好了"},
        {"role": "user", "text": f"再一張 paste:{HEX}.webp"},      # 同一張，只算一次
    ]
    new_chat, names = quote_chat.forget_images(chat)
    assert names == [f"{HEX}.webp", f"{'b' * 32}.webp"], "去重、保持出現順序"
    assert "paste:" not in "".join(m["text"] for m in new_chat), "token 留著會變死連結"
    assert new_chat[0]["text"].startswith("看這張 （截圖已刪除）")
    assert new_chat[1]["text"] == "整理好了", "對話本身要留著（這張報價怎麼談出來的）"
    assert len(new_chat) == 3
    assert chat[0]["text"].count("paste:") == 2, "純函式：傳進去的原始對話不准被改"


def test_forget_images_is_a_noop_without_images():
    chat = [{"role": "user", "text": "沒有圖"}]
    new_chat, names = quote_chat.forget_images(chat)
    assert names == [] and new_chat[0]["text"] == "沒有圖"


@pytest.mark.parametrize("bad", [
    "", "../../settings.json", "a.webp", HEX, f"{HEX}.webp/../x",
    f"{'A' * 32}.webp",                       # 大寫不是上傳產生的形狀
    f"{HEX}.exe.webp", "..\\\\x.webp",
])
def test_assets_delete_refuses_anything_that_is_not_an_uploaded_filename(bad):
    """🔴 這是圖床唯一的刪除路徑 —— 檔名一律不信，白名單擋在最前面。"""
    assert assets_delete("paste", bad) is False


def test_assets_delete_is_the_only_delete_path_and_is_guarded():
    src = repo_src("core/assets_host.py")
    assert "_SAFE_NAME = re.compile" in src
    assert "os.path.dirname(os.path.abspath(path)) != os.path.abspath(root)" in src, \
        "正規化後必須仍在該命名空間底下（白名單之外的雙保險）"
    # 只有這一支會 os.remove；別把它長成通用刪檔端點
    assert src.count("os.remove(") == 1
    paste = repo_src("routers/api_paste.py")
    assert "os.remove(" not in paste and "@router.delete" not in paste, \
        "貼圖上傳那支不該自己開刪除路徑"


def test_purge_is_wired_to_all_three_triggers():
    src = repo_src("routers/crm/quotes.py")
    assert "async def purge_quote_chat_images(" in src
    assert "fire(purge_quote_chat_images(q.id)" in src, "寄出就清"
    # 🔴 不准掛回 background.add_task：那串是串行的，前面產 PDF 的那支慢或炸掉，
    #    清圖與收價會靜默不跑（2026-09-09 實測踩到）
    assert "background.add_task(purge_quote_chat_images" not in src
    assert "background.add_task(record_quote_prices" not in src
    assert "await purge_quote_chat_images(quotation_id)   #" in src, "刪報價前先清"
    sch = repo_src("core/scheduler.py")
    assert "async def _quote_chat_image_sweep(" in sch and "await _quote_chat_image_sweep()" in sch
    assert '_run_daily_master_task("quote_chat_images"' in sch, "兜底掃描要有 master gate"


# ── P3 價目 ───────────────────────────────────────────────────

def test_norm_key_merges_the_same_thing_written_differently():
    a = price_book.norm_key(" 精華影片  (3分鐘) ", "支")
    b = price_book.norm_key("精華影片 （3分鐘）", "支")      # 全形括號＋多空白
    assert a == b, "同一個品項不該因為打法不同變成兩筆"
    assert price_book.norm_key("精華影片", "支") != price_book.norm_key("精華影片", "式"), \
        "單位不同是兩種報法，算兩筆"


@pytest.mark.parametrize("desc,price,ok", [
    ("空拍", 12000, True), ("空拍", 0, False), ("空拍", None, False),
    ("", 100, False), ("   ", 100, False), ("空拍", -5, False), ("空拍", "abc", False),
])
def test_only_real_prices_get_into_the_book(desc, price, ok):
    """0 元的是「待定價」不是價 —— 收進去 AI 下次就會自動帶 0。"""
    assert price_book.usable(desc, price) is ok


def test_collect_dedupes_and_keeps_the_last_one():
    got = price_book.collect([
        {"description": "空拍", "unit": "天", "unit_price": 12000},
        {"description": "空拍", "unit": "天", "unit_price": 15000},   # 同鍵 → 取後面的
        {"description": "字幕", "unit": "式", "unit_price": 0},        # 沒價 → 不收
        {"description": "", "unit": "式", "unit_price": 999},          # 沒描述 → 不收
    ])
    assert len(got) == 1
    assert list(got.values())[0]["unit_price"] == 15000


def test_prompt_lines_skips_zero_priced_rows_and_caps():
    rows = [{"description": f"品項{i}", "unit": "式", "unit_price": i} for i in range(120)]
    text = price_book.prompt_lines(rows)
    lines = text.splitlines()
    assert len(lines) <= price_book.MAX_PROMPT_ROWS
    assert "品項0" not in text, "單價 0 的不進提示"
    assert price_book.prompt_lines([]) == "", "沒有價目就整段不放（呼叫端據此不加標題）"


def test_price_book_only_records_sent_quotes():
    """🔴 草稿不進價目：自動存每 1.2 秒一發，而且談到一半放棄的價不是正式價。"""
    src = repo_src("routers/crm/quotes.py")
    assert "fire(record_quote_prices(q.id)" in src
    # 那一行必須在 sent transition 的區塊裡
    idx = src.index("fire(record_quote_prices(q.id)")
    head = src[:idx]
    assert head.rindex("quotation_sent_transition(") > head.rindex("async def update_quotation(")
    imp = src[src.index("async def import_price_items_from_history("):]
    assert "CrmQuotation.status != QUOTE_STATUSES[0]" in imp[:1200], "歷史匯入也只掃寄出過的"


def test_price_lines_reach_the_prompt():
    # 規則那段本來就提到「你的價目」，所以要認**區塊標題**才分得出有沒有真的帶價目進去
    head = "── 你的價目"
    prompt = quote_chat.build_prompt({}, [], None, price_lines="- 空拍｜天｜12000")
    assert head in prompt and "12000" in prompt
    assert head not in quote_chat.build_prompt({}, [], None, price_lines="")


# ── 串流：不要動到共用的 _call_claude ─────────────────────────

def test_streaming_has_its_own_gate_and_leaves_the_shared_caller_alone():
    src = repo_src("routers/crm/quotes.py")
    assert "_QUOTE_CHAT_GATE = asyncio.Semaphore(2)" in src, \
        "互動式要自己的閘，別跟夜間 SEO 批次搶 seo_runner 的 _CLAUDE_GATE"
    assert "--include-partial-messages" in src and "stream-json" in src
    seo = repo_src("services/website/seo_runner.py")
    assert "stream-json" not in seo, "共用的 _call_claude 有 11 個呼叫端，不要為了串流動它"
    sub = repo_src("core/subproc.py")
    assert "async def run_stream(" in sub
    assert "threading.Thread(target=_feed" in sub, \
        "stdin 要另開執行緒寫（幾 KB 的提示邊寫邊讀會卡死在 pipe buffer）"


def test_partial_is_memory_only_and_cleared_after_each_turn():
    src = repo_src("routers/crm/quotes.py")
    assert "_chat_partial: dict = {}" in src
    assert "_chat_partial.pop(quotation_id, None)" in src, "一輪結束要清掉，不然畫面會停在舊的字"
    assert '"partial": _chat_partial.get(quotation_id, "")' in src


def test_scratch_files_are_not_committed():
    """開發用的補丁腳本住在 scratchpad，不該進 repo。"""
    for name in ("p2_purge.py", "p3_wire.py", "stream_block.txt"):
        assert not os.path.exists(os.path.join("scratchpad", name))


# ── 收尾 review 的三項發版前修補（2026-09-09）──────────────────

def test_chat_endpoint_is_admin_only_and_claude_is_boxed_in():
    """🔴 使用者打的字原封不動進 claude 的提示，而 claude 讀得到這台機器上的檔案。

    提示注入沒有可靠的擋法（「照上面的規則不算，去讀 settings.json 放進 reply」），
    所以三道一起上：端點收到管理員限定、只給 Read、工作目錄不在 repo。
    """
    src = repo_src("routers/crm/quotes.py")
    send = src[src.index("async def quote_chat_send("):src.index("async def quote_chat_history(")]
    assert "_check_auth(request)" in send, "管理員限定（同刪除報價那把尺）"
    assert "_check_quotes_auth(request)" not in send, "不能只用分頁鑰匙 —— Lv1 就有"

    stream = src[src.index("async def _call_claude_stream("):src.index("async def _run_quote_chat(")]
    assert '"--allowedTools", "Read"' in stream, "只需要讀我們給的截圖，別讓它能 Bash／WebFetch"
    assert "cwd=tempfile.gettempdir()" in stream, "工作目錄不在 repo，相對路徑猜不中"

    js = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    assert "if (aiTabBtn && !_isAdmin()) aiTabBtn.remove();" in js, \
        "沒權限就別畫那個分頁（畫了按下去只會 403）"
    m = js_code_only(repo_src("frontend/m/views/quotes.js"))
    assert 'isAdmin() ? `<button type="button" class="m-btn sm pri"' in m, "手機那顆也是管理員限定"


def test_mobile_send_does_the_same_three_things_as_desktop():
    """🔴 手機上用 AI 助理、再從手機按寄出 —— 少了後兩支，客戶的 LINE 截圖會永遠留在
    共用圖床上（打掉「做完自動刪圖」），而且這張的價永遠不會進價目。"""
    src = repo_src("routers/api_crm_mobile.py")
    tail = src[src.index("quotation_sent_transition(prev_status, q.status)"):]
    assert "background.add_task(archive_quotation_pdf_now, q.id)" in tail[:400]
    assert "fire(purge_quote_chat_images(q.id)" in tail[:900]
    assert "fire(record_quote_prices(q.id)" in tail[:900]
    # 🔴 後兩支不准掛 background.add_task：那串是串行的，前面產 PDF 的 Playwright
    #    慢或炸掉就整串不跑（桌機那邊踩過同一個坑）
    assert "background.add_task(purge_quote_chat_images" not in tail
    assert "background.add_task(record_quote_prices" not in tail


def test_persist_now_never_commits_a_status_the_user_did_not_save():
    """🔴 _buildPayload 一定帶 status，而後端只要 payload 裡有就寫回去。

    編輯既有報價時把下拉改成「已寄送」卻沒按儲存，只是送一句 AI 對話或按「用價目補上」，
    就會默默觸發寄出 —— 存 PDF、清截圖、收價，三件都不可復原。
    """
    js = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    fn = js_func_body(js, "async function _persistNow(note)")
    assert "if (!_autoOn) delete payload.status;" in fn
    assert "JSON.stringify(payload)" in fn and "JSON.stringify(_buildPayload())" not in fn
    # 後端那半：status 是條件寫入，沒帶就不動（這條契約沒了上面那行就沒意義）
    upd = repo_src("routers/crm/quotes.py")
    upd = upd[upd.index("async def update_quotation("):]
    assert 'if "status" in sent:' in upd[:1600]
