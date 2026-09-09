"""core/quote_snapshot.py — 「生成報價單」的純規則（無 I/O）。

owner 2026-09-10 拍板把報價單從「活的網頁」改成「一份生成出來的文件」：

  以前：客戶點 /q/{code}，系統當場從資料庫算金額、當場畫版面、當場開 Chromium 產 PDF。
        → ① master 關機就整條死（客戶只看到 502，而且他不知道發生什麼事）
          ② 你在系統裡改了什麼，客戶手上那份就**靜默地跟著變** —— 報價單是給客戶的
             定稿文件，不該有這種行為。

  現在：按「生成報價單」→ 產一份 PDF ＋ 一份 HTML 快照 → 存進共用圖床（NAS，24/7）。
        客戶的連結送的是**那兩個檔案**，不重算、不重畫、不開 Chromium。
        於是對外那條路只需要「把檔案送出去」，NAS 對外容器就做得到。

本檔只管形狀與判斷，不碰檔案系統也不碰 DB：
  - 快照紀錄長什麼樣（存在 `crm_quotations.pdf_snapshot`，JSONB）
  - 「生成之後又改過了嗎」怎麼判
  - 畫面上那句話怎麼寫

🔴 過期判定用的是 **`src`（生成時當下的 `updated_at`）逐字比對**，不是時間先後。
   理由是 race：產 PDF 要好幾秒，中間有人存了一次的話，用「生成時間 > 更新時間」
   會判成新鮮的（生成時間必然比較晚），而內容其實是舊的。比對來源版本沒有這個洞。
   解析不出來一律當「過期」—— 寧可多叫使用者按一次，不要靜默送舊檔給客戶。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

# 共用圖床（NAS PasteAssets）的命名空間，見 core/assets_host.py。
# 用圖床而不是另開一個資料夾：master 用 UNC 寫得到、NAS 對外容器已經掛載，
# 「兩台機器都看得見同一份檔」這件事那邊已經解過一次了。
NAMESPACE = "quote"

# 檔名形狀被 core.assets_host._SAFE_NAME 綁著（32 hex ＋ 2-5 碼副檔名）——
# 那是圖床唯一的刪除路徑，名字不合它的規矩就刪不掉，舊快照會永遠躺在 NAS 上。
PDF_EXT, HTML_EXT = "pdf", "html"

LABEL_NONE = "尚未生成"
LABEL_STALE = "內容已修改，尚未重新生成"


def new_asset_names() -> tuple:
    """→ (pdf 檔名, html 檔名)。每次生成都換一組新的亂數名字。

    為什麼不用報價 id 當檔名：① 換名字＝舊網址立刻失效，不會有人拿到半新半舊的
    快取副本 ② 圖床是對外可讀的，檔名可猜等於少一道門。
    """
    stem = uuid.uuid4().hex
    return f"{stem}.{PDF_EXT}", f"{stem}.{HTML_EXT}"


def make_record(pdf_name: str, html_name: str, filename: str,
                src_updated_at, generated_at=None) -> dict:
    """組出要存進 `pdf_snapshot` 的那一包。

    filename：下載時要顯示的檔名（`YYYYMMDD_客戶_專案_源日報價單.pdf`）。**存起來**
    而不是每次重算 —— 之後改了案名或報價日期，客戶手上那份的檔名不該跟著變，
    它就叫生成當下那個名字。
    """
    return {
        "pdf": str(pdf_name), "html": str(html_name),
        "filename": str(filename or ""),
        "src": _iso(src_updated_at),
        "at": _iso(generated_at or datetime.now().astimezone()),
    }


def assets_of(record: Optional[dict]) -> list:
    """這筆快照佔用的圖床檔名（重新生成後拿去刪舊的）。"""
    if not isinstance(record, dict):
        return []
    return [str(record.get(k) or "") for k in ("pdf", "html") if record.get(k)]


def asset_name(record: Optional[dict], kind: str) -> str:
    """kind＝'pdf'／'html' → 圖床檔名；沒有就回空字串。"""
    if not isinstance(record, dict) or kind not in ("pdf", "html"):
        return ""
    return str(record.get(kind) or "")


def download_filename(record: Optional[dict]) -> str:
    if not isinstance(record, dict):
        return ""
    return str(record.get("filename") or "")


def is_stale(record: Optional[dict], updated_at) -> bool:
    """生成之後又被改過了嗎。沒有快照也算 True（＝不能送，要先生成）。"""
    if not isinstance(record, dict) or not record.get("pdf"):
        return True
    src = str(record.get("src") or "")
    now_src = _iso(updated_at)
    if not src or not now_src:
        return True                      # 判不出來就當過期（見檔頭）
    return src != now_src


def state(record: Optional[dict], updated_at) -> dict:
    """畫面用的一包：{generated: bool, stale: bool, generated_at: str, label: str}。

    文案只在這裡寫一次，桌機與手機都直接顯示後端給的 `label` —— 兩邊各寫一份
    中文的話，改字就會漏掉一邊。
    """
    has = isinstance(record, dict) and bool(record.get("pdf"))
    stale = is_stale(record, updated_at)
    at = str(record.get("at") or "") if has else ""
    if not has:
        label = LABEL_NONE
    elif stale:
        label = LABEL_STALE
    else:
        label = f"已生成 {_human(at)}" if at else "已生成"
    return {"generated": has, "stale": stale, "generated_at": at, "label": label}


def share_url(token: str, base: str = "") -> str:
    """客戶連結。`base` 是設定裡的對外網址（settings `quotes_public_base`）；
    沒設定就回相對路徑，讓前端沿用 `location.origin`（＝現況行為，不會突然改變）。

    🔴 這個設定存在的理由：連結原本是 `location.origin + /q/xxx` 組出來的，
    也就是「按複製的人當時在哪個網址，客戶就拿到哪個網址」。同一張報價單發給
    兩個客戶可能是兩個網域，而其中一個（開發機的 8001）根本不對外。
    """
    tok = str(token or "").strip()
    if not tok:
        return ""
    b = str(base or "").strip().rstrip("/")
    return f"{b}/q/{tok}" if b else f"/q/{tok}"


# ── 內部 ──────────────────────────────────────────────────────

def _iso(value) -> str:
    """datetime／字串 → 逐字比對用的 ISO 字串。認不得的東西一律回空字串
    （呼叫端會把空字串當成「判不出來」＝過期）。"""
    if isinstance(value, datetime):
        return value.isoformat()
    v = str(value or "").strip()
    return v


def _human(iso: str) -> str:
    """ISO → 「09/10 14:30」。解析不出來就原樣回去（畫面上寧可醜，不要空白）。"""
    try:
        return datetime.fromisoformat(iso).strftime("%m/%d %H:%M")
    except (TypeError, ValueError):
        return iso
