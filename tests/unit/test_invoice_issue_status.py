# -*- coding: utf-8 -*-
"""發票的開立狀態：**有號碼才叫已開立**。

owner 2026-08-24：「這裡沒有發票號碼 要標註未開立，等到有發票號碼 才能標註已開立」。

🔴 這條規則本來只存在於發票本的前端，而且**抄了三份**（打字時即時改、存檔時再推
   一次、開視窗時填預設）。專案頁那個入口沒有那三份，於是從專案頁開一張還沒拿到
   號碼的票，會吃到 InvoicePayload 的預設值「已開立」——帳上就出現一張沒有號碼
   卻宣稱已開立的發票（實測：全帳 400 張裡就那一張，2026-08-24 已修）。
   規則搬到後端入口定案，跟 payment_status 同一條路。
"""
from core.finance_logic import (INVOICE_ISSUED, INVOICE_NOT_ISSUED, INVOICE_VOID,
                                issue_status_for)
from core.schemas import InvoicePayload
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src

SRC = "routers/crm/finance.py"


# ── 規則本身 ──────────────────────────────────────────────────

def test_no_number_means_not_issued():
    assert issue_status_for("", "") == INVOICE_NOT_ISSUED
    assert issue_status_for(None, "") == INVOICE_NOT_ISSUED
    assert issue_status_for("   ", "") == INVOICE_NOT_ISSUED


def test_a_number_means_issued():
    assert issue_status_for("XG60957061", "") == INVOICE_ISSUED


def test_the_claimed_status_does_not_beat_the_number():
    """呼叫端說「已開立」但沒號碼 —— 以號碼為準。這就是那一張的成因。"""
    assert issue_status_for("", INVOICE_ISSUED) == INVOICE_NOT_ISSUED
    assert issue_status_for("XG60957061", INVOICE_NOT_ISSUED) == INVOICE_ISSUED


def test_void_is_a_human_decision_and_survives():
    """🔴 作廢的發票通常**有**號碼。拿號碼重推會把它變回已開立 ——
    等於把作廢這件事無聲取消。"""
    assert issue_status_for("XG60957061", INVOICE_VOID) == INVOICE_VOID
    assert issue_status_for("", INVOICE_VOID) == INVOICE_VOID


def test_the_old_wording_never_survives():
    """「開立中」是舊詞（生產 0 張，所以改名沒有資料遷移），但 CSV 與舊客戶端
    可能還送。它不需要別名表 —— 只要不是作廢就走號碼那條，結果自然落在新詞上。
    釘的是**吐出來的絕不會是舊詞**（原樣存回去就會變成第三種狀態）。"""
    for num in ("", "XG60957061"):
        assert issue_status_for(num, "開立中") in (INVOICE_NOT_ISSUED, INVOICE_ISSUED)
    assert issue_status_for("", "開立中") == INVOICE_NOT_ISSUED
    assert issue_status_for("XG60957061", "開立中") == INVOICE_ISSUED
    assert issue_status_for("", "隨便一個沒看過的值") == INVOICE_NOT_ISSUED


# ── 真的接在入口上 ────────────────────────────────────────────

def test_both_invoice_entry_points_derive_it():
    """規則只有一份沒有用 —— 建立與更新兩個入口都要走它。
    少了 create 那半，就是 owner 這次撞到的形狀。"""
    src = repo_src(SRC)
    for fn in ("async def create_invoice(", "async def update_invoice("):
        body = code_only(func_body(src, fn))
        assert "issue_status_for(" in body, f"{fn} 沒有在入口定案開立狀態"


def test_the_payload_default_is_why_this_bites():
    """InvoicePayload.issue_status 有預設值「已開立」，而專案頁那個入口不送這欄
    —— 沒有入口定案的話，沒號碼的票就會是已開立。"""
    assert InvoicePayload.model_fields["issue_status"].default == INVOICE_ISSUED


def test_the_project_panel_still_does_not_send_it():
    """前提：專案頁刻意不送開立狀態（跟 payment_status 一樣交給後端定案）。
    哪天它開始自己送了，這條會提醒重新確認上面那些保證。"""
    body = js_code_only(func_body(repo_src("frontend/tabs/crm/crm-projects-invoices.js"),
                                  "_P.save = async ("))
    assert "issue_status" not in body, "專案頁又自己決定開立狀態了"


# ── 用詞只有一套 ──────────────────────────────────────────────

def test_the_frontend_uses_the_new_wording_everywhere():
    """🔴 一個狀態兩個名字 ＝ 篩選、排序、badge 配色三邊各自對不上。
    唯一允許留著「開立中」的地方是**按鈕文字**（開立中…＝正在開立）。"""
    for path in ("frontend/tabs/crm/crm-invoices.js",
                 "frontend/tabs/crm/crm-invoices.html",
                 "frontend/tabs/crm/crm-projects-invoices.js"):
        src = repo_src(path)
        for line in src.splitlines():
            if "開立中" not in line:
                continue
            assert "textContent" in line or "舊詞" in line, \
                f"{path} 還留著舊詞當狀態值：{line.strip()[:70]}"


def test_both_pages_share_one_issue_badge():
    """發票本與專案頁各畫一份 badge 就會配色/用詞分岔。"""
    utils = js_code_only(repo_src("frontend/tabs/crm/crm-utils.js"))
    assert "export function invoiceIssueBadge(" in utils
    for path in ("frontend/tabs/crm/crm-invoices.js",
                 "frontend/tabs/crm/crm-projects-invoices.js"):
        assert "invoiceIssueBadge" in repo_src(path), f"{path} 沒用共用 badge"


def test_the_project_panel_shows_the_issue_status():
    """owner 指的就是這一格 —— 規則對了但畫面上看不到，等於沒做。"""
    src = js_code_only(repo_src("frontend/tabs/crm/crm-projects-invoices.js"))
    assert "invoiceIssueBadge(i.issue_status)" in src, "清單沒顯示開立狀態"
    assert "th('開立')" in src, "表頭沒有開立那一欄"
