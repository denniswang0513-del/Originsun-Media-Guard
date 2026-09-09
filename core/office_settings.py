"""core/office_settings.py — 要送到 NAS `office-api` 的那幾個設定（**允許清單**）。

## 問題

容器裡沒有 `settings.json`，`config.load_settings()` 拿到的就是 `_DEFAULT_SETTINGS`。
發票那三個鍵不在預設裡，所以在 NAS 上：

    invoices_root       發票影像上傳／下載直接失敗（訊息還會誤導成「管理員尚未設定」）
    invoice_fee_rates   代開費算成 0 —— **靜默的金額錯誤**，不會有任何 error
    invoice_applicants  申請人下拉是空的

## 做法

`/publish` 時把**清單內的鍵**倒成一份 `settings.json` 送到 NAS 的 code 目錄。
`load_settings()` 本來就會把檔案內容跟預設值深度 merge，所以缺的鍵照常拿預設，
容器那側一行程式都不用改。

## 為什麼不是「把整個 settings.json scp 過去」

那裡面有 `jwt_secret`、`database_url`、Google `client_secret`、工時同步 token、
四個 webhook。容器需要的那兩個（DB／JWT）由 env 給、而且只給該給的，沒有理由再把
一整份機密放到 NAS 的 code 目錄。

🔴 這裡是**允許清單不是排除清單**：之後有人往 settings 加了新的機密欄位，預設不會被
送出去。`tests/unit/test_office_surface.py` 另外釘住「清單不可與 api_system 的三組
機密欄位相交」——那是萬一有人把機密鍵加進來的第二道。

## 為什麼不是搬進共用 DB（原規劃 docs/OFFLINE_MASTER_PLAN.md §7.1 的建議）

那要動到**算錢的讀取路徑**（`crm/finance.py`、`crm/invoice_files.py` 有幾處是同步的
`load_settings()`），風險比這批其他部分加起來都高。而這三個鍵幾乎不會變（設定一次用
一年），所以「發版時同步一次」拿到的舊值是**上一次已知good的值**，比拿到 0 好太多。

🔴 代價寫在這裡：master 改了這幾個值之後，NAS 要**等下一次發版**才會跟上。
   哪天這些值開始常改（特別是 `invoice_fee_rates`），就照 `core/public_access.py`
   的形狀（DB → settings.json → 預設，TTL 快取）搬進共用 DB。
"""
from __future__ import annotations

# 送去 NAS 的鍵。加東西進來前先問兩件事：
#   1. office-api 上真的有程式碼會讀它嗎？（不讀就別送，曝露面沒有理由變大）
#   2. 它會不會夾帶機密？（子鍵也算 —— 例如 `timesheet` 裡有 ingest_token，所以整個不送）
EXPORT_KEYS = (
    "invoices_root",        # 發票影像的落地根目錄（crm/invoice_files）
    "invoice_fee_rates",    # 代開費率 —— 沒有它金額會靜默算錯（crm/finance）
    "invoice_applicants",   # 申請人下拉
    "assets_host",          # 共用圖床：貼圖、影像紀錄縮圖、報價單快照都在這
    "drive_map",            # 磁碟代號 → UNC 的覆寫（上面那些路徑要靠它翻譯）
)


def export_settings(settings: dict) -> dict:
    """`settings` → 只含 EXPORT_KEYS 且**值不是 None** 的淺拷貝。

    值是 None／不存在的鍵不送：送過去只會把容器那側的預設值蓋成 None，
    比沒送更糟（`load_settings` 的 merge 會照收）。
    """
    out = {}
    for k in EXPORT_KEYS:
        v = (settings or {}).get(k)
        if v is not None:
            out[k] = v
    return out
