# -*- coding: utf-8 -*-
"""公開區（owner 2026-09-08）：對外免登入的功能集中一份登記表，owner 在使用者管理「公開區」分頁自己開關。

三種模式：
  off  ＝關閉：該面所有免登入端點回 404「此功能未開放」（頁面顯示同一句）
  link ＝連結：現況——憑證是網址裡的 token／短碼，後端逐字比對 DB、可撤銷。owner：「正常來說都是連結公開的」→ 預設全部 link
  open ＝公開：連 token 都不用；只有履歷（員工檔勾 resume_visible）與員工註冊兩面支援，因為它們本來就沒有 token

守衛只有一處：`surface_gate` 用路徑前綴對回登記表的鍵，查模式；關閉才擋，其餘放行到各端點原本的 token 檢查。

🔴 設定的單一真相＝**共用 Postgres**（`website_settings` 的 `public_access` 這一筆，值是 {鍵: 模式}）。
   同一批 public_router **兩台機器都在跑**：master（8000）與 NAS 對外容器（main_website.py 掛
   crm public_router／proposals／references）。對外連結（影像紀錄 QR、雜支連結、提案分享）指的是
   NAS 那台，而它有自己的 settings.json（publish 的 NAS_SYNC_CODE 不同步 settings.json）——
   設定各存各的＝owner 在畫面上關掉，對外那面其實還開著，而且不會報錯。
   舊的 settings.json `public_access` 只當降級來源，第一次讀到就搬進 DB（一次性遷移）。

讀取順序：DB → 讀不到（DB 不可用／沒那筆）→ settings.json → 登記表預設值。
**DB 掛掉不擋人**：回預設（link／open），不因為資料庫離線把對外頁全部 404。
每個公開請求都會走這條，所以模組層 TTL 快取（`_CACHE_TTL`）；master 存檔時 `invalidate()` 立即失效，
NAS 容器收不到寫入事件，靠 TTL 自己追上（形狀對齊 routers/crm/media_log 的 `_conf_cache`）。

純規則在這裡（登記表、normalize、mode_of、surface_for_path）；讀寫設定與丟 HTTPException 的
`surface_gate`／`current_modes`／`save_modes` 也放這裡，routers 只掛。
"""
import time
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request  # type: ignore

MODE_OFF, MODE_LINK, MODE_OPEN = "off", "link", "open"
MODE_LABELS = {MODE_OFF: "關閉", MODE_LINK: "連結", MODE_OPEN: "公開"}

# 每一面：鍵、名稱、誰在用、怎麼進（給管理員看的網址樣式）、頁面、路徑前綴（request.url.path 開頭）、支援模式、預設
PUBLIC_SURFACES: List[dict] = [
    {"key": "expense", "label": "雜支登記", "who": "外部製片、臨時人員",
     "how": "/expense.html?t=<連結>（每個專案、子表、預支款各一條，可撤銷；?project= 是內部路，要登入）",
     "pages": ["expense.html", "group-expense.html", "advance-expense.html"],
     "prefixes": ["/api/v1/crm/public/expense/"], "modes": [MODE_OFF, MODE_LINK], "default": MODE_LINK},
    {"key": "media_log", "label": "影像紀錄上傳牆", "who": "劇組成員手機",
     "how": "/media-log.html?token=<連結>（每個專案一條，專案頁產 QR）",
     "pages": ["media-log.html"], "prefixes": ["/api/v1/crm/public/media-log/"], "modes": [MODE_OFF, MODE_LINK], "default": MODE_LINK},
    {"key": "portal", "label": "客戶看片審批", "who": "客戶",
     "how": "/review.html?t=<連結>（審批門戶分頁建的看片連結）",
     "pages": ["review.html"], "prefixes": ["/api/v1/portal/public/"], "modes": [MODE_OFF, MODE_LINK], "default": MODE_LINK},
    {"key": "quote", "label": "報價單線上檢視", "who": "客戶",
     "how": "/q/<短碼>（報價單按「分享」鑄的）",
     "pages": [], "prefixes": ["/q/", "/api/v1/crm/public/quote/"], "modes": [MODE_OFF, MODE_LINK], "default": MODE_LINK},
    {"key": "invoice_file", "label": "發票影像分享", "who": "客戶、會計",
     "how": "/e/<短碼>（發票列按「分享」鑄的）",
     "pages": [], "prefixes": ["/e/", "/api/v1/crm/public/invoice-file/"], "modes": [MODE_OFF, MODE_LINK], "default": MODE_LINK},
    {"key": "proposal_share", "label": "提案企劃分享（含會議記錄、片庫分享）", "who": "客戶、外部顧問",
     "how": "/project.html?t=<連結>、/meeting-note.html?t=<連結>（可寫）",
     "pages": ["project.html", "meeting-note.html", "reference.html"], "prefixes": ["/api/v1/proposals/shared/", "/api/v1/references/shared/"], "modes": [MODE_OFF, MODE_LINK], "default": MODE_LINK},
    {"key": "showcase_edit", "label": "結案上架編輯", "who": "外部剪接、企劃",
     "how": "/showcase-edit.html?token=<編輯 token>（完稿結案分頁「編輯連結」）",
     "pages": ["showcase-edit.html"], "prefixes": ["/api/v1/crm/public/showcase-edit/"], "modes": [MODE_OFF, MODE_LINK], "default": MODE_LINK},
    {"key": "staff_edit", "label": "員工自助編輯履歷", "who": "員工本人",
     "how": "/staff-edit.html?token=<編輯 token>（員工檔案「發履歷編輯連結」）",
     "pages": ["staff-edit.html"], "prefixes": ["/api/v1/crm/public/staff-edit/"], "modes": [MODE_OFF, MODE_LINK], "default": MODE_LINK},
    {"key": "resume", "label": "公開履歷", "who": "對外（客戶、合作方）",
     "how": "/resume.html?staff=<人員 id>（員工檔勾「履歷公開」才看得到，沒有 token）",
     "pages": ["resume.html"], "prefixes": ["/api/v1/crm/public/staff/"], "modes": [MODE_OFF, MODE_OPEN], "default": MODE_OPEN},
    {"key": "register", "label": "員工自助註冊", "who": "新同事",
     "how": "/my.html 的「註冊」（兩題公司知識驗證；建好只有基本資料）",
     "pages": ["my.html"], "prefixes": ["/api/v1/auth/register"], "modes": [MODE_OFF, MODE_OPEN], "default": MODE_OPEN},
]
SURFACE_KEYS = tuple(s["key"] for s in PUBLIC_SURFACES)


def normalize(cfg: Optional[dict]) -> Dict[str, str]:
    """只留登記表裡的鍵、模式要在那一面支援的清單裡；缺的／壞的用預設。"""
    src = cfg if isinstance(cfg, dict) else {}
    out = {}
    for s in PUBLIC_SURFACES:
        v = src.get(s["key"])
        out[s["key"]] = v if isinstance(v, str) and v in s["modes"] else s["default"]
    return out


def mode_of(key: str, cfg: Optional[dict]) -> str:
    return normalize(cfg)[key]


def surface_for_path(path: str) -> Optional[str]:
    """request.url.path → 面的鍵；沒對到＝不是公開區的端點（守衛不管）。"""
    for s in PUBLIC_SURFACES:
        for pre in s["prefixes"]:
            if path.startswith(pre):
                return s["key"]
    return None


# ── 設定的讀寫（DB 正本 ＋ TTL 快取）────────────────────────────────
SETTING_KEY = "public_access"      # website_settings 的鍵（value＝JSONB {面: 模式}）
_CACHE_TTL = 25.0                  # 秒；NAS 容器靠它追上 master 的變更
_cache: dict = {"at": 0.0, "val": None}


def invalidate() -> None:
    """寫入端（PUT /auth/public-access）呼叫 —— 本行程立刻拿到新值。
    ⚠ 別的行程（NAS 容器、機隊）最多還會用舊值 `_CACHE_TTL` 秒。"""
    _cache["at"] = 0.0
    _cache["val"] = None


async def _db_modes() -> Tuple[bool, Optional[dict]]:
    """(DB 讀得到嗎, 那一筆的原始值)。讀不到一律 (False, None) —— 不 raise，公開頁不能因 DB 掛掉全掛。"""
    try:
        import core.state as state
        if not getattr(state, "db_online", False):
            return False, None
        from db.session import get_session_factory
        factory = get_session_factory()
        if not factory:
            return False, None
        from services.website import settings_service
        async with factory() as session:
            vals = await settings_service.get_prefixed(session, SETTING_KEY)
        raw = vals.get(SETTING_KEY)
        return True, (raw if isinstance(raw, dict) else None)
    except Exception:
        return False, None


def _file_modes() -> Optional[dict]:
    """舊正本 settings.json 的 `public_access`（只當降級來源與一次性遷移的材料）。"""
    try:
        from config import load_settings
        raw = load_settings().get(SETTING_KEY)
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


async def _migrate_file_to_db(modes: Dict[str, str]) -> None:
    """DB 沒那筆、settings.json 有 → 搬進去（best-effort，失敗就算了，下次再試）。"""
    try:
        from db.session import get_session_factory
        factory = get_session_factory()
        if not factory:
            return
        from services.website import settings_service
        async with factory() as session:
            await settings_service.update_settings(
                session, {SETTING_KEY: modes}, updated_by="migrate:settings.json")
    except Exception:
        pass


async def current_modes() -> Dict[str, str]:
    """目前每一面的模式（已 normalize）。DB → settings.json → 預設；TTL 快取。"""
    now = time.monotonic()
    if _cache["val"] is not None and now - _cache["at"] < _CACHE_TTL:
        return dict(_cache["val"])      # 複本：呼叫端改到手上這份不該污染守衛
    reachable, raw = await _db_modes()
    if raw is None:
        legacy = _file_modes()
        if legacy is not None:
            raw = legacy
            if reachable:                       # DB 活著但沒那筆 → 一次性遷移
                await _migrate_file_to_db(normalize(legacy))
    modes = normalize(raw)
    _cache["val"] = modes
    _cache["at"] = now
    return dict(modes)


async def save_modes(modes: Optional[dict], updated_by: Optional[str] = None) -> Dict[str, str]:
    """管理員存檔：normalize → 寫 DB（唯一正本，不再寫 settings.json）→ 本行程快取失效。
    DB 不可用時 503 —— 寫入不能靜默成功（不然畫面說存好了，其實什麼都沒關掉）。"""
    clean = normalize(modes)
    from core.db_guard import db_factory_or_503
    factory = db_factory_or_503()
    from services.website import settings_service
    async with factory() as session:
        await settings_service.update_settings(session, {SETTING_KEY: clean}, updated_by=updated_by)
    invalidate()
    return clean


async def surface_gate(request: Request) -> Optional[str]:
    """掛在 public_router／token_router／portal router 的 Depends，或在 /q/、/e/、/register 手動 `await`。
    關閉 → 404「此功能未開放」（不是 403：對外面的人不該暴露「有這功能但你不能用」）。回面的鍵或 None。"""
    key = surface_for_path(request.url.path)
    if key is None:
        return None
    if (await current_modes())[key] == MODE_OFF:
        raise HTTPException(status_code=404, detail="此功能未開放")
    return key
