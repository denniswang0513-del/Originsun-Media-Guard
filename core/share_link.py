"""core/share_link.py — 寄給外面的人的連結要用哪個網域（**單一正本**）。

報價單的 `/q/{短碼}` 與電子發票的 `/e/{短碼}` 都是「複製一條網址、貼進信裡寄出去」。
那條網址原本是前端用 `location.origin` 組的 —— 也就是**按複製的人當時開在哪個網址**：
內網 IP、localhost、cloudflared 的對外網域，甚至開發機的 8001。同一張報價寄給兩個客戶
可能是兩個網域，而其中一個根本不對外。

所以改成一個設定。owner 2026-09-10 拍板：**報價與發票共用同一個**（分兩個設定只會
有一天其中一個忘了填）。

留空＝回相對路徑，前端沿用 `location.origin`（＝改動前的行為，不會突然變）。

🔴 舊鍵 `quotes_public_base` 保留為 fallback：Cloudflare 給 `.js` 四小時瀏覽器快取，
   換名之後那一輪還會有舊分頁在送舊鍵；而且生產的 settings.json 可能已經填了舊鍵。
   **一輪之後**（設定確定搬到新鍵）可以連同這個 fallback 一起拿掉。
"""
from __future__ import annotations

KEY = "share_public_base"
LEGACY_KEY = "quotes_public_base"


def public_base(settings: dict) -> str:
    """設定 dict → 對外網址（已去尾斜線）。新鍵優先，其次舊鍵，都沒有回空字串。"""
    s = settings or {}
    for k in (KEY, LEGACY_KEY):
        v = str(s.get(k) or "").strip().rstrip("/")
        if v:
            return v
    return ""


def share_url(path: str, base: str = "") -> str:
    """`/q/abc`、`/e/abc` ＋ 對外網址 → 完整連結；沒設定就原樣回相對路徑。"""
    p = str(path or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    b = str(base or "").strip().rstrip("/")
    return b + p if b else p


def clean_base(raw) -> str:
    """使用者填進設定的字串 → 乾淨的來源（scheme + host），尾斜線去掉。

    收垃圾的後果是安靜的：連結是**複製給客戶**的，錯了不會有 error log，
    只會有人說「你給我的網址打不開」。空字串合法（＝不指定，回到 location.origin）。

    回 `(值, 錯誤訊息)`：這支是純函式，不丟 HTTPException —— 呼叫端決定怎麼回報。
    """
    v = str(raw or "").strip().rstrip("/")
    if not v:
        return "", ""
    if not v.startswith(("http://", "https://")):
        return "", "對外連結網址要以 http:// 或 https:// 開頭"
    if len(v.split("/")) != 3:
        return "", "對外連結網址只要網址開頭（例：https://www.example.com）"
    return v, ""
