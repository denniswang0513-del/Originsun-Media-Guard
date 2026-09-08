# -*- coding: utf-8 -*-
"""公開區（owner 2026-09-08）：對外免登入的功能集中一份登記表，owner 在使用者管理「公開區」分頁自己開關。

三種模式：
  off  ＝關閉：該面所有免登入端點回 404「此功能未開放」（頁面顯示同一句）
  link ＝連結：現況——憑證是網址裡的 token／短碼，後端逐字比對 DB、可撤銷。owner：「正常來說都是連結公開的」→ 預設全部 link
  open ＝公開：連 token 都不用；只有履歷（員工檔勾 resume_visible）與員工註冊兩面支援，因為它們本來就沒有 token

守衛只有一處：`surface_gate` 用路徑前綴對回登記表的鍵，查模式；關閉才擋，其餘放行到各端點原本的 token 檢查。
設定存 settings.json `public_access = {鍵: 模式}`（每次請求讀 load_settings 的快取，改了立即生效）。
純規則在這裡（登記表、normalize、mode_of、surface_for_path）；讀設定與丟 HTTPException 的 `surface_gate` 也放這裡，routers 只掛。
"""
from typing import Dict, List, Optional

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
    return normalize(cfg).get(key, MODE_LINK)


def surface_for_path(path: str) -> Optional[str]:
    """request.url.path → 面的鍵；沒對到＝不是公開區的端點（守衛不管）。"""
    for s in PUBLIC_SURFACES:
        for pre in s["prefixes"]:
            if path.startswith(pre):
                return s["key"]
    return None


def surface_gate(request: Request) -> Optional[str]:
    """掛在 public_router／token_router／portal router 的 Depends，或在 /q/、/register 手動呼叫。
    關閉 → 404「此功能未開放」（不是 403：對外面的人不該暴露「有這功能但你不能用」）。回面的鍵或 None。"""
    key = surface_for_path(request.url.path)
    if key is None:
        return None
    from config import load_settings
    if mode_of(key, load_settings().get("public_access")) == MODE_OFF:
        raise HTTPException(status_code=404, detail="此功能未開放")
    return key
