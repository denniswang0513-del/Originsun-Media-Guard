"""core/assets_host.py — 共用圖床的定址（單一實作）。

一台圖床（NAS PasteAssets + Assets_Nginx 容器，24/7 對外、不依賴 master），
底下用**命名空間**分區：

    paste     全站 textarea 貼上的圖片（routers/api_paste.py）
    medialog  影像紀錄的縮圖（routers/crm/media_log.py）

為什麼共用一台而不各開一份設定：兩者需求完全相同（不可猜檔名、對外可讀、
master 關機仍在），各開一份就會有兩份要同步搬家的網址。命名空間讓「哪個功能
的圖」在檔案系統與網址上都看得出來，清理政策也能分開下。

寫入端一律用 assets_target() 拿 (本機可寫目錄, 對外 base_url)；
DB 只存檔名/token 不存網域 —— 圖床搬家改 settings 一行，資料一筆都不用動。
"""
import os
import re

import config
from core.drive_map import to_local_path

# (settings.json mtime, {namespace: (dir, base_url)}) —— 縮圖/貼圖熱路徑每張圖都
# 呼叫，load_settings() 無快取（每次 open+json.load+merge，且跑在 event loop 上）。
# 照 drive_map.effective_map 的 mtime 失效慣例：設定極少變，改檔即失效。
_cache: tuple = (None, {})


def assets_target(namespace: str) -> tuple:
    """→ (dir, base_url)。dir 已翻成執行本機的視角（NAS 容器拿到掛載點）。

    設定缺漏 → ("", "")，呼叫端據此回 503（誠實回報未設定，不要默默寫到別處）。
    """
    ns = str(namespace or "").strip("/")
    if not ns:
        return "", ""
    global _cache
    try:
        mtime = os.path.getmtime(config._SETTINGS_FILE)
    except OSError:
        mtime = None
    if _cache[0] != mtime:
        _cache = (mtime, {})              # 設定變了 → 清整個命名空間快取
    if ns not in _cache[1]:
        conf = config.load_settings().get("assets_host") or {}
        root = str(conf.get("dir") or "").strip()
        base = str(conf.get("base_url") or "").strip().rstrip("/")
        _cache[1][ns] = ((os.path.join(to_local_path(root), ns), f"{base}/{ns}")
                         if root and base else ("", ""))
    return _cache[1][ns]


# 檔名只准是「上傳時產生的那種形狀」：32 hex + 副檔名。這支是圖床唯一的刪除路徑，
# 不做成通用刪檔端點 —— 呼叫端傳進來的字串一律不信（路徑穿越、砍到別的命名空間）。
_SAFE_NAME = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,5}$")


def assets_delete(namespace: str, filename: str) -> bool:
    """刪掉某個命名空間裡的一個檔。回「這次真的刪掉了嗎」。

    用途：報價助理的截圖用完就刪（owner 2026-09-09 拍板；docs/QUOTE_ASSISTANT_PLAN.md §5.4）
    —— 客戶的 LINE 截圖會帶大頭貼與姓名，不該一直躺在圖床上。

    刪不掉（設定沒設、檔名不合法、檔案已經不在、權限）→ 回 False，**不丟例外**：
    呼叫端是「順手清理」，不該因為清不掉就讓寄出報價失敗。
    """
    if not _SAFE_NAME.match(str(filename or "")):
        return False
    root, _base = assets_target(namespace)
    if not root:
        return False
    path = os.path.join(root, filename)
    # 正規化後必須仍在該命名空間底下（雙保險，檔名已經先過一次白名單）
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(root):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False
