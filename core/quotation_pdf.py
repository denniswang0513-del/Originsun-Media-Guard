"""報價單 PDF 的檢視模型（純函式，無 I/O）。

版面定稿 2026-09-06（示範頁 frontend/demo/quotation-pdf.html 是視覺正本）：
標題＋logo → 客戶／專案／規格 → 明細表（分組列帶小結）→ 結算靠右 → 備註清單 →
匯款資訊一行 → 兩個簽章框 → 頁尾一行（公司資訊、日期、版本、頁碼）。

金額規則（owner 拍板）：`discount` 是稅前折扣（後端 _calc_quotation 已算進 total）；
**專案優惠**不是欄位，是「含稅總計 − final_price」的差額，只在 final_price 低於 total 時印。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from core.hr_logic import tw_day

_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_SPEC_SEP = re.compile(r"[、\r\n]+")

COMPANY_DEFAULTS = {
    "name": "源日有限公司", "name_en": "ORIGINSUN STUDIO", "tax_id": "",
    "address": "", "phone": "", "email": "",
    "bank": "", "account_name": "", "account_no": "",
    "quote_valid_days": 14, "delivery_terms": "", "logo_path": "", "seal_path": "",
}


def nt(n) -> str:
    """NT$ 千分位（整數；None／空 → NT$0）。"""
    return "NT$" + f"{int(round(float(n or 0))):,}"


def ymd(d: Optional[date]) -> str:
    return f"{d.year}.{d.month:02d}.{d.day:02d}" if d else ""


def _as_date(v) -> Optional[date]:
    """ISO 字串／datetime／date → 台北日期；空 → None。"""
    if not v:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return tw_day(v)


def spec_lines(spec) -> list[str]:
    """規格用「、」或換行切成一項一行（示範頁的規則；數字與單位不會被拆開）。"""
    return [x.strip() for x in _SPEC_SEP.split(str(spec or "")) if x.strip()]


def promo_amount(total, final_price) -> int:
    """專案優惠＝含稅總計 − 最終報價，只有最終報價較低時才有。"""
    if final_price is None:
        return 0
    return max(int(total or 0) - int(final_price), 0)


def pdf_filename(quote_date: Optional[date], client_name: str, project_name: str, suffix: str = "源日報價單") -> str:
    """`YYYYMMDD_客戶_專案_源日報價單.pdf`；檔名裡的路徑字元換成 `-`。"""
    d = quote_date or date.today()
    parts = [d.strftime("%Y%m%d")] + [
        _FILENAME_BAD.sub("-", str(x or "")).strip() for x in (client_name, project_name)
    ]
    return "_".join(p for p in parts if p) + f"_{suffix}.pdf"


def build_quotation_view(q: dict, company: Optional[dict] = None, *,
                         client_name: str = "", project_name: str = "",
                         today: Optional[date] = None) -> dict:
    """`_to_quotation_dict` 的輸出 → 模板要的一切（金額字串、分組、備註清單、檔名）。"""
    co = {**COMPANY_DEFAULTS, **(company or {})}
    try:
        valid_days = int(co.get("quote_valid_days") or 14)
    except (TypeError, ValueError):
        valid_days = 14

    quote_date = _as_date(q.get("quote_date")) or today or date.today()
    valid_until = _as_date(q.get("valid_until")) or (quote_date + timedelta(days=valid_days))
    valid_days = max((valid_until - quote_date).days, 0)

    groups: list[dict] = []
    for it in q.get("items") or []:
        name = (it.get("group_name") or "").strip()
        if not groups or groups[-1]["name"] != name:
            groups.append({"name": name, "rows": [], "subtotal": 0})
        amount = int(it.get("amount") or 0)
        groups[-1]["rows"].append({
            "description": it.get("description") or "",
            "note": it.get("note") or "",
            "quantity": it.get("quantity") or 0,
            "unit": it.get("unit") or "",
            "unit_price_fmt": nt(it.get("unit_price")),
            "amount_fmt": nt(amount),
        })
        groups[-1]["subtotal"] += amount
    for g in groups:
        g["subtotal_fmt"] = nt(g["subtotal"])

    total = int(q.get("total") or 0)
    final_price = q.get("final_price")
    promo = promo_amount(total, final_price)
    final = int(final_price) if final_price is not None else total

    payment = [{
        "label": s.get("label") or "", "pct": s.get("pct") or 0,
        "amount_fmt": nt(final * float(s.get("pct") or 0) / 100),
    } for s in (q.get("payment_stages") or []) if isinstance(s, dict)]

    terms_lines = [t.strip() for t in str(q.get("terms") or "").splitlines() if t.strip()]
    client = client_name or q.get("client_short_name") or ""      # 客戶全稱優先，退回代稱
    project = project_name or q.get("project_name") or ""

    return {
        "version": q.get("version") or 1,
        "client_name": client,
        "project_name": project,
        "spec_lines": spec_lines(q.get("spec")),
        "quote_date": ymd(quote_date),
        "valid_until": ymd(valid_until),
        "valid_days": valid_days,
        "groups": groups,
        "subtotal_fmt": nt(q.get("subtotal")),
        "discount": int(q.get("discount") or 0),
        "discount_fmt": nt(q.get("discount")),
        "tax_rate": q.get("tax_rate") if q.get("tax_rate") is not None else 5,
        "tax_fmt": nt(q.get("tax_amount")),
        "promo": promo,
        "promo_fmt": nt(promo),
        "final_fmt": nt(final),
        "payment": payment,
        "terms_lines": terms_lines,
        "delivery_terms": (co.get("delivery_terms") or "").strip(),
        "company": co,
        "filename": pdf_filename(quote_date, client, project),
    }


def footer_line(view: dict) -> str:
    """頁尾一行：公司資訊、日期、版本（頁碼由 PDF 產生器補）。"""
    co = view["company"]
    bits = [co.get("name") or ""]
    if co.get("tax_id"):
        bits.append(f"統一編號 {co['tax_id']}")
    bits += [co.get("address") or "", co.get("phone") or "", co.get("email") or "",
             view["quote_date"], f"v{view['version']}"]
    return "　".join(b for b in bits if b)
