"""core/api_docs.py — 互動式 API 文件的開關（單一正本）。

🔴 `/docs`、`/redoc`、`/openapi.json` **預設全關**。這三條無認證就吐出完整的
端點清單與參數形狀 —— 等於把內部 API 的地圖公告出去，讓「要先猜到路徑才用得到」
的冷門端點變成照著說明書就能打。2026-08-08 實測：生產 8000 的 /openapi.json
無認證可取得，而且裡面就列著當時無守衛的 utils/read_text。

8000 經 cloudflared 對外（foundry）、8001 也有對外網址（foundrytest），
NAS 對外容器更不用說 —— 三邊都關，所以判斷放在 core/（唯一同步得到容器的地方）。

需要看文件：settings.json 加 `"expose_api_docs": true` 再重啟。
"""
from __future__ import annotations


def docs_urls() -> dict:
    """FastAPI() 的 kwargs：預設把三條文件路徑關掉。"""
    try:
        from config import load_settings
        if load_settings().get("expose_api_docs"):
            return {}
    except Exception:
        pass
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}
