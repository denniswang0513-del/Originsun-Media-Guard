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

from core.auth import current_payload, payload_grants

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
#   - `amount` / `subtotal` / `balance` / `deposit` 只出現在**整支 403**的帳務與
#     報價端點上，收進來零收益、誤殺卻是真的
#
# 新增欄位時的判準：**它自己或它與另一個可見欄位的乘積是不是金額**
# （`days` 留著，所以 `rate` 一定要抹）。
MONEY_FIELDS = frozenset({
    # 專案（crm_projects）
    "contract_amount", "amount_receivable", "amount_received",
    "profit_target_pct", "profit_target", "misc_budget_pct",
    "budget_amount", "misc_budget_amount", "ex_tax", "total_contract",
    # 人／派工（crm_staff / crm_project_staff）—— rate 系列是 cost 的還原路徑
    "daily_rate", "hourly_rate", "day_rate",
    "rate", "rate_override", "default_rate",
    "cost", "actual_cost", "total_cost",
    # 成本明細（crm_project_cost_lines）
    "estimated_unit_price", "estimated_amount",
    "actual_unit_price", "actual_amount", "unit_price",
    "paid_amount", "unpaid_amount",
})


def can_see_money(request: Request) -> bool:
    """這個請求看不看得到金額。管理員（Lv3）一律 True。

    刻意收在這裡而不是各處自己 `payload_grants(..., 'money_view')`：
    「誰算看得到錢」將來若要放寬（例如專案負責人看自己的案子），只有這一支要改。
    """
    return payload_grants(current_payload(request), MODULE_KEY)


def check_money(request: Request):
    """第一層守衛：整支端點就是錢 → 沒授權直接 403。"""
    if not can_see_money(request):
        raise HTTPException(status_code=403, detail="沒有金額檢視權限")
    return True


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

    有授權的請求**原樣返回**（連 body 都不碰）—— 成本只落在沒授權的那條路上。
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
            try:
                data = json.loads(body)
            except ValueError:
                return response
            clean = redact(data)
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
