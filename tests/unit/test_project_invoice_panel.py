# -*- coding: utf-8 -*-
"""專案詳情「發票」分頁：申請人／品項可就地補、刪除兩邊同步。

owner 2026-08-24：「這裡要可以填申請人跟品項，發票開立那裡如果有修改，這裡的
連結要維持同步（如果發票開立那裡刪除，這裡也要刪除。這裡刪除發票，發票開立那邊
也要可以刪除發票）」。

🔴 這一頁碰同一張發票，而 PUT /invoices/{id} 是**整包寫回**（model_dump 逐欄
   setattr，沒送的欄位會被 schema 預設值覆蓋）。同一族陷阱在這個 repo 咬過兩次，
   其中一次把 183 張、合計 10,656,093 的代開發票方向翻成收款。所以這裡釘的不是
   「有沒有那個欄位」，而是**寫入路徑的形狀**：讀整張 → 換一欄 → 整包送回。
"""
from db.models import CrmInvoice
from routers.crm.finance import _to_invoice_dict
from core.schemas import InvoicePayload
from tests.unit._srcscan import func_body, js_code_only, repo_src
from tests.unit._srcscan import finance_src

PANEL = "frontend/tabs/crm/crm-projects-invoices.js"


# ── 讀要蓋得住寫 ──────────────────────────────────────────────

def test_the_read_covers_every_writable_field():
    """「讀整張 → 換一欄 → 整包送回」只有在**讀蓋得住寫**時才是無損的。

    少一個欄位就是一個會被 schema 預設值悄悄洗掉的欄位 —— 而且是在使用者只想
    補一個品項的時候。新增 InvoicePayload 欄位卻忘了進 _to_invoice_dict，
    這條會紅。
    """
    got = set(_to_invoice_dict(CrmInvoice(), ""))
    missing = set(InvoicePayload.model_fields) - got
    assert not missing, f"GET 回不到這些可寫欄位，整包送回會被洗掉：{sorted(missing)}"


def test_file_url_is_deliberately_not_writable():
    """反向的那半：file_url 刻意不在 payload 裡（只有上傳/刪除端點動得了）。
    哪天有人「順手補齊」把它加進 InvoicePayload，整包寫回就會在前端沒送時
    把已開立電子發票的檔案路徑清成空字串。"""
    assert "file_url" not in InvoicePayload.model_fields
    assert "file_url" in _to_invoice_dict(CrmInvoice(), "")


# ── 前端的寫入路徑形狀 ────────────────────────────────────────

def test_the_panel_reads_the_whole_invoice_before_writing_it_back():
    body = js_code_only(func_body(repo_src(PANEL), "_P.setMeta = async ("))
    assert "await _fetch('/invoices/' + id)" in body, \
        "沒有先讀整張票 —— 整包寫回會把品名/金額/抬頭/專案連結洗成預設值"
    assert "...full" in body, "沒把讀到的整張攤平送回"
    assert "'PUT'" in body


def test_the_panel_does_not_take_the_list_row_as_the_base():
    """底稿只能是 GET /invoices/{id}。拿清單那一列來省一趟 —— 現在剛好夠，
    但清單序列化與寫入 payload 是兩份定義，分岔那天會靜默清欄位。"""
    body = js_code_only(func_body(repo_src(PANEL), "_P.setMeta = async ("))
    assert "...inv," not in body and "...inv }" not in body, \
        "拿了 _invoices 裡的清單列當底稿"


def test_a_failed_save_puts_the_old_value_back():
    """失敗不還原的話，畫面會顯示一個資料庫裡不存在的值 —— 而下一次存檔
    就會把那個假值寫成真的。"""
    body = js_code_only(func_body(repo_src(PANEL), "_P.setMeta = async ("))
    assert "el.value = inv[field]" in body, "存檔失敗沒有把輸入框退回原值"


def test_an_unchanged_value_does_not_hit_the_backend():
    body = js_code_only(func_body(repo_src(PANEL), "_P.setMeta = async ("))
    assert "if ((inv[field] || '') === value) return;" in body, \
        "沒改也送 —— 每次 blur 都會整包寫回一次，白白多一次全欄覆蓋的風險"


# ── 刪除：兩邊同一支端點 ──────────────────────────────────────

def test_the_panel_deletes_through_the_same_endpoint():
    """🔴 在這裡另寫一套「簡單版刪除」會留下孤兒的收款分配列，而後果是靜默的：
    那筆收款仍被判成「分配與實收相符」，錢卻沒對到任何存在的發票。
    共用 DELETE /invoices/{id} 正是兩邊不會分岔的原因。"""
    body = js_code_only(func_body(repo_src(PANEL), "_P.del = async ("))
    assert "'/invoices/' + id" in body and "'DELETE'" in body


def test_deleting_an_invoice_with_money_on_it_says_so():
    """PM 在專案頁看到的只有「已收 $X」一個數字，不會自己想到刪掉會解除配對。"""
    body = func_body(repo_src(PANEL), "_P.del = async (")
    assert "inv.collected" in body, "確認框沒看已收金額"
    assert "解除" in body, "確認框沒說會解除收款配對"


def test_the_delete_endpoint_still_cleans_the_allocations():
    """後端那半：上面那條「共用端點就安全」的前提是端點真的有清乾淨。"""
    import re
    body = func_body(finance_src(),
                     "async def delete_invoice(")
    # 釘的是「真的刪掉那些連結列」，不是「這個字有出現」—— 端點本來就會先
    # select 一次拿受影響的收支，光看字串的話刪除那行被拿掉也照樣綠。
    assert re.search(r"(delete|_sadel)\(\s*CrmCashInvoiceLink\s*\)", body), \
        "刪發票沒真的刪掉收款分配列（留下孤兒，收款會被判成分配相符）"
    assert "_set_primary_invoice" in body, "沒改指/清空收支列的主要發票"
    assert "_assert_month_open" in body, "沒檢查鎖帳月"


# ── 申請人清單只有一份 ────────────────────────────────────────

def test_the_panel_reuses_the_server_applicant_list():
    """兩個入口各存一份清單的話，這頁開的票會掛到發票本下拉裡選不到的名字，
    篩選與排序就再也對不起來。"""
    src = js_code_only(repo_src(PANEL))
    assert "crmCacheFetch('invoice_applicants', '/invoice-applicants')" in src, \
        "沒讀伺服器的申請人清單（或沒走共用快取）"
    assert src.count("invoice-applicants") == 1, \
        "碰了那支端點不只一次 —— 這裡只該讀，管理留在發票本的 ⚙️"


def test_an_applicant_missing_from_the_list_is_still_selected():
    """🔴 票上的名字被從清單移掉時，下拉不能顯示「—」—— 畫面會說「這張沒有
    申請人」而它其實有，接著只要有人動同一列的品項，整包寫回就真的把名字清掉。"""
    body = js_code_only(func_body(repo_src(PANEL), "function _applicantCell("))
    assert "_applicants.includes(cur)" in body, \
        "沒處理「票上的申請人不在目前清單裡」"
    assert "[cur, ..._applicants]" in body, "沒把票上那個名字補進選項"
