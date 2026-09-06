# -*- coding: utf-8 -*-
"""員工端 /my.html 的福委會卡片（owner 2026-08-21）。

> 「我希望他的申請與狀況可以在員工的 my 網頁裡被看到」

「申請」原本就畫得出來，真正沒被看到的是**狀況**：

  🔴 退回原因完全沒顯示 —— 退回是唯一需要員工動手的狀態，卻不告訴他要改什麼
  🔴 只畫最近 8 筆而且沒說被截掉（有人 22 筆）—— 舊的那筆在畫面上直接消失
  🔴 「我用了多少」看不到：畫面上只有池餘額，那是**全公司共用**的那一份，
     員工會把它當成自己的額度

外加一個授權面的事實：這個卡片由 `me_benefits` 模組閘住，
生產上一開始**沒有任何帳號有這個 key** —— 所以誰都看不到。
"""
import re

from tests.unit._srcscan import code_only, func_body, repo_src

MY = "frontend/my.html"
SRC = "routers/crm/benefits.py"


def _card():
    """卡片的整段程式碼。"""
    s = repo_src(MY)
    i = s.index("function cardBenefits(")
    j = s.index("\nfunction ", i + 10)
    return s[i:j]


# ── 後端：我自己用了多少 ──────────────────────────────────────────

def test_me_returns_my_own_usage_not_just_the_pool():
    """池餘額是全公司共用的，回答不了「我用了多少」。"""
    body = code_only(func_body(repo_src(SRC), "async def my_benefits("))
    for k in ("mine_used", "mine_pending", "mine_count"):
        assert k in body, f"沒有回 {k}"


def test_my_usage_uses_the_shared_rule_not_a_second_formula():
    """🔴 用同一支純規則算。另寫一份加總 ＝ 前端／後端各講一套，
    而「已核准＋已付款算用掉、待審不算」這條規則只該有一個正本。"""
    body = code_only(func_body(repo_src(SRC), "async def my_benefits("))
    assert "benefit_pool_balance([]," in body, "沒有重用 benefit_pool_balance"
    assert "staff_allowance_balance" not in body, "每人額度也不該在端點裡自己算"
    assert "BENEFIT_COMMITTED" not in body, "在端點裡自己重算了一次狀態判定"


def test_my_usage_is_computed_per_pool():
    """兩個池要分開算 —— 加在一起的話「我在進修用了多少」就答不出來。"""
    body = code_only(func_body(repo_src(SRC), "async def my_benefits("))
    assert "mine_by_pool" in body and "setdefault(e.pool_id" in body


# ── 前端：狀況真的看得到 ──────────────────────────────────────────

def test_rejected_entries_show_the_reason():
    """🔴 退回是唯一需要員工做事的狀態。只顯示「退回」兩個字等於沒說。"""
    c = _card()
    assert 'e.status === "退回" && e.notes' in c, "沒有算出退回原因"
    assert "esc(e.notes)" in c, "退回原因沒有跳脫（notes 是人打的字）"
    # 算出來還要**畫出去** —— 少了這行，上面兩條照樣過但畫面上什麼都沒有
    assert "${why}" in c, "退回原因沒有被放進列的樣板裡"


def test_list_is_not_truncated():
    """原本 slice(0, 8) 而且沒有任何提示 —— 第 9 筆之後直接消失。"""
    c = _card()
    assert ".slice(0, 8)" not in c and ".slice(0,8)" not in c, "又截斷了"
    assert "共 ${all.length} 筆" in c, "沒有告訴員工總共幾筆"
    assert "overflow-y" in c, "不截斷又不給捲軸，卡片會被拉到很長"


def test_pool_balance_is_labelled_as_shared():
    """池餘額不是個人額度 —— 標籤要說清楚，不然員工會誤會。"""
    c = _card()
    # 🔴 斷言要框在**樣板**上，不是全檔找字串 —— 「池餘額」三個字在我自己
    #    寫的註解裡也有，只找字串的話註解就把測試餵飽了（實測過）。
    assert "${esc(p.name)} 池餘額</div>" in c, "餘額沒有標示成「池」的"
    assert "我在${esc(p.name)}已用" in c, "沒有顯示自己用了多少"


def test_status_meanings_are_spelled_out():
    """待審／已核准／已付款對員工不是自明的字。"""
    c = _card()
    for w in ("待審＝", "已核准＝", "已付款＝"):
        assert w in c, f"沒有解釋「{w[:3]}」"


# ── 補心得 ────────────────────────────────────────────────────────

def test_reflection_editor_is_a_textarea_not_prompt():
    """🔴 prompt 會把換行吃掉 —— 而員工端正是寫心得的主場。
    管理端那支早就因為同一個理由改成小視窗了。"""
    c = _card()
    assert "prompt(" not in c, "又用回 prompt 了（換行會被吃掉）"
    assert "<textarea" in c and "data-noteedit" in c


def test_reflection_put_sends_the_other_fields_back():
    """🔴 me 那支 PUT 是**整筆覆蓋**。只送 reflection 會把項目與金額
    洗成空的，然後被 422 擋下來（或更糟：洗掉真資料）。"""
    c = _card()
    put = c[c.index("data-note]"):]
    body = put[put.index("method: \"PUT\""):put.index("if (!r.ok)")]
    for k in ("pool_id:", "title:", "amount:", "spend_date:", "reflection:"):
        assert k in body, f"PUT 少送了 {k}"


def test_only_editable_entries_get_the_buttons():
    """已核准／已付款的登記帳上掛著應付款，本人不能再動（後端會 409）——
    畫面上就不該給按鈕，按了才被拒是最差的。"""
    c = _card()
    assert 'const editable = e.status === "待審" || e.status === "退回";' in c
    assert re.search(r"\$\{editable \? `<button[^`]*data-up", c, re.S), \
        "傳單據的按鈕沒有被 editable 閘住"
    assert "data-note" in c[c.index("editable ?"):c.index("<span class=\"pill")], \
        "寫心得的按鈕沒有跟 editable 綁在一起"


def test_cancel_does_not_wipe_an_existing_note():
    """取消 ≠ 清空。"""
    c = _card()
    assert "[data-cancel]\").onclick = () => box.remove();" in c


# ── 沒綁人員檔案的帳號 ────────────────────────────────────────────

def test_unbound_account_is_explained_not_shown_as_an_error():
    """🔴 owner 用 admin 開 /my.html 時看到「載入失敗（409）」。

    409 的意思是「這個帳號沒有綁人員檔案」—— admin 是系統帳號本來就沒有綁人，
    那不是錯誤，是「這張卡對你沒有內容」。後端 detail 早就寫得清清楚楚，
    前端卻丟掉只印狀態碼（跟 503 被印成「需要權限」同一種病）。
    """
    my = repo_src(MY)
    assert "cardBenefits(ws.bound)" in my, "卡片不知道帳號有沒有綁人"
    c = _card()
    assert "bound === false" in c, "沒有短路 —— 還是會去打那支必定 409 的端點"
    assert "還沒有綁定人員檔案" in c


def test_backend_detail_is_surfaced_not_swallowed():
    c = _card()
    assert "(await r.json()).detail" in c, "後端的話被丟掉了"
    assert "載入失敗（${r.status}）" not in c, "又只印狀態碼了"


def test_status_fallback_covers_the_meaningful_codes():
    """沒有 detail 時才落到對照表；503 一樣不能說成權限問題。"""
    my = repo_src(MY)
    f = my[my.index("function failText("):]
    f = f[:f.index(chr(10) + "}")]
    for code in ("=== 0", "=== 401", "=== 403", "=== 503", ">= 500"):
        assert code in f, f"沒有處理 {code}"
    assert "不是權限問題" in f
    i503 = f.index("=== 503")
    assert "需要「" not in f[i503:], "503 又在要求權限"


# ── 授權 ──────────────────────────────────────────────────────────

def test_card_is_gated_by_me_benefits_and_the_key_is_registered():
    """卡片由 me_benefits 閘住 —— 這個 key 沒有在三處註冊的話，
    owner 在使用者管理裡根本勾不到它（於是誰都看不到，生產上就是這樣）。"""
    assert 'ws.allowed.includes("me_benefits")' in repo_src(MY)
    assert "'me_benefits'," in repo_src("core/auth.py")
    assert "me_benefits" in repo_src("frontend/js/shared/tab-config.js")
    assert "me_benefits:'我的福委會'" in repo_src("frontend/js/admin/user-mgmt.js")
    assert '"me_benefits"' in repo_src("core/auth.py").split("ME_MODULE_KEYS")[1][:400], \
        "workspace 沒有把 me_benefits 帶進 allowed（ME_MODULE_KEYS 正本在 core/auth）"
