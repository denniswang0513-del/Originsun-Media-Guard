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
