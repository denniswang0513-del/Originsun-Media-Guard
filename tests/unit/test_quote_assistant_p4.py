# -*- coding: utf-8 -*-
"""報價助理 P4（docs/QUOTE_ASSISTANT_PLAN.md）：

- **模型可選**：前端下拉送上來的別名會變成 claude CLI 的 `--model` 參數 → 一律白名單。
- **用價目補上待定價**：比對規則只有一份（後端 `price_book.norm_key`），
  而且**不覆蓋人手打的價**。
- **長截圖**：標了 `data-paste-hires` 的欄位另存一份高解析度副本給 AI 讀，
  清圖時兩個命名空間一起刪。
"""
import pytest

from core import quote_chat
from tests.unit._srcscan import js_code_only, js_func_body, repo_src


# ── 模型白名單 ────────────────────────────────────────────────

@pytest.mark.parametrize("requested,fallback,want", [
    ("sonnet", "", "sonnet"),
    ("  OPUS  ", "", "opus"),                 # 前後空白＋大小寫要正規化
    ("fable", "haiku", "fable"),              # 前端選的優先
    ("", "haiku", "haiku"),                   # 沒選就用 settings 的
    ("", "", quote_chat.DEFAULT_MODEL),
    ("gpt-4", "", quote_chat.DEFAULT_MODEL),  # 不在名單
    ("", "不存在的模型", quote_chat.DEFAULT_MODEL),
])
def test_pick_model_whitelists(requested, fallback, want):
    assert quote_chat.pick_model(requested, fallback) == want


@pytest.mark.parametrize("evil", [
    "--dangerously-skip-permissions", "sonnet --allowedTools Bash",
    "; rm -rf /", "../../claude", "sonnet\n--model opus", None, 123,
])
def test_pick_model_never_lets_a_flag_through(evil):
    """🔴 這個值會直接變成 `--model <值>` —— 名單外一律回預設，不准原樣帶過去。"""
    got = quote_chat.pick_model(evil)
    assert got in quote_chat.ALLOWED_MODELS
    assert got == quote_chat.DEFAULT_MODEL


def test_model_reaches_the_cli_only_through_pick_model():
    src = repo_src("routers/crm/quotes.py")
    body = src[src.index("def _quote_chat_model("):src.index("def _paste_image_paths(")]
    assert "quote_chat.pick_model(" in body
    # CLI 那一行拿到的只能是 _quote_chat_model() 的產物
    assert '"--model", model' in src
    stream = src[src.index("async def _call_claude_stream("):src.index("async def _run_quote_chat(")]
    assert "model" in stream and "body.model" not in stream, "前端的字串不准直接進 CLI"
    assert 'fire(_run_quote_chat(quotation_id, body.model or "")' in src


def test_frontend_remembers_the_model_choice():
    js = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    assert "_CHAT_MODEL_KEY" in js
    send = js_func_body(js, "async function _sendChat()")
    assert "JSON.stringify({ text, model })" in send
    html = repo_src("frontend/tabs/crm/crm-quotes.html")
    assert 'id="quote-chat-model"' in html and 'data-no-search' in html


# ── 用價目補上待定價 ──────────────────────────────────────────

def test_match_endpoint_only_reads():
    src = repo_src("routers/crm/quotes.py")
    body = src[src.index("async def match_price_items("):src.index('@router.post("/price-items/import-history")')]
    assert "price_book.norm_key(" in body, "比對規則跟收價時同一支"
    for forbidden in ("session.add(", "session.commit(", "session.delete("):
        assert forbidden not in body, f"{forbidden}：這支只查不寫"
    assert '"n": n' in body, "回傳的編號要跟 patch 的編號同一套"


def test_fill_prices_never_overwrites_a_typed_price():
    """🔴 只補現在是 0 的 —— 蓋掉人剛打的價比少補一項嚴重得多。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    fn = js_func_body(js, "async function _fillPricesFromBook()")
    assert ".filter(m => !(rows[m.n - 1] || {}).unit_price)" in fn
    assert "_flatItems().filter(it => it.description)" in fn, "要用攤平後的順序，編號才對得上"
    assert "applyQuotePatch(_groups, _terms" in fn
    # 🔴 編輯既有報價時 _autoOn 是關的 —— 補價一定要走 _saveAiChange 才存得下去，
    #    只呼叫 _autoTouch() 的話畫面變了、關窗就丟（2026-09-09 實測踩到）
    assert "_saveAiChange(" in fn
    assert "'/price-items/match'" in fn


def test_ai_changes_are_saved_even_when_autosave_is_off():
    """🔴 編輯既有報價時自動存是關的（怕靜默改舊資料）；但 AI 的 patch 與補價是
    使用者按出來的、畫面也已經變了 —— 不寫回去就是丟資料。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    save = js_func_body(js, "async function _saveAiChange(note)")
    assert "if (_autoOn) { _autoTouch(); return; }" in save, "新增那條路照原本的 debounce 走"
    assert "method: 'PUT'" in save and "_buildPayload()" in save
    apply_ = js_func_body(js, "function _applyNewChatPatches()")
    assert "_saveAiChange(" in apply_ and "_autoQueue(" in apply_, "要排進 _autoChain，關窗才 await 得到"


def test_fill_button_only_shows_when_something_is_missing_a_price():
    js = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    fn = js_func_body(js, "function _renderChatSummary()")
    assert "noPrice ? `<button" in fn and 'id="quote-fill-prices"' in fn
    assert "_fillPricesFromBook" in fn


# ── 長截圖的高解析度副本 ──────────────────────────────────────

def test_hires_copy_is_opt_in_and_shares_the_filename():
    paste = repo_src("routers/api_paste.py")
    assert '_HI_NAMESPACE = "paste-hi"' in paste and "_MAX_SIDE_HI" in paste
    assert 'hires: str = Form("")' in paste
    assert "os.path.splitext(fname)[0]" in paste, "高解析度副本要跟顯示版同檔名（清理時才找得到）"
    assert "_MAX_SIDE = 1600" in paste, "顯示用的仍然是 1600，別為了 AI 把全站貼圖都放大"

    js = js_code_only(repo_src("frontend/js/shared/paste-image.js"))
    assert "ta.dataset.pasteHires !== undefined" in js and "fd.append('hires', '1')" in js
    html = repo_src("frontend/tabs/crm/crm-quotes.html")
    assert "data-paste-hires" in html


def test_ai_reads_the_hires_copy_first_and_purge_deletes_both():
    src = repo_src("routers/crm/quotes.py")
    assert 'PASTE_HI_NS = "paste-hi"' in src
    feed = src[src.index("def _paste_image_paths("):src.index("async def _call_claude_stream(")]
    assert 'for ns in (PASTE_HI_NS, "paste")' in feed, "高解析度優先，沒有才退回顯示用的"
    purge = src[src.index("async def purge_quote_chat_images("):src.index('@router.post("/quotations/{quotation_id}/chat")')]
    assert 'assets_delete("paste", n)' in purge and "assets_delete(PASTE_HI_NS, n)" in purge, \
        "兩份都要刪，不然高解析度那張會留在圖床上"
