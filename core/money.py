# -*- coding: utf-8 -*-
"""金額檢視授權（`money_view`）—— 政策正本。

owner 2026-08-15：**預設看不到金額，除非我授權**。

在此之前，「錢」只擋到「你得先登入」：2026-08-14 補的 `_crm_read_guard` 擋掉了
匿名，但公司內任何一個有帳號的人 `GET /api/v1/crm/projects` 就拿得到全部
`contract_amount` / `amount_receivable` / `profit_target_pct`（生產 236 筆）。

兩層機制、一份名單（設計與理由：docs/MONEY_VISIBILITY.md）：

  1. **整支擋**：端點本身就是錢（成本明細、財務摘要、發票、請款、收支、報價、
     費率史…）→ `check_money(request)` 403。
  2. **欄位抹除**：端點只是夾帶金額（專案清單／詳情、派工、客戶統計）→
     `MoneyRedactRoute` 在 router 出口把 `MONEY_FIELDS` 的鍵**刪掉**。

🔴 **刪鍵不是歸零**：前端很多地方寫 `x.contract_amount || 0`，抹成 0 會變成
「這案子合約金額是 0 元」——那是謊報。鍵不存在，前端才畫得出「—」。

🔴 **只抹 `cost` 等於沒抹**：`cost = days × rate`，而 `days`（檔期）是要留給
企劃看的。所以 `daily_rate` / `hourly_rate` 也必須在名單裡 —— 否則拿人名去
`GET /crm/staff` 查日費、乘上天數就還原了。同理 `/staff/{id}/rate-history`
整支走第一層。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.responses import Response

# `_extract_token`：驗過的 payload 或 None（不丟例外）。名字帶底線但它是這個
# repo 既有的公用寫法 —— core/identity.py、api_api_keys、api_auth、api_ota 都
# 直接 import 它。另外包一個公開別名只會讓「該用哪一個」多一次判斷。
from core.auth import _extract_token, payload_grants

MODULE_KEY = "money_view"

# 這些鍵一出現就抹掉（**列舉，不是靠字串比對猜**）。
#
# 為什麼不用「名字裡有 amount/cost 就抹」這種規則：`days`（檔期）、`quantity`、
# `tax_rate`（法定 5%，沒有資訊量）長得像錢但不是錢，誤殺會讓沒授權的人連
# 「誰在哪幾天」都看不到 —— 而那正是這次要留給他們的東西。
#
# 🔴 泛名一律不收（2026-08-15 實查，收了會靜默打壞現有畫面）：
#   - `total` 在 CRM 是**列數**（clients/projects/quotes/finance 的 `len(rows)`），
#     `flow.py` 還拿它當五軌完成條的分母 —— 抹掉＝進度條變 0/0
#   - `expense` 在雜支端點是 `{"id": ...}` 這個**物件**的鍵（公開登記頁在用）
#   - `amount` / `subtotal` / `balance` / `deposit` 幾乎只出現在**整支 403**的
#     帳務與報價端點上，收進來的收益遠小於誤殺風險
#
# ⚠️ 上一條**不是**「所以那些欄位一定看不到」。實查的反例：
# `GET /crm/proposals/{pid}/quotes`（提案工作區的報價單分頁）掛的是提案守衛
# （拍攝企劃／提案庫／專案管理任一），回的 `total` / `final_price` 因此對沒有
# `money_view` 的人仍然看得到。那是 owner 待決的邊界（「工作面的錢」vs
# 「公司的錢」，見 docs/MONEY_VISIBILITY.md），**不是漏掉** —— 但別把上面那句
# 當成第二層無條件成立的證明。
#
# 新增欄位時的判準：**它自己或它與另一個可見欄位的乘積是不是金額**
# （`days` 留著，所以 `rate` 一定要抹）。名單有沒有跟上 model，由
# `tests/unit/test_money_visibility.py::test_registry_covers_money_columns` 釘住。
MONEY_FIELDS = frozenset({
    # 專案（crm_projects）
    "contract_amount", "amount_receivable", "amount_received",
    "profit_target_pct", "profit_target", "misc_budget_pct",
    "budget_amount", "misc_budget_amount", "ex_tax", "total_contract",
    "transfer_fee",
    # 人／派工（crm_staff / crm_project_staff）—— rate 系列是 cost 的還原路徑
    "daily_rate", "hourly_rate", "day_rate",
    "rate", "rate_override", "default_rate",
    "cost", "actual_cost", "total_cost",
    # 成本明細（crm_project_cost_lines）
    "estimated_unit_price", "estimated_amount",
    "actual_unit_price", "actual_amount", "unit_price",
    "paid_amount", "unpaid_amount",
    # 報價（crm_quotations）＋ 發票
    "internal_cost", "amount_ex_tax", "amount_total", "tax_amount",
    "invoice_amount",
    # 財務管理／對帳／貸款（api_finance；那支 router 整個 403，列在這裡是為了
    # 「錢的欄位名有一份完整清單」，將來哪支端點搬進 CRM router 也自動受保護）
    "opening_balance", "statement_balance", "system_balance",
    "bank_fee", "annual_rate",
    # 器材（api_equipment，同上理由）
    "purchase_cost",
})

# 名字像錢、但**刻意不抹**的欄位 —— 每一個都要有理由寫在這裡。
# `tests/unit/test_money_visibility.py::test_registry_covers_money_columns`
# 掃 `db/models.py` 的 Column 名，凡命中金額字樣就必須落在上面的名單或這裡，
# 二選一。加欄位的人一定要表態，這就是那道網子的意義。
REGISTRY_EXEMPT = {
    # ── 真的不是錢 ──
    "tax_rate": "法定 5%，沒有資訊量",
    "payment_status": "狀態字串（未到帳／部分／全額），不是金額",
    "budget_hours": "時數池不是錢（N2 工時）",
    "cost_group_id": "外鍵 ID",
    "curated": "正則誤中（cu-rate-d）",
    # ── 是錢，但抹了會壞事 ──
    "amount": "泛名：`{expense: {id}}` 之類的物件也叫它；發它的端點整支 403",
    # ── 是錢，但屬於「工作面的錢」，owner 待決的邊界 ──
    #    （docs/MONEY_VISIBILITY.md：me_finance／公開雜支頁／提案報價單）
    "final_price": "提案工作區的報價單分頁 —— 企劃正在做的那份報價",
    "budget_range": "提案的客戶預算區間，是企劃的工作輸入",
    # ── 自由文字，第二層（抹鍵）本來就到不了 ──
    "fee_note": "備註文字裡的金額不是鍵，是句子",
}


def can_see_money(request: Request) -> bool:
    """這個請求看不看得到金額。管理員（Lv3）一律 True。

    刻意收在這裡而不是各處自己 `payload_grants(..., 'money_view')`：
    「誰算看得到錢」將來若要放寬（例如專案負責人看自己的案子），只有這一支要改。
    """
    return payload_grants(_extract_token(request), MODULE_KEY)


def check_money(request: Request):
    """第一層守衛：整支端點就是錢 → 沒授權直接 403。

    這是**手動呼叫**的形式（`api_finance` / `api_cashflow` 的 `_guard` 在
    endpoint body 裡直接呼叫它）。當 FastAPI dependency 用的是
    `routers/crm/_shared._check_money` —— 那支是 async 包裝，理由見該處。
    ⚠️ 別把這支改成 `async def`：那兩個 `_guard` 是同步呼叫，改了會變成
    「產生一個沒人 await 的 coroutine」＝**守衛靜默失效**（2026-08-15 試過，
    測試當場抓到）。
    """
    if not can_see_money(request):
        raise HTTPException(status_code=403, detail="沒有金額檢視權限")
    return True


# 便宜的預篩：`MONEY_FIELDS` 每一個鍵都含這幾個子字串之一（由
# `test_prefilter_tokens_cover_every_money_field` 釘住），所以「原始 bytes 裡
# 一個 token 都沒出現」就保證**沒有東西要抹**。這是嚴格的超集過濾，不可能改變
# 行為，卻能讓多數回應完全跳過 parse + redact。
#
# 為什麼值得：owner 的政策是「預設不給」，所以**沒授權才是多數路徑**。實測
# 236 筆專案（118KB、無金額欄）走 parse+redact 是 1.6ms 而且結果整份丟掉；
# 走這條預篩是 0.34ms。命中金額時多付的成本是 0.0008ms。
_PREFILTER = (b"amount", b"rate", b"cost", b"price", b"fee",
              b"profit", b"budget", b"ex_tax", b"contract", b"balance")


def redact(obj: Any) -> Any:
    """遞迴刪掉 `MONEY_FIELDS` 的鍵（list/dict 混排都吃）。"""
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items() if k not in MONEY_FIELDS}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


class MoneyRedactRoute(APIRoute):
    """第二層：掛在 router 上，沒授權的請求出口統一抹掉金額欄位。

    **為什麼掛 router 而不是逐支改**：同樣兩個欄位（`rate`/`cost`）散在四支端點
    （派工、成本明細、成本摘要、某人的專案清單），逐支加漏一支就等於沒修 ——
    2026-08-14 那個匿名可讀的洞就是這樣長出來的。掛這裡，新端點預設就是安全的。

    有授權的請求**原樣返回**（連 body 都不碰）。沒授權的先走 `_PREFILTER`
    這道 bytes 掃描，只有真的帶金額字樣的回應才付 parse + redact。
    非 JSON 的回應（收據圖片、縮圖、PDF、串流）跳過：它們沒有欄位可抹。
    """

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            if can_see_money(request):
                return response
            body = getattr(response, "body", None)
            if not body or "application/json" not in response.headers.get("content-type", ""):
                return response
            if not any(t in body for t in _PREFILTER):
                return response
            try:
                data = json.loads(body)
            except ValueError:
                return response
            clean = redact(data)
            # 沒動到就別重建 —— 深度比較實測 0.00~0.04ms（redact 重用了葉節點，
            # `==` 在每個純量上以 identity 短路），換掉的是 0.88ms 的 json.dumps。
            if clean == data:
                return response
            return JSONResponse(
                content=clean, status_code=response.status_code,
                # content-length 由 JSONResponse 依新 body 重算，不能沿用舊的
                headers={k: v for k, v in response.headers.items()
                         if k.lower() not in ("content-length", "content-type")},
                background=response.background,
            )

        return handler
