# -*- coding: utf-8 -*-
"""統一編號的前導 0（2026-08-21 owner 回報：兩廳院應該是 00973926，畫面只有 973926）。

**不是我們吃掉的** —— 後端從頭到尾只做 `.strip()`（欄位 String(16)、schema 是 str）。
是 Excel／Google Sheets 把 `00973926` 存成**數字** 973926，匯出 CSV 就少了兩個 0，
系統忠實地把缺 0 的值匯了進來。生產實測 23 筆缺 0，全是 0 開頭的政府／學術機構
（國家兩廳院 00973926、中央研究院 03811209、公視 01012145…）——
統編錯了就是發票開錯，這是會計與法遵層級的錯。

所以修在**寫入的咽喉**，讓下一次匯入自己痊癒：
  · ClientPayload / InvoicePayload 的 field_validator（一般 CRUD 都走這）
  · 兩支 CSV 匯入是 `Model(**data)` 直接建、**繞過 payload**，各自再過一次

統編定義上就是 8 碼，所以補 0 是規則不是猜測。
"""
import re

import pytest

from core.crm_logic import normalize_tax_id
from core.schemas import ClientPayload, InvoicePayload
from tests.unit._srcscan import repo_src


# ── 規則本身 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("973926", "00973926"),        # 🔴 owner 回報的那一筆（國家兩廳院）
    ("3811209", "03811209"),       # 中央研究院
    ("1012145", "01012145"),       # 公視
    ("501503", "00501503"),
    ("509403", "00509403"),
])
def test_short_numeric_gets_zero_padded(raw, want):
    assert normalize_tax_id(raw) == want


@pytest.mark.parametrize("raw", ["12345678", "00973926", "87654321"])
def test_already_eight_is_untouched(raw):
    assert normalize_tax_id(raw) == raw


@pytest.mark.parametrize("raw", [
    "A123456789",   # 個人身分證（10 碼）
    "F12345678",    # 9 碼
    # 🔴 這兩個**短**的才真的驗到 isdigit() 那道關 —— 上面兩個長度就 ≥8，
    # 早被「≥8 碼不動」擋掉了，拿掉 isdigit() 也照樣過（實測破壞測試沒咬到）。
    "A12345",
    "1234-56",
])
def test_letters_are_never_padded(raw):
    """別名有「統編/身分證」—— 補 0 會把它變成 000A12345 這種毀掉的值。"""
    assert normalize_tax_id(raw) == raw


def test_longer_than_eight_is_not_truncated():
    """不猜也不截斷 —— 太長八成是別的東西，動它只會把錯的變成看起來對的。"""
    assert normalize_tax_id("123456789012") == "123456789012"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_blank_stays_blank(raw):
    assert normalize_tax_id(raw) == ""


def test_whitespace_and_numeric_input_are_handled():
    """CSV 會給前後空白；Excel 直接給 int 也要吃得下。"""
    assert normalize_tax_id("  501503 ") == "00501503"
    assert normalize_tax_id(973926) == "00973926"


# ── 四個寫入口都要走到 ────────────────────────────────────────────

def test_client_payload_normalizes():
    assert ClientPayload(short_name="兩廳院", tax_id="973926").tax_id == "00973926"


def test_invoice_payload_normalizes():
    assert InvoicePayload(title="x", tax_id="3811209").tax_id == "03811209"


@pytest.mark.parametrize("cls,kw", [(ClientPayload, {"short_name": "x"}),
                                    (InvoicePayload, {"title": "x"})])
def test_payload_letters_survive(cls, kw):
    assert cls(tax_id="A123456789", **kw).tax_id == "A123456789"


def test_client_csv_import_normalizes():
    """🔴 CSV 那條是 `Client(**data)` 直接建、繞過 payload 的驗證器 ——
    漏了它，最容易吃掉 0 的那條路（試算表匯出）反而沒有保護。"""
    src = repo_src("routers/crm/clients.py")
    i = src.index("def import_clients_csv") if "def import_clients_csv" in src else 0
    assert "normalize_tax_id(data.get(\"tax_id\"))" in src, "客戶 CSV 沒過正規化"
    assert 'data["tax_id"] = tax_id' in src, "算了卻沒寫回 data"


def test_invoice_csv_import_normalizes():
    src = repo_src("routers/crm/finance.py")
    body = src[src.index("def _map_invoice_row"):]
    assert 'data["tax_id"] = normalize_tax_id(data["tax_id"])' in body[:1200], \
        "發票 CSV 沒過正規化"


def test_rule_lives_in_one_place():
    """規則只有一份正本 —— 誰再自己寫 zfill(8) 就會有第二個答案。"""
    hits = []
    for rel in ("routers/crm/clients.py", "routers/crm/finance.py",
                "core/schemas.py"):
        src = repo_src(rel)
        hits += [(rel, m.group(0))
                 for m in re.finditer(r"zfill\(\s*8\s*\)|rjust\(\s*8", src)]
    assert not hits, f"有人自己補 0 而不是用 normalize_tax_id：{hits}"
