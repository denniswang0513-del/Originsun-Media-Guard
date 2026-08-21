# -*- coding: utf-8 -*-
"""從電子發票證明聯（PDF 抽出來的文字）讀欄位，並跟系統裡那張發票對照。

需求來源（Soca 2026-08-21，owner 轉）：
> 「上傳發票 pdf 可以偵測上面的抬頭和金額是否跟表單上一致，如果不一樣可以警示我。
>   因為現在這樣如果一次多張，我有點怕會傳錯張。」

所以這裡的產出是**警示**不是閘門 —— 他怕的是「傳錯張」，不是「填錯欄」。
PDF 抽字本來就會有抽不到的情況（掃描件、字型把字拆開、版面不同），一旦擋下來
就會變成「明明是對的卻傳不上去」，那比偶爾漏警示更糟。所以規則是：

    抽得到而且對不上  → 警示
    抽不到            → 安靜（不猜、不警示）

純函式、無 I/O —— 檔案讀取在 routers/crm/invoice_files.py，這裡只吃文字。
"""
from __future__ import annotations

import re

# 台灣發票號碼：兩碼英文 + 8 碼數字
TW_INVOICE_NO = r"[A-Z]{2}\d{8}"

# 證明聯常把字距拉開（letter-spacing），抽出來會變成「發 票 號 碼」——
# 所以每個字之間都要容許空白。冒號可能被字型吃掉，用 `[:：]?`。
INVOICE_NO_LABELLED = re.compile(
    r"發\s*票\s*號\s*碼\s*[:：]?\s*(" + TW_INVOICE_NO + r")")

# 買方統編。「買方」「買受人」可能有也可能沒有；核心是「統一編號」四個字。
TAX_ID_LABELLED = re.compile(
    r"統\s*一?\s*編\s*號\s*[:：]?\s*(\d{8})")

# 金額。證明聯上通常是「總計」（含稅），也見過「總 計」「合計」。
# 🔴 只認**含稅總額**那一個標籤 —— 銷售額（未稅）跟營業稅也在同一張紙上，
#    認錯標籤就會拿未稅價去比含稅價，每張都跳假警示。
AMOUNT_LABELLED = re.compile(
    r"(?:總\s*計|總\s*金\s*額|合\s*計)\s*[:：]?\s*[\$＄]?\s*([\d,]+)")


def _digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s or "")


def _squash(s: str) -> str:
    """把所有空白拿掉 —— PDF 抽出來的中文常夾雜空格與換行，
    直接字串比對一定不相等（「松山文創園區」可能變成「松山文創 園區」）。"""
    return re.sub(r"\s+", "", s or "")


def parse_invoice_text(text: str) -> dict:
    """證明聯文字 → 欄位。抽不到的欄位回 None（**不是** 0 或 ""，
    呼叫端才分得出「抽不到」與「抽到 0」）。"""
    text = text or ""
    m = INVOICE_NO_LABELLED.search(text)
    number = m.group(1) if m else None
    if not number:
        # 沒有標籤時才退而求其次：全文**恰好只有一組**才推定。
        # 證明聯上還有隨機碼、載具號碼，多於一組時猜錯的代價（法定號碼寫錯）
        # 遠大於讓人自己填。
        hits = set(re.findall(TW_INVOICE_NO, text))
        number = hits.pop() if len(hits) == 1 else None

    m = TAX_ID_LABELLED.search(text)
    tax_id = m.group(1) if m else None

    m = AMOUNT_LABELLED.search(text)
    amount = int(_digits(m.group(1))) if m and _digits(m.group(1)) else None

    return {"invoice_number": number, "tax_id": tax_id, "amount_total": amount}


def compare_invoice_pdf(parsed: dict, inv: dict, text: str = "") -> list:
    """比對「PDF 上的」與「系統裡的」，回警示訊息（空 list ＝ 沒發現問題）。

    四個訊號，可靠度由高到低：

      統編    8 碼數字，精確比對 —— **最可靠**，錯了幾乎確定是傳錯張
      金額    含稅總額，精確比對 —— 次可靠
      發票號碼 已經另外回報給使用者選擇套用，這裡也一起比
      抬頭    只做「有沒有出現在全文裡」的包含檢查（拿掉所有空白後）——
              不試圖把公司名從版面裡切出來，那在中文 PDF 上很不可靠

    抬頭那條刻意最寬鬆：抽不到就安靜。它存在的意義是「明顯是別家公司」時出聲。
    """
    warns = []
    if not parsed:
        return warns

    want_tax = _digits(inv.get("tax_id"))
    got_tax = parsed.get("tax_id")
    if want_tax and got_tax and _digits(got_tax) != want_tax:
        warns.append(f"統編不一致：PDF 上是 {got_tax}，表單填的是 {inv.get('tax_id')}")

    want_amt = inv.get("amount_total")
    got_amt = parsed.get("amount_total")
    if want_amt and got_amt is not None and int(want_amt) != int(got_amt):
        warns.append(
            f"金額不一致：PDF 上是 {got_amt:,}，表單填的是 {int(want_amt):,}")

    want_no = (inv.get("invoice_number") or "").strip()
    got_no = parsed.get("invoice_number")
    if want_no and got_no and got_no != want_no:
        warns.append(f"發票號碼不一致：PDF 上是 {got_no}，表單填的是 {want_no}")

    want_co = _squash(inv.get("company_name"))
    if want_co and text:
        # 兩碼以下的抬頭不做包含檢查（太容易誤中）
        if len(want_co) > 2 and want_co not in _squash(text):
            warns.append(f"PDF 上找不到抬頭「{inv.get('company_name')}」")
    return warns
