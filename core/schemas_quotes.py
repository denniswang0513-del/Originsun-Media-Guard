# -*- coding: utf-8 -*-
"""core/schemas_quotes.py — 報價相關的請求模型。

從 core/schemas.py 搬出來（同 core/schemas_website.py 的前例）：那個檔已經到
`tests/unit/test_files_stay_readable.py` 的單次讀取上限 2000 行，再往上加就會被
靜默截斷。`core.schemas` 仍然 re-export 這四個名字，既有的 import 一行都不用改。
"""
from typing import List, Optional

from pydantic import BaseModel


class QuotationItemPayload(BaseModel):
    group_name: str = ""
    description: str
    unit: str = "式"
    quantity: int = 1
    unit_price: int = 0
    internal_cost: int = 0
    note: str = ""


class QuotationPayload(BaseModel):
    status: str = "草稿"
    quote_date: Optional[str] = None
    valid_until: Optional[str] = None
    discount: int = 0
    tax_rate: int = 5
    final_price: Optional[int] = None
    payment_stages: List[dict] = []
    terms: str = ""
    # 🔴 Optional：沒送＝不動（不是清空）。CF 給 .js 4 小時快取，舊分頁 PUT 不帶 spec，
    # 用 `str = ""` 會把別人剛填的規格洗掉（同型事故見 reference_cloudflare_js_cache）。
    spec: Optional[str] = None         # 規格（報價單抬頭；「、」或換行分隔多項）
    items: List[QuotationItemPayload] = []


class PriceItemPayload(BaseModel):
    """改一筆價目（沒送的欄位不動 —— 前端只想改價的時候不該把描述洗掉）。"""
    description: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[int] = None


class QuoteChatPayload(BaseModel):
    """對話式完成報價：使用者這一輪說的話（可含貼圖 token）。docs/QUOTE_ASSISTANT_PLAN.md"""
    text: str = ""


class QuotationTemplatePayload(BaseModel):
    name: str
    description: str = ""
    tax_rate: int = 5
    terms: str = ""
    payment_stages: List[dict] = []
    items: List[QuotationItemPayload] = []
