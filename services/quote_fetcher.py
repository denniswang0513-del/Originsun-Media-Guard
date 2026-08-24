# -*- coding: utf-8 -*-
"""services/quote_fetcher.py — 證券報價抓取（資產儀表板，§8 階段 5）。

免 key 公開源，stdlib urllib（零新依賴，比照 intel_runner 的 RSS 抓取）：
- 台股：TWSE MIS 即時（`tse:2330` → mis.twse.com.tw getStockInfo，欄 z=成交價，
  盤後為當日收盤；z 為 '-' 時退用 y=昨收）。
- 美股/ETF/匯率：Yahoo Finance chart API（`yahoo:VTI` / `yahoo:VWRA.L` /
  `yahoo:USDTWD=X` → query1.finance.yahoo.com/v8/finance/chart，
  meta.regularMarketPrice）。⚠ 2026-08-24 實測 stooq 的 CSV 端點已加
  JS 反爬（404/驗證頁）—— 別換回去。

守則（自動化憲章）：kill switch＝settings `my_ledger.quotes_enabled`（預設開）；
逐檔失敗**不拋**，回 None 讓呼叫端沿用 last_price 標舊價 —— 報價源斷線
不能擋拍快照。整批抓有 8 秒/檔逾時、逐檔 try。

同步函式（urllib blocking）—— async 呼叫端用 asyncio.to_thread 包。
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_TIMEOUT = 8
_UA = {"User-Agent": "Mozilla/5.0 (OriginsunLedger quote fetcher)"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read()


def fetch_tse(code: str) -> float | None:
    """台股（上市）：成交價，盤中即時/盤後收盤。失敗回 None。"""
    try:
        raw = _get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                   f"?ex_ch=tse_{code}.tw&json=1&delay=0")
        arr = json.loads(raw).get("msgArray") or []
        if not arr:
            return None
        z = (arr[0].get("z") or "").strip()
        if not z or z == "-":
            z = (arr[0].get("y") or "").strip()   # 無成交 → 昨收
        return float(z) if z and z != "-" else None
    except Exception as e:   # noqa: BLE001 — 逐檔失敗不拋（見 docstring）
        logger.warning("[quotes] tse:%s 失敗: %s", code, e)
        return None


def fetch_yahoo(symbol: str) -> float | None:
    """Yahoo chart API 的 regularMarketPrice。失敗回 None。"""
    try:
        raw = _get("https://query1.finance.yahoo.com/v8/finance/chart/"
                   f"{urllib.parse.quote(symbol)}?interval=1d&range=1d")
        meta = ((json.loads(raw).get("chart") or {}).get("result") or [{}])[0].get("meta") or {}
        px = meta.get("regularMarketPrice")
        return float(px) if px else None
    except Exception as e:   # noqa: BLE001
        logger.warning("[quotes] yahoo:%s 失敗: %s", symbol, e)
        return None


def fetch_quote(quote_symbol: str) -> float | None:
    """'tse:2330' / 'yahoo:VTI' → 單價（原幣）。不認得/空 → None。"""
    qs = (quote_symbol or "").strip()
    low = qs.lower()
    if low.startswith("tse:"):
        return fetch_tse(low.split(":", 1)[1])
    if low.startswith("yahoo:"):
        return fetch_yahoo(qs.split(":", 1)[1])   # 保留大小寫（VWRA.L / USDTWD=X）
    return None


def fetch_usd_twd() -> float | None:
    return fetch_yahoo("USDTWD=X")
