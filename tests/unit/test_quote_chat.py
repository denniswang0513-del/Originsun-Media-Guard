# -*- coding: utf-8 -*-
"""對話式完成報價（docs/QUOTE_ASSISTANT_PLAN.md）的純規則。

兩半：
- 後端 `core/quote_chat.py` —— 組提示、把 claude 回的東西正規化成固定形狀。
- 前端 `frontend/js/shared/quote-patch.js` —— 把 patch 套進草稿（用 node 真的跑，不是掃字串）。

釘的規則（都是「做錯會靜默毀資料」那一類）：
- patch 的編號＝項目攤平後的順序，**前後端同一套**。
- 只動 patch 指名的項目，其餘原封不動（聊到第 8 輪不准洗掉第 3 輪手改的單價）。
- claude 回垃圾時回空 patch，不准讓半套 patch 去改報價單。
- 提示裡不准出現內部成本（那個不出這台機器）。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core import quote_chat
from tests.unit._srcscan import js_code_only, js_func_body, repo_src

_REPO = Path(__file__).resolve().parents[2]
PATCH_JS = "frontend/js/shared/quote-patch.js"


# ── 提示 ──────────────────────────────────────────────────────

def _quotation():
    return {
        "client_short_name": "遠見", "project_name": "2026 國際論壇",
        "spec": "論壇現場轉播", "payment_stages": [{"label": "簽約", "pct": 30}],
        "terms": "字幕由客戶校正\n IG 要直式 ",
        "items": [
            {"group_name": "拍攝", "description": "雙機拍攝", "unit": "天",
             "quantity": 2, "unit_price": 35000, "internal_cost": 9999},
            {"group_name": "後期", "description": "精華影片", "unit": "支",
             "quantity": 3, "unit_price": 0, "internal_cost": 8888},
        ],
    }


def test_item_lines_number_from_one_and_flag_missing_price():
    text = quote_chat.item_lines(_quotation()["items"])
    lines = text.splitlines()
    assert lines[0].startswith("1 [拍攝] 雙機拍攝 2 天 × 35000")
    assert lines[1].startswith("2 [後期] 精華影片 3 支 × 0")
    assert "待定價" in lines[1] and "待定價" not in lines[0]


def test_snapshot_never_leaks_internal_cost():
    """內部成本／毛利不出這台機器（規劃正本 §8）。"""
    snap = quote_chat.snapshot(_quotation())
    assert "9999" not in snap and "8888" not in snap
    assert "遠見" in snap and "2026 國際論壇" in snap
    assert "簽約 30%" in snap
    assert "1. 字幕由客戶校正" in snap and "2. IG 要直式" in snap


def test_build_prompt_carries_draft_history_and_images():
    chat = [quote_chat.message("user", "幫我報價 paste:" + "a" * 32 + ".webp", "2026-09-09T10:00:00")]
    prompt = quote_chat.build_prompt(_quotation(), chat, ["C:/tmp/a.webp"])
    assert "只輸出 JSON" in prompt
    assert "雙機拍攝" in prompt
    assert "使用者：幫我報價 （截圖）" in prompt, "token 對 LLM 沒意義，換成人話；圖另外用路徑餵"
    assert "C:/tmp/a.webp" in prompt


def test_history_is_capped():
    chat = [quote_chat.message("user", f"第{i}句", "2026-09-09T10:00:00") for i in range(60)]
    lines = quote_chat.history_lines(chat).splitlines()
    assert len(lines) == quote_chat.MAX_HISTORY_TURNS
    assert lines[-1].endswith("第59句")


# ── 貼圖 token ────────────────────────────────────────────────

def test_paste_tokens_dedupe_and_keep_order():
    a, b = "a" * 32, "b" * 32
    text = f"前 paste:{a}.webp 中 paste:{b}.webp 後 paste:{a}.webp"
    assert quote_chat.paste_tokens(text) == [f"{a}.webp", f"{b}.webp"]
    assert quote_chat.paste_tokens("沒有圖") == []
    assert "paste:" not in quote_chat.strip_paste_tokens(text)


# ── 回覆正規化 ────────────────────────────────────────────────

_GOOD = {
    "reply": "整理好了",
    "patch": {
        "add": [{"group_name": "拍攝", "description": "空拍", "unit": "天",
                 "quantity": "1", "unit_price": "12000"},
                {"description": "", "unit_price": 100}],          # 沒描述 → 丟掉
        "update": [{"n": 2, "unit_price": 6000}, {"n": 0, "unit_price": 1}, {"n": 3}],
        "remove": [5, 5, 0, "7"],
    },
    "needs_price": ["短影音", "  "],
    "questions": [f"Q{i}" for i in range(9)],
    "terms_add": ["字幕客戶自校"],
}


def test_parse_reply_normalizes_and_drops_junk():
    p = quote_chat.parse_reply(json.dumps(_GOOD, ensure_ascii=False))
    assert p["ok"] is True and p["reply"] == "整理好了"
    assert p["patch"]["add"] == [{"group_name": "拍攝", "description": "空拍",
                                  "unit": "天", "quantity": 1, "unit_price": 12000}]
    assert p["patch"]["update"] == [{"n": 2, "unit_price": 6000}], "沒編號／沒欄位的都不算數"
    assert p["patch"]["remove"] == [5, 7], "去重、丟掉非法編號"
    assert p["needs_price"] == ["短影音"]
    assert len(p["questions"]) == quote_chat.MAX_QUESTIONS, "一次最多問這麼多題"
    assert p["terms_add"] == ["字幕客戶自校"]


def test_parse_reply_strips_code_fence():
    raw = "```json\n" + json.dumps({"reply": "好", "patch": {}}, ensure_ascii=False) + "\n```"
    p = quote_chat.parse_reply(raw)
    assert p["ok"] is True and p["reply"] == "好"
    assert p["patch"] == quote_chat.EMPTY_PATCH


@pytest.mark.parametrize("raw", ["", "我覺得應該這樣報", "{壞掉的 json", "[1,2,3]"])
def test_parse_reply_never_raises_and_never_half_applies(raw):
    """寧可讓使用者看到「再說一次」，也不要讓半套 patch 去改報價單。"""
    p = quote_chat.parse_reply(raw)
    assert p["ok"] is False
    assert p["patch"] == quote_chat.EMPTY_PATCH
    assert p["questions"] == [] and p["needs_price"] == []
    assert isinstance(p["reply"], str) and p["reply"]


def test_update_only_carries_fields_that_were_sent():
    """沒帶的欄位不動 —— 整包覆寫會洗掉使用者剛手改的那格。"""
    p = quote_chat.parse_reply(json.dumps(
        {"patch": {"update": [{"n": 1, "quantity": 3, "description": " 精華 "}]}}))
    assert p["patch"]["update"] == [{"n": 1, "quantity": 3, "description": "精華"}]


# ── 前端：applyQuotePatch（用 node 真的跑）─────────────────────

_NODE_HARNESS = """
import { applyQuotePatch } from '%s';
const groups = [
  { name: '拍攝', items: [
      { description: 'A', unit: '天', quantity: 2, unit_price: 35000 },
      { description: 'B', unit: '式', quantity: 1, unit_price: 0 } ] },
  { name: '後期', items: [
      { description: 'C', unit: '支', quantity: 3, unit_price: 26000 } ] },
];
const terms = ['既有備註', ''];
const out = applyQuotePatch(groups, terms, {
  add: [{ group_name: '拍攝', description: 'D', unit: '式', quantity: 1, unit_price: 12000 },
        { group_name: '新組',  description: 'E', unit: '式', quantity: 1, unit_price: 500 }],
  update: [{ n: 2, unit_price: 6000 }],
  remove: [3],
  terms_add: ['既有備註', '新的備註'],
});
console.log(JSON.stringify({
  out,
  untouched: groups[0].items[1].unit_price,   // 原始陣列不准被改到
  origLen: groups.length,
}));
"""


def _run_node(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("這台沒有 node")
    r = subprocess.run([node, "--input-type=module"], input=script.encode("utf-8"),
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return json.loads(r.stdout.decode("utf-8"))


def test_apply_patch_is_pure_and_only_touches_named_items():
    url = (_REPO / PATCH_JS).as_uri()
    got = _run_node(_NODE_HARNESS % url)
    out, groups = got["out"], got["out"]["groups"]

    assert got["untouched"] == 0 and got["origLen"] == 2, "純函式：傳進去的陣列不准被改"

    descs = [(g["name"], it["description"], it["unit_price"])
             for g in groups for it in g["items"]]
    # 原本攤平＝A(1) B(2) C(3)：update n=2 改 B 的價、remove 3 砍掉 C、
    # add D 進既有的「拍攝」、add E 開一個新組
    assert ("拍攝", "B", 6000) in descs, "update 用編號找到第 2 項"
    assert all(d[1] != "C" for d in descs), "remove 用編號砍掉第 3 項"
    assert ("拍攝", "D", 12000) in descs, "同名大項目就掛進去"
    assert ("新組", "E", 500) in descs, "沒有的大項目才新開一個"
    assert ("拍攝", "A", 35000) in descs, "沒被指名的項目原封不動"
    assert out["applied"] == {"added": 2, "updated": 1, "removed": 1, "terms": 1}
    assert out["terms"] == ["既有備註", "新的備註"], "重複的備註不再加一次；空的那列拿來填"


def test_apply_patch_ignores_out_of_range_numbers():
    url = (_REPO / PATCH_JS).as_uri()
    script = ("import { applyQuotePatch } from '%s';\n"
              "const g=[{name:'a',items:[{description:'X',unit:'式',quantity:1,unit_price:100}]}];\n"
              "const o=applyQuotePatch(g,[], {update:[{n:9,unit_price:1}],remove:[42],add:[]});\n"
              "console.log(JSON.stringify(o));") % url
    out = _run_node(script)
    assert out["applied"] == {"added": 0, "updated": 0, "removed": 0, "terms": 0}
    assert out["groups"][0]["items"][0]["unit_price"] == 100, "編號對不上就跳過，不亂猜"


# ── 契約：兩邊用同一套編號、後端不直接改項目 ──────────────────

def test_single_writer_contract_is_documented_and_kept():
    """🔴 後端只算 patch、不改項目 —— 兩邊都寫的話前端下一發自動存會靜默蓋掉。"""
    src = repo_src("routers/crm/quotes.py")
    runner = src[src.index("async def _run_quote_chat("):src.index("@router.post(\"/quotations/{quotation_id}/chat\")")]
    assert "q.chat = chat" in runner
    for forbidden in ("_save_items(", "_calc_quotation(", "q.subtotal", "q.total ="):
        assert forbidden not in runner, f"{forbidden}：後端不准直接改項目或金額"

    js = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    send = js_func_body(js, "async function _sendChat()")
    assert "_autoFlush" in send, "送出前要先把自動存 flush 掉，順序不一致編號會改到別人"
    apply_ = js_func_body(js, "function _applyNewChatPatches()")
    assert "_chatApplied" in apply_
    assert "applyQuotePatch(_groups, _terms" in apply_
    # 套完一定要寫回去 —— 編輯既有報價時 _autoOn 是關的，只 _autoTouch() 會靜默丟掉
    assert "_saveAiChange(" in apply_


def test_chat_is_not_carried_in_the_list_payload():
    """幾百則對話不該跟著報價清單一起送。"""
    src = repo_src("routers/crm/quotes.py")
    to_dict = src[src.index("def _to_quotation_dict("):src.index("def _item_to_dict(")]
    assert "chat" not in to_dict
    assert '@router.get("/quotations/{quotation_id}/chat"' in src


# ── 手機版報價助理（frontend/m/views/quote-chat.js）────────────

def test_mobile_assistant_shares_the_backend_and_the_patch_rules():
    """手機與桌機同一組後端、同一支 patch 純函式 —— 兩邊不會漂掉。"""
    js = js_code_only(repo_src("frontend/m/views/quote-chat.js"))
    assert "from '/js/shared/quote-patch.js'" in js, "patch 套用只有一份實作"
    for path in ("/chat", "/price-items/match"):
        assert path in js, path
    apply_ = js_func_body(js, "async function applyNew()")
    assert "S.applied" in apply_ and "applyQuotePatch(S.groups, S.terms" in apply_
    assert "await save()" in apply_, "套完要存回去（手機這邊就是寫入者）"


def test_mobile_put_carries_back_the_fields_the_backend_overwrites():
    """🔴 update_quotation 對這四個欄位是**無條件覆寫**（不看 model_fields_set）——
    少帶一個就是靜默清掉它。"""
    js = js_code_only(repo_src("frontend/m/views/quote-chat.js"))
    save = js_func_body(js, "async function save()")
    for field in ("tax_rate: S.q.tax_rate", "final_price: S.q.final_price",
                  "payment_stages: S.q.payment_stages", "terms: termsText()"):
        assert field in save, f"{field}：原值沒帶回去會被洗掉"
    assert "method: 'PUT'" in save


def test_mobile_entry_is_gated_and_new_files_dodge_the_cache_trap():
    m = js_code_only(repo_src("frontend/m/views/quotes.js"))
    assert "isAdmin() ? `<button" in m and 'data-ai="${esc(q.id)}"' in m, "AI 鈕跟編輯同一把鑰匙"
    assert "openQuoteChat(a.dataset.ai" in m
    # 🔴 新功能走**新檔案**，不往 shell.js 加 export：CF 給 .js 4 小時快取，
    #    舊分頁的 named import 拿不到新 export 會讓整個模組載入失敗
    shell = repo_src("frontend/m/shell.js")
    assert "openQuoteChat" not in shell and "quote-chat" not in shell
