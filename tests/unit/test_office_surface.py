"""NAS `office-api` 容器的曝露面守衛（app 層，不是 per-router）。

office-api（`main_office.py`，NAS 8002）是「master Windows 關機時，內部同仁的報價／
發票／員工工作台照樣能用」的那一份。它跟 master 連**同一顆生產 Postgres**、共用同一把
jwt_secret，所以它多掛一樣東西，代價都是實打實的：

  1. 多掛一個**排程／背景 runner** → 夜間批次、告警探測、對外寄信在 NAS 再跑一份
     ＝重複觸發（同一顆 DB、同一組 webhook，兩邊都以為自己是唯一的那個）。
  2. 多掛一個**吃硬體的 router** → 那些端點要記憶卡、ffmpeg.exe、本機磁碟；在容器裡
     它們不是「不能用」而是「用了會壞在很奇怪的地方」（路徑存在但不是同一顆磁碟）。
  3. 少 serve 一支前端相依 → master 上一切正常，**只有 NAS 上那頁白畫面**。這種故障
     最貴：自己人平常都走 master，發現不了。

守衛的分工（跟對外容器那組平行）：

    tests/unit/test_public_surface.py        對外容器 website-api 的 app 層曝露面
    tests/unit/test_media_log_public_router.py  per-router 白名單（R1 防線）
    本檔                                     內部容器 office-api 的 app 層曝露面
                                             ＋ 兩個容器互不污染

最後一條（`test_website_app_has_no_office_endpoints`）是整組裡最重要的：website-api
服務的是 `www` 的**匿名**流量，office 那些端點漏過去就是內部資料直接對全網開著。
"""
import os
import re
import subprocess
import sys

import pytest

pytest.importorskip("sqlalchemy", reason="office app 需要 DB 套件")

import core.office_assets as office_assets  # noqa: E402
from core.office_assets import (MODULE_DIRS, PAGES, SYNC_PATHS,  # noqa: E402
                                is_served)
from core.public_assets import import_closure  # noqa: E402
from tests.unit._srcscan import code_only, func_body, repo_src  # noqa: E402

# 逐檔白名單（住在頁籤資料夾裡、但 office 的頁靜態 import 得到的共用庫）。
# 用 getattr 取：取不到時退成空 tuple，讓「它被拿掉」由相依閉包那兩條去紅
# （而不是在這裡先 ImportError，把整個檔案的 16 條斷言一起炸掉）。
MODULE_FILES = getattr(office_assets, "MODULE_FILES", ())

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND = os.path.join(REPO, "frontend")
NGINX_CONF = os.path.join(REPO, "docker", "nginx", "originsun.conf")


def _office_paths():
    """office app 上所有路由的路徑集合。"""
    import main_office
    return {getattr(r, "path", "") or "" for r in main_office.app.routes}


# ── 1. 模組圖：不准長出排程／背景 runner ──────────────────────

# 只要**被 import** 就算違規的模組。理由分別是：
#   core.scheduler   croniter 迴圈：排程在兩台各跑一次 ＝ 同一個備份／報表任務被觸發兩次
#   croniter         上面那支唯一的外部依賴；它出現在模組圖裡就代表有人繞路把排程接回來了
#   core.socket_mgr  Socket.IO 單例；它一被 import 就開一個 AsyncServer
#   socketio         同上（有人直接用套件而不經過我們的單例）
#   services.intel_runner / services.social_runner
#                    夜間 AI 批次：兩台各抓一次 RSS、各寫一次 DB、各發一次通知
#   core.watchdog_service / notifier
#                    告警探測與對外寄信：同一次事故會寄兩封，而且冷卻各算各的
_FORBIDDEN_MODULES = (
    "core.scheduler", "croniter",
    "core.socket_mgr", "socketio",
    "services.intel_runner", "services.social_runner",
    "core.watchdog_service", "notifier",
)


def test_office_module_graph_has_no_schedulers_or_runners():
    """在**乾淨的 subprocess** 裡 import main_office，檢查 sys.modules。

    為什麼要開 subprocess：pytest 這個行程早就被別的測試 import 過半個 repo 了
    （`core.scheduler` 幾乎一定已經在 sys.modules 裡），在同一個行程裡問這個問題
    只會拿到別人的答案。**這條測試唯一有意義的觀測點是「乾淨行程」**。

    漏了會怎樣：有人為了「順便讓 NAS 也能跑排程」在 main_office 加一行 import，
    夜間批次就在兩台各跑一次 —— 同一顆 DB、同一組 webhook，兩邊都以為自己是唯一
    的那個。症狀是同事收到兩份一模一樣的通知，而 log 兩邊都看起來正常。
    """
    code = (
        "import main_office, sys, json;"
        "print(json.dumps(sorted(m for m in sys.modules if m in %r)))"
        % (_FORBIDDEN_MODULES,)
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, (
        f"乾淨行程 import main_office 就失敗了：\n{proc.stderr[-3000:]}")
    leaked = proc.stdout.strip().splitlines()[-1]
    assert leaked == "[]", (
        f"main_office 的模組圖裡出現排程／背景 runner：{leaked}\n"
        f"全機隊共用同一顆生產 DB，這些東西只該在 master 跑一份。")


# ── 2. 吃硬體的東西一條都不准掛 ───────────────────────────────

# 這些路徑段落背後都是**本機硬體**：記憶卡、ffmpeg.exe、本機磁碟、機隊 OTA。
# 用「路徑段落完全相等」比對而不是子字串 —— `/api/v1/crm/quotations` 裡面就有
# `ota`（q-u-`ota`-t-i-o-n-s），拿 `"ota" in path` 掃會把整個報價模組誤判成 OTA。
_HARDWARE_SEGMENTS = {
    "jobs",             # 備份／驗證／轉檔／串接／語音全都掛在 /api/v1/jobs*
    "backup", "verify", "transcode", "concat", "transcribe",
    "report_jobs", "reports",        # 報表要 ffmpeg 抽膠卷 + 本機輸出目錄
    "tts", "tts_jobs", "voice_profiles",   # F5-TTS 要 GPU 與 1.2GB 模型
    "drone", "drone_meta",           # 空拍寫入：掃本機來源根目錄
    "agents", "ota",                 # 機隊管理與 OTA 推送（只有 master 是發版來源）
    "queue", "schedules", "bookmarks",     # 佇列／cron 排程／書籤（都綁本機任務）
    "control", "models", "job_history",    # 暫停/停止/更新、Whisper 模型、任務 log 檔
    "list_dir", "utils",             # 檔案總管／拖放解析／彈系統選擇器（本機才有意義）
}


def test_office_app_mounts_nothing_hardware_bound():
    """漏了會怎樣：容器裡沒有記憶卡、沒有 T:/S:/V: 對映磁碟、也沒有 ffmpeg.exe。
    這些端點掛上去不會「明顯壞掉」——它們會**收下任務**然後失敗在很深的地方
    （路徑不存在／ffmpeg 找不到），而使用者只看到一個排到一半就死掉的任務。
    OTA 那組更糟：NAS 這台的碼可能比 master 舊一版，讓它去推機隊等於發版倒退。
    """
    hits = []
    for path in sorted(_office_paths()):
        for seg in path.strip("/").split("/"):
            if seg in _HARDWARE_SEGMENTS:
                hits.append((path, seg))
    assert not hits, (
        "office-api 掛到了吃硬體的路由（那些只該在 master 8000）：\n"
        + "\n".join(f"  {p}  ←  段落 {s!r}" for p, s in hits))


def test_office_router_list_is_the_only_way_things_get_mounted():
    """`_ROUTER_MODULES` 是一份看得見的清單 —— 掛 router 只准經過它。

    漏了會怎樣：有人在檔尾補一句 `app.include_router(api_backup.router)`，上面那條
    段落比對擋得住已知的名字，但擋不住**新開**的硬體 router。清單化之後，「這台要掛
    什麼」永遠是一個 code review 看得到的 diff。
    """
    src = code_only(repo_src("main_office.py"))
    # 只有 _mount() 內部那一句可以呼叫 include_router
    assert src.count("include_router") == 1, (
        "main_office 出現第二個 include_router —— 掛載一律走 _ROUTER_MODULES + _mount()")
    assert "kept" in func_body(src, "def _mount("), "_mount 的實作被換掉了？"


# ── 3. 帳號管理留在 master ────────────────────────────────────

def test_account_admin_endpoints_stay_on_master():
    """漏了會怎樣：使用者資料是 Postgres ＋ users.json **雙寫**的，而 NAS 這台沒有
    JSON 正本。在這裡改權限只寫得進 DB，master 的 JSON 鏡射就跟著漂 —— 平常沒事
    （JSON 只是 DB 掛掉時的退路），但那正好是最需要它準的時候。身份範本同理，它存在
    settings.json，這台也沒有那個檔。
    """
    leaked = sorted(p for p in _office_paths()
                    if p.startswith(("/api/v1/auth/users",
                                     "/api/v1/auth/rbac/templates")))
    assert not leaked, (
        f"office-api 出現帳號／範本管理端點：{leaked}\n"
        f"它們要留在 master（見 main_office._DROP_PREFIXES 的註解）")


def test_login_path_is_still_open_on_office():
    """反面：不能為了上面那條把人擋在門外。登入／換 token／我是誰／Google 首登
    是同仁天天要走的路，master 關機時它們**必須**在這台可用 —— 否則整個容器等於沒開。
    """
    paths = _office_paths()
    for needed in ("/api/v1/auth/login", "/api/v1/auth/refresh",
                   "/api/v1/auth/me", "/api/v1/auth/google/login"):
        assert needed in paths, f"office-api 少了 {needed} —— 同仁登不進來"


# ── 4. `_mount` 是「組一個新 router」不是「掛完再刪」 ──────────

def test_mount_builds_a_new_router_instead_of_deleting_routes():
    """漏了會怎樣：改成「先 include_router 全掛上，再從 `app.router.routes` 刪掉
    不要的」有兩個沉默的失敗模式 ——
      (a) 刪除要靠**順序**：邊走邊 remove 會跳過元素，某幾條就這樣留在 app 上；
      (b) 刪錯了**沒有任何徵兆**：app 照常起得來，只是多／少幾條路由，
          而「多一條」正是這整個檔案在防的事。
    組一個新的 APIRouter 則讓「哪些沒掛」變成一份讀得到的清單。
    """
    body = code_only(func_body(repo_src("main_office.py"), "def _mount("))
    assert "APIRouter()" in body, "_mount 不再自己組 router 了？"
    for forbidden in ("app.router.routes", ".remove(", "del app", "pop("):
        assert forbidden not in body, (
            f"_mount 出現刪除法的痕跡（{forbidden}）—— 排除要用「組一個新 router」，"
            f"刪除法靠順序而且刪錯沒徵兆")


def test_drop_prefixes_actually_drops_something():
    """漏了會怎樣：`_DROP_PREFIXES` 打錯字（少一個斜線、改了路徑）時，它會靜靜地
    一條都比不中 —— 排除照樣「成功」，帳號管理端點就整組上線了。所以除了斷言那些路徑
    不在 app 上（上面那條），也要確認它們**在來源 router 上真的存在**。
    """
    from routers.api_auth import router as auth_router
    from main_office import _DROP_PREFIXES

    src_paths = {getattr(r, "path", "") or "" for r in auth_router.routes}
    for pref in _DROP_PREFIXES:
        assert any(p.startswith(pref) for p in src_paths), (
            f"_DROP_PREFIXES 的 {pref!r} 在 api_auth 上一條都比不中 —— "
            f"路徑改過了？這個排除等於沒生效")


# ── 5. 前端相依閉包 ───────────────────────────────────────────

def _closure_violations(rel):
    return sorted((imp, dep) for imp, dep in import_closure(FRONTEND, rel)
                  if not is_served(dep))


def test_page_import_closure_stays_inside_served_dirs():
    """🔴 最陰險的那種改動：往 office 的頁加一個 `../crm/…` import。

    漏了會怎樣：master 上一切正常（整個 frontend/ 都在），NAS 上那頁**白畫面** ——
    ES module 的相依鏈只要斷一環，整個 module script 就不執行，而且畫面上什麼都不說。
    自己人平常走 master，這種故障要等到 master 真的關機那天才會被發現。
    """
    bad = {}
    for page in PAGES:
        v = _closure_violations(page)
        if v:
            bad[page] = v
    assert not bad, (
        "office 頁面的相依跑出 MODULE_DIRS 之外（office-api 只 serve "
        f"{MODULE_DIRS}）：\n"
        + "\n".join(f"  {page}: {imp} → {dep}"
                    for page, vs in bad.items() for imp, dep in vs))


def test_served_module_import_closure_stays_inside_served_dirs():
    """MODULE_DIRS 是**整個目錄**開出去的，所以裡面每一支 .js 都可能被載到。

    漏了會怎樣：同上（NAS 白畫面），只是入口從「頁面」換成「頁面之後某天才 import
    到的那支共用模組」。這條比上面那條嚴格，擋的是「現在還沒人 import、但已經埋在
    served 目錄裡」的地雷 —— 等有人 import 它的那天，紅的會是別人的 PR。
    """
    bad = {}
    for f in MODULE_FILES:
        v = _closure_violations(f)
        if v:
            bad[f] = v
    for d in MODULE_DIRS:
        root_dir = os.path.join(FRONTEND, *d.split("/"))
        for root, _dirs, files in os.walk(root_dir):
            for f in sorted(files):
                if not f.endswith(".js"):
                    continue
                rel = os.path.relpath(os.path.join(root, f),
                                      FRONTEND).replace(os.sep, "/")
                v = _closure_violations(rel)
                if v:
                    bad[rel] = v
    assert not bad, (
        "被 serve 的模組相依到沒 serve 的檔（office-api 只 serve "
        f"{MODULE_DIRS}）：\n"
        + "\n".join(f"  {rel}: {imp} → {dep}"
                    for rel, vs in bad.items() for imp, dep in vs))


def test_office_pages_and_dirs_actually_exist():
    """清單裡寫了但 repo 裡沒有的項目 —— serve 出去永遠 503／404，
    而且 `main_office` 只會在啟動 log 印一行 warning，沒有人在看。"""
    for page in PAGES:
        assert os.path.isfile(os.path.join(FRONTEND, page)), page
    for d in MODULE_DIRS:
        assert os.path.isdir(os.path.join(FRONTEND, *d.split("/"))), d
    for f in MODULE_FILES:
        assert os.path.isfile(os.path.join(FRONTEND, *f.split("/"))), f


# ── 6. 三處對齊：清單 / 同步 / nginx ──────────────────────────

def test_every_page_and_module_dir_is_synced():
    """漏了會怎樣：serve 得到但沒同步過去 ＝ `_serve_page` 永遠回 503
    （`main_office` 刻意用 503 不是 404，就是為了讓「沒推過來」看得出來）。"""
    for page in PAGES:
        assert f"frontend/{page}" in SYNC_PATHS, f"SYNC_PATHS 少了 frontend/{page}"
    for d in MODULE_DIRS:
        assert f"frontend/{d}" in SYNC_PATHS, f"SYNC_PATHS 少了 frontend/{d}"
    # 🔴 逐檔白名單最容易漏這一步：`is_served()` 說得出「它該被 serve」、main_office
    # 也真的掛了路由，但檔案沒被 scp 過去 —— NAS 上就是 404，而 master 上完全正常。
    for f in MODULE_FILES:
        assert f"frontend/{f}" in SYNC_PATHS, f"SYNC_PATHS 少了 frontend/{f}"


def _server_blocks(conf: str):
    """把 conf 切成一個個 `server { … }` 區塊（配對大括號，location 巢狀也吃得住）。"""
    out = []
    for m in re.finditer(r"\bserver\s*\{", conf):
        depth, i = 0, m.end() - 1
        while i < len(conf):
            if conf[i] == "{":
                depth += 1
            elif conf[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append(conf[m.end():i])
    return out


def _office_server_block(conf: str):
    for blk in _server_blocks(conf):
        if re.search(r"server_name[^;]*\boffice\.originsun-studio\.com\b", blk):
            return blk
    return None


def test_nginx_has_an_office_server_block():
    """漏了會怎樣：`office.originsun-studio.com` 沒有自己的 server 區塊時，會落到
    `listen 80 default_server; server_name www… _;` 那個 catch-all —— 也就是被
    當成官網來服務：`/my.html` 去 Astro 的 dist/ 找檔案，回 404（而不是到 8002）。
    容器跑得好好的，網址就是打不開。
    """
    conf = open(NGINX_CONF, encoding="utf-8").read()
    assert _office_server_block(conf) is not None, (
        "docker/nginx/originsun.conf 裡找不到 office.originsun-studio.com 的 "
        "server 區塊 —— 流量會落到 default_server（官網）變成 404")


def _office_proxy_prefixes(blk: str):
    """office server 區塊裡「會轉進 office-api:8002」的路徑前綴。

    `location /` 這種 catch-all 也算（office-api 自己同時 serve 頁面與 API，
    最可能的寫法就是整台反代過去）—— 只認 `^~` 的話會把正確的設定判成錯的。
    """
    out = set()
    for m in re.finditer(r"location\s+(?:\^~\s+|=\s+)?(\S+)\s*\{", blk):
        path, i = m.group(1), m.end() - 1
        depth = 0
        while i < len(blk):
            if blk[i] == "{":
                depth += 1
            elif blk[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if re.search(r"proxy_pass\s+https?://office-api:8002", blk[m.end():i]):
            out.add(path)
    return out


def test_nginx_routes_every_office_asset_to_the_container():
    """漏了會怎樣：頁在容器裡、API 也在容器裡，但 nginx 沒開那條路徑 —— 同仁拿到的
    是 404 或官網首頁。`/js/shared/` 這種只少一條的情況更難查：頁面本身開得起來，
    只有它 import 的模組 404，畫面白掉。
    """
    conf = open(NGINX_CONF, encoding="utf-8").read()
    blk = _office_server_block(conf)
    assert blk is not None, "先修 test_nginx_has_an_office_server_block"
    reachable = _office_proxy_prefixes(blk)

    wanted = (["/" + p for p in PAGES]
              + ["/" + d + "/" for d in MODULE_DIRS]
              + ["/" + f for f in MODULE_FILES]
              + ["/api/v1/", "/healthz"])
    missing = [w for w in wanted
               if not any(w.startswith(p) or p == "/" for p in reachable)]
    assert not missing, (
        f"office server 區塊沒有把這些轉給 office-api:8002：{missing}\n"
        f"（區塊裡轉得到的前綴：{sorted(reachable)}）")


def test_office_endpoints_are_not_reachable_from_the_public_www_server():
    """漏了會怎樣：把 office 的 location 加進 `www` 那個 server 區塊很省事，
    但那個 server 服務的是**匿名**流量 —— 等於把內部工作台掛到官網上。
    授權還在（後端仍要 token），但整面攻擊面就這樣多了一份，而且 Cloudflare 對
    www 的 bot 對抗層會間歇擋掉帶 Authorization 的請求（見 CLAUDE.md 檔頭）。
    """
    conf = open(NGINX_CONF, encoding="utf-8").read()
    for blk in _server_blocks(conf):
        if re.search(r"server_name[^;]*\boffice\.originsun-studio\.com\b", blk):
            continue
        assert "office-api" not in blk, (
            "office-api 出現在非 office 的 server 區塊裡 —— "
            "內部工作台不可以掛在 www（匿名）那台上")


# ── 7. 兩個容器的曝露面不可互相污染（最重要的一條）──────────

# office 才有的內部命名空間。website-api 服務的是 www 的**匿名**流量，
# 這些東西漏過去就是內部資料對全網開著。
_OFFICE_ONLY_PREFIXES = (
    "/api/v1/me/",           # 員工工作台（我的今天／這週／專案查詢）
    "/api/v1/timesheets/",   # 工時（全公司每個人做了什麼、幾小時）
    "/api/v1/hr/",           # 人事／假勤
    "/api/v1/journal/",      # 週記
    "/api/v1/milestones",    # 每週專案里程碑
    "/api/v1/auth/login",    # 登入端點（對外容器不該是登入入口）
    "/api/v1/crm/clients",   # 客戶名冊
    "/api/v1/crm/quotations",
    "/api/v1/crm/invoices",
    "/api/v1/crm/cash",
    "/api/v1/crm/staff",
    "/api/v1/paste_upload",  # 貼圖上傳（匿名可寫檔＝圖床被當免費空間）
)


def test_website_app_has_no_office_endpoints():
    """🔴 這是整個檔案裡最重要的一條。

    漏了會怎樣：website-api 那個容器掛在 `www.originsun-studio.com` 後面，服務的是
    **沒有帳號的訪客**。office 的任何一條漏進去，客戶名冊／報價／發票／全公司工時就
    直接對全網開著 —— 而且 master 上完全看不出來（那些端點在 master 本來就該在）。
    最可能的來源是「兩支入口都要掛 CRM，複製貼上一行 include_router」。
    """
    import main_website
    paths = {getattr(r, "path", "") or "" for r in main_website.app.routes}
    leaked = sorted(p for p in paths if p.startswith(_OFFICE_ONLY_PREFIXES))
    assert not leaked, (
        f"對外容器（www 匿名流量）出現 office 的內部端點：{leaked}")


def test_office_app_has_the_endpoints_website_must_not():
    """反面對照：上面那條要有意義，這些端點得**真的**存在於 office 上。

    漏了會怎樣：office 那邊哪天被拿掉了（改名、router 沒掛成功），上面那條會變成
    永遠綠的空斷言 —— 一個測不到東西的守衛比沒有守衛更糟，因為它看起來有在守。
    """
    paths = _office_paths()
    for pref in _OFFICE_ONLY_PREFIXES:
        assert any(p.startswith(pref) for p in paths), (
            f"office-api 上找不到 {pref} —— router 沒掛成功？"
            f"（main_office 對掛載失敗只印 warning，不會讓容器起不來）")
