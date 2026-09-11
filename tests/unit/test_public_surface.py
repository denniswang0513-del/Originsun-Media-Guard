"""NAS 對外容器的曝露面守衛（app 層，不是 per-module）。

對外容器（website-api）掛的東西有兩類，兩類都必須是**列舉出來的**：

  A. API：各模組自己的 `public_router`（CRM 的影像紀錄、提案的 token 頁）。
     低阻力的寫法是 `include_router(某模組.router)` —— 那一行會把整套內部
     端點（客戶/報價/成本/財務、提案清單/統計/成案）曝到對外服務上，而且
     沒有任何徵兆。
  B. 前端：公開頁與它們 import 的模組目錄（core.public_assets）。
     這裡的失敗模式更陰險 —— master 上一切正常，只有客戶那條路壞掉。

per-module 的守衛（test_media_log_public_router）擋得住「有人往那個
public_router 加端點」，但擋不住「有人在 main_website 多掛了一個 router」——
那是 app 層的事實，所以在這裡對**整個 app** 斷言一次。
"""
import os

import pytest

pytest.importorskip("sqlalchemy", reason="對外 app 需要 DB 套件")

from core.public_assets import (MODULE_DIRS, PAGES, SYNC_PATHS,  # noqa: E402
                                import_closure, is_served)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND = os.path.join(REPO, "frontend")
NGINX_CONF = os.path.join(REPO, "docker", "nginx", "originsun.conf")

# 對外容器允許出現的完整 API 路由集合。新增對外端點要同步改這裡，
# 等於強制經過一次「這真的該被**匿名**打到嗎」的思考。
EXPECTED_API = {
    # 影像紀錄（token 授權；同仁現場收照）
    ("/api/v1/crm/public/media-log/{token}", "GET"),
    ("/api/v1/crm/public/media-log/{token}/upload", "POST"),
    # 分塊上傳（>100MB 的檔會被 Cloudflare 擋在單一請求的 body 上限外）
    ("/api/v1/crm/public/media-log/{token}/upload/begin", "POST"),
    ("/api/v1/crm/public/media-log/{token}/upload/{upload_id}/chunk", "PUT"),
    ("/api/v1/crm/public/media-log/{token}/upload/{upload_id}/finish", "POST"),
    ("/api/v1/crm/public/media-log/{token}/file/{file_id}", "GET"),
    ("/api/v1/crm/public/media-log/{token}/file/{file_id}", "DELETE"),
    # 雜支登記（token 授權；現場外部人員沒有帳號，見 costs.py EXPENSE_LINK_SCOPE）
    # 讀的那兩支只回這條連結範圍內的東西；金額欄由 MoneyRedactRoute 抹掉
    # （匿名一律沒有 money_view → 看得到「哪個專案、哪一天」，看不到預算）。
    ("/api/v1/crm/public/expense/{token}", "GET"),
    ("/api/v1/crm/public/expense/{token}/expenses", "GET"),
    ("/api/v1/crm/public/expense/{token}/expenses", "POST"),
    ("/api/v1/crm/public/expense/{token}/receipts/{expense_id}", "POST"),
    # 報價單線上檢視（客戶手上的 /q/<短碼>；nginx 把短碼 rewrite 成這條）。
    # 憑證是網址裡的 share_token —— 後端逐字比對 DB、可撤銷；送的是寄出當下生成好的
    # 靜態快照（HTML 與 PDF 各一份），對外容器只是把檔案交出去，不重算金額也不重畫版面。
    ("/api/v1/crm/public/quote/{token}", "GET"),
    ("/api/v1/crm/public/quote/{token}/pdf", "GET"),
    # 發票影像分享（客戶／會計師手上的 /e/<短碼>；nginx 把 /e/ 整段轉進來，這條不必
    # rewrite —— 對外容器自己有 /e/{code} 路由）。該被匿名打到的理由同報價：憑證是網址
    # 裡的 share_token，逐字比對 DB、可撤銷；meta 只回「那張發票上本來就印著的欄位」
    # （docs/INVOICE_SHARE_PLAN.md §3.5 白名單），代開費／收款狀態／內部備註一律不上。
    # 匯款通知（收款人手上的 /p/<短碼>）。授權同報價與發票：憑證就是網址裡那串短碼，
    # 後端逐字比對 DB、可隨時撤銷；回的是 core.payout_share 的白名單投影（金額、日期、
    # 哪幾筆、誰匯的），連他自己的帳號與身分證都不上 —— 那條連結是可以被轉傳的。
    ("/api/v1/crm/public/payout/{token}", "GET"),
    ("/api/v1/crm/public/invoice-file/{token}/meta", "GET"),
    ("/api/v1/crm/public/invoice-file/{token}/download", "GET"),
    # 舊的長網址（已寄出的連結就是它）：維持「點了直接下載」，刻意不改成頁面
    ("/api/v1/crm/public/invoice-file/{token}", "GET"),
    # 提案公開共編頁（token 授權；客戶手上的 ?t= 連結）
    ("/api/v1/proposals/shared/{token}", "GET"),
    ("/api/v1/proposals/shared/{token}/deck", "GET"),
    ("/api/v1/proposals/shared/{token}/info", "PATCH"),
    ("/api/v1/proposals/shared/{token}/survey", "PATCH"),
    ("/api/v1/proposals/shared/{token}/cell", "PATCH"),
    ("/api/v1/proposals/shared/{token}/meta", "GET"),
    ("/api/v1/proposals/shared/{token}/refs", "POST"),
    ("/api/v1/proposals/shared/{token}/refs/{rid}", "PATCH"),
    ("/api/v1/proposals/shared/{token}/refs/{rid}", "DELETE"),
    ("/api/v1/proposals/shared/{token}/folder", "GET"),
    ("/api/v1/proposals/shared/{token}/folder/file", "GET"),
    # 單篇會議記錄唯讀分享（自己的 token；只出四欄，owner 2026-08-15 拍板）
    ("/api/v1/proposals/shared/meeting/{token}", "GET"),
    # 參考片研究頁（token 授權；提案公開頁的「研究頁 ↗」連過去）
    ("/api/v1/references/shared/{token}/{rid}", "GET"),
    ("/api/v1/references/shared/{token}/{rid}", "PATCH"),
    ("/api/v1/references/shared/{token}/{rid}/research/rows", "POST"),
    ("/api/v1/references/shared/{token}/{rid}/shots", "POST"),
    ("/api/v1/references/shared/{token}/{rid}/shots/{sid}", "PATCH"),
    ("/api/v1/references/shared/{token}/{rid}/shots/{sid}", "DELETE"),
}

# 對外容器可能出現的 API 命名空間（前綴之外的都不是我們在守的東西）
_WATCHED = ("/api/v1/crm", "/api/v1/proposals", "/api/v1/references")


def _app_api_routes():
    """對外 app 上所有受監視命名空間的路由。"""
    import main_website
    out = set()
    for r in main_website.app.routes:
        path = getattr(r, "path", "") or ""
        if not path.startswith(_WATCHED):
            continue
        for m in (getattr(r, "methods", None) or set()):
            if m not in ("HEAD", "OPTIONS"):
                out.add((path, m))
    return out


# ── A. API 曝露面 ─────────────────────────────────────────

def test_public_app_exposes_exactly_the_whitelist():
    assert _app_api_routes() == EXPECTED_API


def test_public_api_is_all_token_scoped():
    """對外端點一律吃 {token} —— 不帶 token 就是匿名可打，不該在這裡。"""
    for path, _m in EXPECTED_API:
        assert "{token}" in path, f"對外端點不帶 token：{path}"


def test_internal_domains_never_reachable():
    """點名幾個絕不可外洩的領域（讀起來就知道守的是什麼）。"""
    joined = " ".join(p for p, _ in _app_api_routes())
    for leaked in ("/clients", "/quotations", "/invoices", "/cash", "/staff",
                   "/payments", "/cost", "/convert", "/stats", "/plan/share",
                   "/archive", "/suggest"):
        assert leaked not in joined, f"對外 app 出現內部路徑：{leaked}"


def test_share_folder_is_read_only():
    """客戶只能看和下載 —— 分享夾出現寫入端點（上傳/刪除/改名）立刻紅。"""
    for path, method in EXPECTED_API:
        if "/folder" in path:
            assert method == "GET", f"分享資料夾出現寫入端點：{method} {path}"


# ── B. 前端資產 ───────────────────────────────────────────

def test_page_import_closure_stays_inside_served_dirs():
    """🔴 這條擋的是最陰險的那種改動：往公開頁的模組加一個 `../crm/…` import。
    master 上一切正常（整個 frontend/ 都在），NAS 上客戶那頁直接載入失敗。"""
    from core.public_assets import SOFT_DEPS
    for page in PAGES:
        for importer, dep in sorted(import_closure(FRONTEND, page)):
            if (importer, dep) in SOFT_DEPS:
                continue          # 動態 import + .catch，載不到只是少一個 toast
            assert is_served(dep), (
                f"{page} 的 {importer} 相依 {dep}，但對外容器只 serve "
                f"{MODULE_DIRS} —— 客戶那頁會在 NAS 上靜默載入失敗")


def test_every_page_and_module_dir_is_synced():
    """serve 得到但沒同步過去 = 永遠 503。"""
    for page in PAGES:
        assert f"frontend/{page}" in SYNC_PATHS
    for d in MODULE_DIRS:
        assert f"frontend/{d}" in SYNC_PATHS


def _nginx_reachable_prefixes(conf):
    """nginx 會轉進 website-api 的路徑前綴集合。

    兩種寫法都要算 —— 只認 `location ^~` 的話，用 rewrite 接進來的短網址
    （`/q/<短碼>` → `/api/v1/crm/public/quote/<短碼>`）在守衛眼裡等於不存在。
    rewrite 的替換字串取到第一個 `$` 之前那段（`$1` 是變動的短碼本身）。
    """
    import re as _re

    out = {m.group(1) for m in _re.finditer(r"location\s+\^~\s+(\S+)\s*\{", conf)}
    for target in _re.findall(r"rewrite\s+\S+\s+(\S+)", conf):
        head = target.split("$")[0]
        if head.startswith("/"):
            out.add(head)
    return out


# 掛在對外 app 上、但 nginx 刻意沒開路的前綴 —— 那一面在 NAS 上整個沒上線
# （雜支登記的 expense.html 也不在 PAGES 裡，客戶／製片走的是 master 那條）。
# 🔴 這裡不是「例外清單」而是待辦：要讓某一面 master 關機也能用，就補 location
# 並把它從這裡刪掉，不要反過來把新端點加進來繞過檢查。
_MASTER_ONLY_API_PREFIXES = ("/api/v1/crm/public/expense/",)


def test_nginx_has_a_location_for_everything_served():
    """nginx 沒開路徑的話，前面幾條都對也還是到不了容器 —— 而且只有客戶會走到。"""
    conf = open(NGINX_CONF, encoding="utf-8").read()
    for page in PAGES:
        assert f"location = /{page}" in conf, f"nginx 少了 /{page}"
    for d in MODULE_DIRS:
        assert f"location ^~ /{d}/" in conf, f"nginx 少了 /{d}/"

    reachable = _nginx_reachable_prefixes(conf)
    for path, _m in sorted(EXPECTED_API):
        if path.startswith(_MASTER_ONLY_API_PREFIXES):
            continue
        assert any(path.startswith(p) for p in reachable), (
            f"對外 app 有 {path}，但 nginx 沒有任何 location／rewrite 轉得到它 —— "
            f"master 關機時客戶那條連結會落到 location / 變成 404")


def test_nginx_maps_the_short_quote_link():
    """客戶手上的網址是 `/q/<短碼>`，而對外容器只有長 API —— 中間那段 rewrite
    只住在 nginx conf 裡，沒有任何 Python 端會因為它不見而紅。

    順帶釘住「不要在這裡碰快取」：報價是金錢文件，後端自己送 no-store，
    location 內一旦有 add_header 還會把 server 層三個安全標頭整組吃掉。"""
    import re as _re

    conf = open(NGINX_CONF, encoding="utf-8").read()
    block = _re.search(r"location\s+\^~\s+/q/\s*\{(.*?)\n\s*\}", conf, _re.S)
    assert block, "nginx conf 裡找不到 /q/ 短網址的 location（客戶的報價連結會 404）"
    body = block.group(1)
    assert _re.search(r"rewrite\s+\S+\s+/api/v1/crm/public/quote/", body), \
        "/q/ 沒有 rewrite 到報價的公開 API"
    assert "proxy_pass http://website-api:8001;" in body, "/q/ 沒有轉給 website-api"

    for name in ("/q/", "/api/v1/crm/public/quote/"):
        blk = _re.search(r"location\s+\^~\s+%s\s*\{(.*?)\n\s*\}" % _re.escape(name),
                         conf, _re.S)
        assert blk, f"nginx conf 裡找不到 {name} 的 location"
        assert "add_header" not in blk.group(1) and "expires" not in blk.group(1), \
            f"{name} 不該自己設快取／標頭（會蓋掉後端 no-store 並吃掉安全標頭）"


def test_nginx_media_log_body_cap_covers_backend_limit():
    """nginx 擋在後端前面 —— 它比 `_MAX_UPLOAD_BYTES` 小的話，區網直傳大檔會拿到
    一個 nginx 自己產的 413（不是我們的 JSON），錯誤訊息看不出是大小問題。

    ⚠ 這條釘的是**repo 裡的 conf**，不是 NAS 上正在跑的那份 —— 手改容器仍然
      可能讓生產與這裡不一致。它擋的是「有人改了後端常數卻忘了改 conf」。"""
    import re as _re

    from routers.crm.media_log import _MAX_UPLOAD_BYTES

    conf = open(NGINX_CONF, encoding="utf-8").read()
    block = _re.search(
        r"location\s+\^~\s+/api/v1/crm/public/media-log/?\s*\{(.*?)\n\s*\}",
        conf, _re.S)
    assert block, "nginx conf 裡找不到 media-log 的 location 區塊（改了寫法？）"
    m = _re.search(r"client_max_body_size\s+(\d+)([kmgKMG]?)", block.group(1))
    assert m, "media-log 的 location 沒有設 client_max_body_size"
    unit = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]
    assert int(m.group(1)) * unit >= _MAX_UPLOAD_BYTES, (
        f"nginx 上限 {m.group(0)} < 後端 _MAX_UPLOAD_BYTES "
        f"{_MAX_UPLOAD_BYTES // 1024 ** 2}MB")


def test_synced_pages_actually_exist():
    for page in PAGES:
        assert os.path.isfile(os.path.join(FRONTEND, page)), page
    for d in MODULE_DIRS:
        assert os.path.isdir(os.path.join(FRONTEND, *d.split("/"))), d


#: 寄出去的短網址**正本清單**：(前綴 -> NAS 上回哪個對外頁；None＝不回頁面)。
#:
#: 🔴 這份表是手寫的，而下面第一支測試會強制它跟 main.py 實際註冊的短網址完全
#:    對齊 —— 新增一條就一定要回來加一列。前一版是用 regex 去猜的，形狀稍有不同
#:    （/pay/{code}、/p/{token}、多帶一個 kwarg）就靜默跳過，一行都不紅。
SHORT_LINKS = {
    "/e/": "invoice-file.html",   # 發票影像（客戶／會計師）
    "/p/": "payout.html",         # 匯款通知（收款人）
    # /q/ 報價單：nginx 直接 rewrite 到公開 API 拿快照，NAS 那支 app **刻意沒有**
    # 這條路由。內容由 test_nginx_maps_the_short_quote_link 守。
    "/q/": None,
}


def _master_short_links():
    """main.py 上實際註冊的短網址前綴（單層 /x/{...} 形狀）。"""
    import re as _re
    master = open(os.path.join(REPO, "main.py"), encoding="utf-8").read()
    # `[^/}"]+` 而不是 `\w+`：`{code:path}` 這種帶轉換器的參數也要算進來
    # （`\w+` 遇到冒號就不匹配 → 那條路由靜默消失，正是這支要防的病）。
    # 前綴收大小寫與數字（`/e2/`）—— 寧可多抓一條逼人來這裡加一列。
    return {m.group(1) + "/" for m in
            _re.finditer(r'@app\.get\(\s*"(/[A-Za-z0-9]{1,4})/\{[^/}"]+\}[^"]*"', master)}


def test_the_short_link_table_lists_every_short_link_master_serves():
    """新增一條短網址 → 這份表也要加一列。

    表漏了誰，下面那兩支就完全看不到他 —— 而漏掉的症狀是「master 上正常、
    只有拿著連結的客戶 404」，我們這邊不會有任何徵兆。
    """
    assert _master_short_links() == set(SHORT_LINKS), (
        f"main.py 上的短網址是 {sorted(_master_short_links())}，"
        f"這支測試的 SHORT_LINKS 表寫的是 {sorted(SHORT_LINKS)} —— 對齊它們")


def test_short_links_exist_on_the_nas_app_too():
    """🔴 短網址是**兩支入口各定義一次**（master 的 main.py ＋ NAS 的
    main_website.py）。只加在 master 的話：nginx 轉得到 NAS、NAS 卻沒有那條路由
    → 從對外網域打開就是 404，而「master 關機他照樣打得開」正是做成連結的理由。

    2026-09-11 匯款通知 /p/ 就是這樣漏的 —— 那時 nginx 有 location、曝露面白名單
    也過了，唯獨 NAS 那支 app 沒有路由，發版當下才在 8090 上測出來。
    """
    nas = open(os.path.join(REPO, "main_website.py"), encoding="utf-8").read()
    for prefix, page in SHORT_LINKS.items():
        if page is None:
            continue
        assert page in PAGES, f"{prefix} 回的 {page} 不在對外頁清單裡（NAS serve 不到）"
        route = prefix.rstrip("/") + "/{code}"
        assert f'"{route}"' in nas, (
            f"{route} 只在 master 的 main.py 有 —— NAS 的 main_website.py 也要定義一條，"
            "不然從對外網域打開會 404（master 關機時那條連結就是死的）")


def test_nginx_has_a_location_for_every_short_link():
    """🔴 三邊的最後一邊。兩支 app 都有路由、白名單也過，nginx 少一條 location
    的話那條網址會落到 location /（對外站的 Astro dist）→ 客戶手上是 404，
    而全套測試綠、master 上也一切正常。

    2026-09-11 /polish 抓到：/e/ 與 /p/ 兩條當時都沒有任何測試守著（/q/ 有自己
    那支）—— 把整段 location 從 conf 拿掉，測試零失敗。
    """
    import re as _re
    conf = open(NGINX_CONF, encoding="utf-8").read()
    for prefix in SHORT_LINKS:
        assert _re.search(r"location\s+\^~\s+%s\s*\{" % _re.escape(prefix), conf), (
            f"nginx conf 裡找不到 {prefix} 的 location —— "
            "客戶手上那條連結會落到對外站的 dist/ 變成 404")
