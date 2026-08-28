

def test_gzip_middleware_only_touches_json():
    """🔴 只壓 `application/json`。starlette 的 GZipMiddleware 不分內容型別，
    會連 OTA 的 ~1GB ZIP（純燒 CPU）、看片門戶帶 Range 的影片（壓了 Range 就壞）、
    SSE `text/event-stream`（被緩衝就不即時）一起處理 —— 所以自己寫一個。

    owner 平常從 cloudflared 遠端開帳，收支明細 3.53 MB → 0.31 MB（實測 11.5×）。
    """
    src = open("main.py", encoding="utf-8").read()
    assert "class GzipJsonMiddleware" in src
    assert "app.add_middleware(GzipJsonMiddleware)" in src
    fn = src[src.index("class GzipJsonMiddleware"):src.index("app.add_middleware(")]
    assert "application/json" in fn, "只認 JSON"
    # 不可換成 starlette 那個不分內容型別的（會連 ZIP／影片／SSE 一起壓）
    assert "from starlette.middleware.gzip" not in src
    assert "add_middleware(GZipMiddleware" not in src
    # 不宣告 gzip 的客戶端要原樣拿到
    assert 'b"gzip" not in accept' in fn
    # 超大串流要放棄壓縮而不是把記憶體吃爆
    assert "_CAP" in fn and "flush_plain" in fn
