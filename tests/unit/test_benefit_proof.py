# -*- coding: utf-8 -*-
"""福委會的單據與心得筆記（owner 2026-08-21）。

> 「他們會需要上傳單據 以及心得筆記，才有辦法請款 所以會需要有欄位提供他們上傳
>   與撰寫。但這塊並不是一定要上傳才能請款，這樣才符合各種使用情境，
>   有心得跟有單據讓我知道就好。」

兩句話合起來只有一個意思：**給欄位、但不擋**。所以整份測試守的是

  🔴 沒單據、沒心得 → 照樣登記得了、照樣送得出去、照樣核得准
  🔴 有沒有要**標示出來**（has_receipt / has_reflection），owner 才「知道」

以及一個資料面的：心得**不共用 notes** —— notes 裝退回原因（append `[退回] xxx`），
員工寫的心得跟 owner 寫的退回理由混在一欄，兩邊都讀不乾淨。
"""
from tests.unit._srcscan import code_only, func_body, repo_src

SRC = "routers/crm/benefits.py"


def _body(fn):
    return code_only(func_body(repo_src(SRC), fn))


# ── 欄位是分開的 ──────────────────────────────────────────────────

def test_reflection_is_its_own_column():
    from db.models import HrBenefitEntry
    cols = HrBenefitEntry.__table__.c
    assert "reflection" in cols and "notes" in cols, "心得與備註要各自一欄"
    assert "receipt_url" in cols


def test_reject_reason_goes_to_notes_not_reflection():
    """🔴 退回原因寫進 notes —— 沾到 reflection 就把員工的心得覆蓋/污染了。"""
    body = _body("async def reject_entry(")
    assert "e.notes = ((e.notes or \"\")" in body, "退回原因不是寫進 notes"
    assert "reflection" not in body, "退回竟然碰到心得欄"


def test_payload_carries_reflection():
    from core.schemas import BenefitEntryPayload
    p = BenefitEntryPayload(title="奧德賽", amount=380, reflection="很好看")
    assert p.reflection == "很好看"
    assert BenefitEntryPayload(title="x", amount=1).reflection == ""


# ── 🔴 都不是必填 ─────────────────────────────────────────────────

def test_nothing_requires_a_receipt_or_reflection():
    """owner：「並不是一定要上傳才能請款」。任何一支端點都不准因為
    沒單據／沒心得而擋下來。"""
    src = repo_src(SRC)
    for bad in ("必須上傳", "請先上傳", "沒有單據", "缺少心得"):
        assert bad not in src, f"出現了像閘門的字串：{bad}"
    # 建立與核准的函式體裡都不該出現對這兩欄的必填檢查
    for fn in ("async def add_my_entry(", "async def add_entry_for(",
               "async def approve_entry("):
        b = _body(fn)
        assert "receipt_url" not in b or "raise" not in b.split("receipt_url")[0][-200:], \
            f"{fn} 疑似把單據當必填"


def test_validate_entry_only_checks_title_and_amount():
    """欄位檢查的正本只管項目與金額 —— 心得單據不進來。"""
    from core.hr_logic import validate_benefit_entry
    assert validate_benefit_entry("奧德賽", 380) == ""      # 沒心得沒單據也過
    src = code_only(func_body(repo_src("core/hr_logic.py"),
                              "def validate_benefit_entry("))
    assert "reflection" not in src and "receipt" not in src


# ── 有沒有要標示出來 ──────────────────────────────────────────────

def test_entry_dict_exposes_both_flags():
    """owner：「有心得跟有單據讓我知道就好」。"""
    body = _body("def _entry_dict(")
    assert '"has_receipt": bool(e.receipt_url)' in body
    assert '"has_reflection": bool(reflection)' in body
    assert '"reflection": reflection' in body


def test_blank_reflection_is_not_counted_as_having_one():
    """只打了幾個空白不算有心得 —— 標示會騙人。"""
    body = _body("def _entry_dict(")
    assert '(e.reflection or "").strip()' in body, "沒有 strip，空白會被當成有心得"


def test_admin_ui_shows_both_indicators():
    js = repo_src("frontend/tabs/hr_benefits/hr_benefits.js")
    assert "function _proof(" in js
    assert "has_receipt" in js and "has_reflection" in js
    # 待審佇列與明細表**都要**顯示（owner 主要在待審佇列上看）
    assert js.count("${_proof(e)}") == 2, "有一張表沒有顯示單據／心得"
    assert "receipt-file?path=" in js, "單據沒有可以點開的連結"
    css = repo_src("frontend/tabs/hr_benefits/hr_benefits.html")
    assert ".hb-proof" in css, "標示沒有樣式（會變成看不見的純文字）"


def test_missing_proof_is_shown_as_dash_not_an_error():
    """沒有不是錯誤 —— 淡色的「—」，不是紅字警告。"""
    js = repo_src("frontend/tabs/hr_benefits/hr_benefits.js")
    assert "單據 —" in js and "心得 —" in js


def test_employee_ui_can_write_and_upload():
    my = repo_src("frontend/my.html")
    assert "mb-reflection" in my, "員工端沒有心得欄"
    assert "reflection:" in my, "心得沒有送出去"
    assert "/receipt/" in my or "/receipt`" in my, "員工端不能補傳單據"
    assert "選填" in my, "沒有告訴員工這是選填"


# ── 單據上傳的守衛 ────────────────────────────────────────────────

def test_receipt_upload_checks_ownership_or_approver():
    """本人可以傳自己的；不是自己的要有審核權。少了這道，任何登入者
    都能覆蓋別人的憑證。"""
    body = _body("async def upload_benefit_receipt(")
    assert "e.staff_id == ident[\"staff\"].id" in body, "沒有判斷是不是自己的"
    assert "_check_approver(request)" in body, "別人的沒有要求審核權"


def test_receipt_upload_locked_after_approval():
    """已核准／已付款之後不再開放換單據 —— 那時帳上掛著應付款，換單據＝換憑證。"""
    body = _body("async def upload_benefit_receipt(")
    assert "BENEFIT_EDITABLE" in body and "409" in body


def test_receipt_upload_has_the_two_upload_guards():
    """副檔名黑名單 + 串流上限 —— 與零用金收據同一套（那條路徑的教訓）。"""
    body = _body("async def upload_benefit_receipt(")
    assert "BLOCKED_UPLOAD_EXTS" in body, "沒擋副檔名"
    assert "stream_to_disk" in body and "_MAX_RECEIPT_BYTES" in body, "沒有串流上限"


def test_receipt_shares_the_existing_root_and_serving_endpoint():
    """存到既有的收據 root（settings.receipts_root），取檔走既有的
    /receipt-file —— 不另開一條儲存與取檔的路。"""
    body = _body("async def upload_benefit_receipt(")
    assert "_receipts_root()" in body
    js = repo_src("frontend/tabs/hr_benefits/hr_benefits.js")
    assert "/api/v1/crm/receipt-file?path=" in js
