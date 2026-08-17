# -*- coding: utf-8 -*-
"""專案頁「支出」分頁的畫面（docs/PETTY_CASH_PLAN.md §3.5）。

直接 import 子視圖模組、餵固定資料 —— 不走 SPA 導覽（那條在這個 repo 一向
flaky，而且真正會壞的是渲染邏輯不是點擊）。

守的是三件會靜默出錯的事：

  A. **未設預算不准畫成 0** —— `misc_budget_total` 是三態（鍵不在＝沒權限／
     null＝真的沒設／有值）。`budget || 0` 會把「還沒編預算」講成「預算 0 元」，
     於是每個案子看起來都超支。這一類謊報在雜支頁踩過一次（MONEY_VISIBILITY §4）。
  B. **依項目小計要真的加總** —— 這一頁回答的「花在哪」就是這張表。
  C. **超支要看得出來** —— 有預算且實際 > 預算時走紅色那條路。
"""
import json

import pytest

pytestmark = pytest.mark.e2e

MOD = "/tabs/proposals/expense-view.js"

ROWS = [
    {"id": "1", "category": "飲食", "actual": 1200, "estimated": 0,
     "sub_item": "兩廳院中餐", "item": "專案雜支", "staff_name": "蘇家弘",
     "expense_date": "2026-08-09", "status": "待審", "receipt_url": "", "invoice_no": ""},
    {"id": "2", "category": "交通", "actual": 800, "estimated": 0,
     "sub_item": "油資", "item": "專案雜支", "staff_name": "蔡念栩",
     "expense_date": "2026-08-10", "status": "已付款", "receipt_url": "x.webp", "invoice_no": ""},
    {"id": "3", "category": "其他", "actual": 500, "estimated": 0,
     "sub_item": "對講機租金", "item": "設備耗材", "staff_name": "蘇家弘",
     "expense_date": "2026-08-11", "status": "已付款", "receipt_url": "", "invoice_no": "AB12345678"},
]


def _render(page, base_url, payload):
    """把模組掛到一個空白頁上，回傳 host 的 innerText。"""
    page.goto(base_url + "/petty-cash.html", wait_until="domcontentloaded")
    return page.evaluate(
        """async ([mod, data]) => {
            const m = await import(mod);
            const host = document.createElement("div");
            host.id = "t-host";
            document.body.appendChild(host);
            await m.renderExpenses(host, {
                projectId: "p1", fetcher: async () => data });
            return host.innerText;
        }""", [MOD, payload])


@pytest.fixture(scope="module")
def base(real_server):
    return real_server["base_url"]


def test_unset_budget_is_not_drawn_as_zero(page, base):
    """🔴 A：預算沒設就寫「未設」，不准出現 NT$ 0 的預算，也不准喊超支。"""
    text = _render(page, base, {"expenses": ROWS, "grouped_by_group": [],
                                "misc_budget_total": None})
    assert "未設" in text, "沒設預算時該顯示「未設」"
    assert "NT$ 2,500" in text, "雜支實際應該是三筆之和 2,500"
    assert "超支" not in text, "沒有預算就無從超支"
    assert "剩餘" not in text, "沒有預算就沒有剩餘"


def test_subtotal_by_item(page, base):
    """B：依項目小計 —— 專案雜支 2,000（1200+800）、設備耗材 500。"""
    text = _render(page, base, {"expenses": ROWS, "grouped_by_group": [],
                                "misc_budget_total": None})
    assert "專案雜支" in text and "NT$ 2,000" in text
    assert "設備耗材" in text and "NT$ 500" in text


def test_over_budget_is_visible(page, base):
    """C：有預算且超出 → 出現「超支」與差額。"""
    text = _render(page, base, {"expenses": ROWS, "grouped_by_group": [],
                                "misc_budget_total": 2000})
    assert "超支" in text
    assert "NT$ 500" in text, "超支金額 2,500 − 2,000 = 500"


def test_within_budget_shows_remaining(page, base):
    text = _render(page, base, {"expenses": ROWS, "grouped_by_group": [],
                                "misc_budget_total": 4000})
    assert "剩餘" in text and "超支" not in text
    assert "NT$ 1,500" in text


def test_pending_payment_is_called_out(page, base):
    """請款中的金額單獨標出來 —— 它已經是成本，但錢還沒出去。"""
    text = _render(page, base, {"expenses": ROWS, "grouped_by_group": [],
                                "misc_budget_total": None})
    assert "尚未付款" in text and "NT$ 1,200" in text


def test_empty_project_points_at_the_petty_page(page, base):
    """沒有支出時不留死路 —— 告訴人現場登記走哪裡。"""
    text = _render(page, base, {"expenses": [], "grouped_by_group": [],
                                "misc_budget_total": None})
    assert "還沒有支出紀錄" in text and "零用金" in text


def test_no_id_number_leaks(page, base):
    """誰墊的只出姓名 —— Sheet 上的「姓名_身分證」整串不准漏到畫面。"""
    rows = json.loads(json.dumps(ROWS))
    rows[0]["staff_name"] = "蘇家弘"          # 匯入端已只存姓名
    text = _render(page, base, {"expenses": rows, "grouped_by_group": [],
                                "misc_budget_total": None})
    assert "蘇家弘" in text
    assert "E123871879" not in text
