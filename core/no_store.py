# -*- coding: utf-8 -*-
"""不留快取的檔案回應。

主機對靜態檔一律回 no-cache（瀏覽器帶 ETag 問一句、304 不重傳本體）；但有些檔案有 ETag 卻不該進
磁碟快取——財務文件（發票 PDF、雜支收據：不留任何副本）、OTA 安裝包（~1GB，快取只是佔空間）。
這種由 handler 自己在回應上宣告，NoCacheMiddleware 看到 handler 宣告過就不再疊自己的（main.py）。
以前是 middleware 用網址前綴列例外，結果同一份發票 PDF 三條路只擋到一條（2026-09-03 /simplify 抓到）。
"""
from fastapi.responses import FileResponse

NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"
NO_STORE_B = NO_STORE.encode()   # middleware 那側是 bytes header


def no_store_file(path, **kwargs) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": NO_STORE}, **kwargs)
