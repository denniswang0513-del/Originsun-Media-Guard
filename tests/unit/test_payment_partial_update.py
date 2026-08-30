# -*- coding: utf-8 -*-
"""請款單編輯：空的「預計付款月」不能擋住存檔，沒送的欄位不能被洗掉。

owner 2026-08-24：「我之前記帳錯誤我要調整 但是不給我調整了」——
畫面只吐一句 `儲存失敗: Input should be a valid string`，看不出是哪一欄。

兩件事，都在同一次存檔上：

① **存不了**：前端的共用收值（crm-utils.enableInlineEdit）對 date/month 一律
   `val = val || null`。那對 request_date／payment_date 是對的（Optional），
   但「預計付款月」留空時送來的也是 null，而 planned_month 宣告成 str → 422。
   任何一張沒填預計付款月的請款單都改不了，而錯誤訊息不說是哪一欄。

② **就算存得了，會弄壞更多東西**：PUT /payments/{id} 本來是整包 model_dump 寫回，
   而編輯面板只送 13 個欄位 —— needs_invoice→0、invoice_amount→None、
   is_advance→0、advance_by→""、advance_returned→0、project_label→"" 全部無聲歸零。
   2026-08-24 量過生產：824 張裡，需代開與代開金額各 186 張、專案標籤 141 張會被
   一次存檔清掉，而畫面上只有使用者改的那一欄看起來變了。
   🔴 那個 422 其實**擋下了一次資料破壞** —— 修①的同時一定要修②，
      不然等於把一顆會毀資料的存檔鈕交回去。
"""
from core.schemas import PaymentRequestPayload
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src
from tests.unit._srcscan import finance_src

BASE = dict(summary="台科大10周年 後期製作", amount=14490, category="發票代開")


# ── ① 空的預計付款月 ──────────────────────────────────────────

def test_an_empty_planned_month_arrives_as_null_and_must_be_accepted():
    """這就是 owner 撞到的那一發：month 欄位留空 → 前端送 null。"""
    assert PaymentRequestPayload(**BASE, planned_month=None).planned_month == ""


def test_a_real_planned_month_still_goes_through():
    assert PaymentRequestPayload(**BASE, planned_month="2026-09").planned_month == "2026-09"


def test_the_stored_shape_is_never_none():
    """全 repo 都用 `planned_month or ""` 比對 —— 收斂在入口，不要改存的形狀。
    三條路都要是空字串：送 null、完全不送、送空字串。只驗第一條的話，
    把欄位改成 Optional[str] = None（不送就存 None）會整個溜過去。"""
    assert PaymentRequestPayload(**BASE, planned_month=None).planned_month == ""
    assert PaymentRequestPayload(**BASE).planned_month == ""
    assert PaymentRequestPayload(**BASE, planned_month="").planned_month == ""
    assert PaymentRequestPayload.model_fields["planned_month"].default == ""


def test_the_frontend_really_sends_null_for_an_empty_month():
    """釘住前提：哪天共用收值改成送空字串，上面那條就不是在保護真實情況了。"""
    body = js_code_only(repo_src("frontend/tabs/crm/crm-utils.js"))
    assert "el.type === 'date' || el.type === 'month'" in body and "val || null" in body, \
        "共用收值不再把空的 date/month 送成 null —— 這組測試的前提變了，重新確認"
    fields = js_code_only(repo_src("frontend/tabs/crm/crm-payments.js"))
    assert "{name:'planned_month', label:'預計付款月', type:'month'}" in fields, \
        "預計付款月不再是 month 型別 —— 前提變了"


# ── ② 沒送的欄位不能被洗掉 ────────────────────────────────────

_WIPED = ("needs_invoice", "invoice_amount", "project_label",
          "advance_by", "is_advance", "advance_returned")


def test_a_partial_payload_only_carries_what_was_sent():
    """exclude_unset 的保證：沒送的欄位不會出現在要寫回的 dict 裡。"""
    sent = PaymentRequestPayload(**BASE, planned_month=None).model_dump(exclude_unset=True)
    assert set(sent) == {"summary", "amount", "category", "planned_month"}
    for f in _WIPED:
        assert f not in sent, f"{f} 沒送卻混進了寫回的欄位"


def test_the_endpoint_writes_only_what_was_sent():
    body = code_only(func_body(finance_src(),
                               "async def update_payment("))
    assert "exclude_unset=True" in body, \
        "整包寫回 —— 編輯面板沒送的六個欄位會被洗成預設值"
    assert "req.model_dump(exclude=date_fields" not in body, \
        "還留著整包 model_dump 的舊路徑"


def test_the_dangerous_defaults_are_the_reason_this_matters():
    """這些欄位的預設值就是「洗掉」的形狀 —— 不是 None 就是 0／空字串。
    哪天有人把預設改成 None-而-不-寫，這條會提醒他先想清楚。"""
    fields = PaymentRequestPayload.model_fields
    assert fields["needs_invoice"].default == 0
    assert fields["invoice_amount"].default is None
    assert fields["is_advance"].default == 0
    assert fields["project_label"].default == ""


def test_the_frontend_edit_form_really_omits_them():
    """②之所以會咬人，是因為表單真的沒送這些欄位。前提也要釘。"""
    src = js_code_only(repo_src("frontend/tabs/crm/crm-payments.js"))
    start = src.index("function _buildEditFields()")
    form = src[start:src.index("\n}", start)]
    for f in _WIPED:
        assert f"name:'{f}'" not in form, \
            f"{f} 現在有在表單裡了 —— 前提變了，重新確認這條還要不要"
