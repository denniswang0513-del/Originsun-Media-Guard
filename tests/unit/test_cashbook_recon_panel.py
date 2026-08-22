# -*- coding: utf-8 -*-
"""對帳系統搬進收支明細（owner 2026-08-22）。

owner 的原話：「這兩個按鈕我希望整合在一起，對帳系統按了之後就直接在收支表
這裡對帳，不要像現在跳轉至銀行帳戶；銀行帳戶的這個功能直接移到收支表就可以了。」

所以做了兩件事：
  1. banking.js 的對帳整段（上傳對帳單／分類規則／對帳工作台／核對餘額）
     **搬**成 finance/subviews/recon.js —— 一個掛得進任何容器的模組。
  2. 收支明細把「對帳系統」與「匯入 CSV」兩顆合成一個入口，就地展開面板，
     recon.js 掛進 #cash-recon-mount。

這支測試釘的是「搬」而不是「複製」：分類規則、草稿、工作台狀態只該有一份。
兩個畫面各養一套規則，使用者在 A 改完到 B 看不到效果 —— 那是最容易靜默錯帳
的一種重複，而且不會有任何訊號（沒有 build 錯誤、沒有例外，只有帳不對）。

🔴 斷言一律走 js_code_only —— 這幾個檔案的註解裡就有 'financeNav'、
   '對帳' 這些字，全檔找字串會被自己的說明打敗（這個坑本 session 踩過三次）。
"""
from tests.unit._srcscan import js_code_only, repo_src

CASHBOOK_JS = "frontend/tabs/crm/crm-cashbook.js"
CASHBOOK_HTML = "frontend/tabs/crm/crm-cashbook.html"
RECON = "frontend/tabs/finance/subviews/recon.js"
BANKING = "frontend/tabs/finance/subviews/banking.js"

# 對帳系統獨有的東西：這些字串出現在哪個檔，那個檔就有一份對帳實作。
RECON_MARKERS = (
    "/bank-statement/preview",   # 對帳單解析
    "/import-rules",             # 分類規則
    "/statement-lines",          # 對帳工作台逐列勾銷
    "/reconciliations",          # 核對餘額
)


def test_recon_implementation_lives_in_exactly_one_file():
    """對帳只能有一份實作，而且在 recon.js。"""
    recon = js_code_only(repo_src(RECON))
    for m in RECON_MARKERS:
        assert m in recon, f"recon.js 少了 {m} —— 搬移漏了一塊"

    for path in (BANKING, CASHBOOK_JS):
        src = js_code_only(repo_src(path))
        for m in RECON_MARKERS:
            assert m not in src, f"{path} 又長出一份對帳（{m}）"


def test_cashbook_mounts_the_shared_module_instead_of_navigating_away():
    """按鈕就地展開面板，不再跳到別的子視圖。"""
    js = js_code_only(repo_src(CASHBOOK_JS))
    assert "import('../finance/subviews/recon.js')" in js, "沒有掛共用的對帳模組"
    assert "cash-recon-mount" in js
    # 舊行為：點側欄那顆 banking。整個機制連同 finance.js 的導覽入口一起收掉了。
    assert "financeNav" not in js, "還在跳頁"
    assert "financeNav" not in js_code_only(repo_src("frontend/tabs/finance/finance.js")), \
        "沒人用的導覽入口留著只會被下一個人當成介面"


def test_the_panel_is_lazy_loaded():
    """recon.js 一千多行 —— 開 tab 的關鍵路徑上不該有它。

    靜態 import 會讓每個開收支明細的人都付這個成本，而多數人不對帳。
    """
    js = js_code_only(repo_src(CASHBOOK_JS))
    assert "from '../finance/subviews/recon.js'" not in js, "變成靜態 import 了"


def test_the_two_import_entries_became_one():
    """對帳與匯入 CSV 對使用者是同一件事（把外面的帳弄進來）→ 一個入口。"""
    html = repo_src(CASHBOOK_HTML)
    assert html.count('id="cash-btn-recon"') == 1
    assert html.count('id="cash-btn-import"') == 1
    # 匯入 CSV 必須在面板裡，不能留在工具列（不然就不是「整合在一起」）
    panel = html[html.index('id="cash-recon-panel"'):html.index('id="cash-list-panel"')]
    assert 'id="cash-btn-import"' in panel, "匯入 CSV 沒有搬進面板"
    assert 'id="cash-recon-mount"' in panel


def test_the_two_modules_do_not_share_a_namespace():
    """banking.js 是 window._finBank、recon.js 是 window._finRecon。

    拆檔前兩邊共用 _finBank；如果搬過去的程式碼忘了換命名空間，
    `_fb.reload()` 會去重畫**銀行帳戶**那一頁（現在根本沒掛在畫面上）——
    症狀是按鈕按了畫面不動，而且不會有任何例外。
    """
    recon = js_code_only(repo_src(RECON))
    banking = js_code_only(repo_src(BANKING))
    assert "window._finBank" not in recon
    assert "window._finRecon" not in banking


def test_bank_only_rule_is_shared_not_copied():
    """「什麼算真銀行帳戶」（排除股東往來）拆檔後兩邊都要問 —— 正本在 fin-utils。"""
    utils = js_code_only(repo_src("frontend/tabs/finance/fin-utils.js"))
    assert "export const bankOnly" in utils
    for path in (RECON, BANKING):
        src = js_code_only(repo_src(path))
        # 只釘 bankOnly 那一條（啟用中 且 非股東往來）。banking.js 另有兩處單看
        # isShareholderAcct —— 股東往來區塊、以及帳戶卡片列（那裡刻意**連停用的
        # 也列**，好讓人把它啟用回來），那些不是這條規則的複本。
        assert "a.active !== false && !isShareholderAcct" not in src, \
            f"{path} 又自己寫了一次 bankOnly"
